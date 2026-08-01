# 배포 가이드 — bootstrap + rollout

본 문서는 내부망 VM 1 대(단일 prod)에 엔진을 docker compose 매체로 배포·rollout 하는 단계별 가이드.

배포 매체는 docker compose 단일. 엔진 rollout 은 배포 대상 VM 에서 `deploy.sh` 를 실행해 수행한다(시퀀스는 3절). 내부망 outbound-only VM 이라 밖에서 push 하지 않고 VM 이 outbound 로 이미지·compose 를 pull 한다. VM 생성·OS 설정은 별도 준비된 VM 전제 — docker·cosign 설치와 운영 스크립트 배치는 1회성 `bootstrap.sh`.

관련: artifact(이미지)는 `docs/guides/release.md`. 환경변수·secret contract 는 `docs/reference/contracts/env.md`.

## 1. 배포 모델 한눈

| 계층 | 담당 | 수단 |
|------|------|------|
| VM 생성·서브넷·OS 설정 (provisioning) | 별도 준비 VM 전제 | OpenStack 등 (본 repo 무관) |
| docker·cosign·디렉토리·secret·운영 스크립트 배치 (bootstrap) | 1회성 | `bootstrap.sh` (멱등, raw 에서 curl) |
| 엔진 rollout (검증·pull·마이그레이션·health·rollback) | 본 repo | `deploy.sh` (VM 에서 실행) |

전제: 배포 대상 VM 은 GitHub·GHCR·Sigstore 로 outbound HTTPS 만 가능하면 된다 (inbound·SSH·runner 불요) — VM 이 스스로 이미지를 pull.

왜 GitHub Actions runner 를 안 쓰나: 내부망 VM 은 GitHub-hosted runner 가 도달 못 하고(inbound 없음), self-hosted runner 는 public repo 에서 fork PR 이 runner(=prod VM)에서 코드를 실행할 수 있어 GitHub 이 권고하지 않는다. 그래서 밖에서 push 하는 대신 VM 이 pull 하고, 배포는 사람이 VM 에서 `deploy.sh` 를 실행한다(= 배포 게이트).

## 2. VM 1회 부트스트랩

`bootstrap.sh` 가 (1) docker engine + compose plugin + cosign (2) 배포 디렉토리·secret 스캐폴딩·`.env` 템플릿 (3) 운영 스크립트(`deploy.sh`·`rotate-secret.sh`) 배치를 멱등 수행한다. public repo 라 raw 에서 받아 실행 — clone 불요, `.env` 템플릿과 두 운영 스크립트는 스크립트가 자체 fetch:

```bash
curl -fsSL https://raw.githubusercontent.com/z-converter-assessment/assessment-engine/main/bootstrap.sh -o bootstrap.sh
sudo bash bootstrap.sh
```

선택 env: `DEPLOY_DIR`(기본 `/opt/assessment-engine`), `COSIGN_VERSION`(기본은 스크립트가 핀한 버전 — 바꾸면 체크섬 대조를 건너뛴다), `ENV_TEMPLATE_URL`·`DEPLOY_SCRIPT_URL`·`ROTATE_SCRIPT_URL`·`PROD_COMPOSE_URL`(기본 raw main).

`bootstrap.sh` 가 `$DEPLOY_DIR/secrets/*` 를 만든다 — `docker-compose.prod.yml` 의 `secrets:` 항목을 읽어
없는 파일만 강 random 으로 채우고 권한 644 를 건다(디렉토리는 0700 root 소유, 근거는 `docs/reference/contracts/env.md`).
재실행해도 기존 값은 보존되므로 `deploy.sh` 를 갱신하려고 다시 돌려도 안전하다.

남는 일은 하나다. `$DEPLOY_DIR/.env` 에 `POSTGRES_USER`·`RABBITMQ_USER`·`ZDM_DEFAULT_IP` 등 운영값을 채운다
(비밀번호는 file 채널이라 여기 두지 않는다).

`rabbitmq_password` 는 외부 agent 가 broker 발행에 쓰는 값이라 agent 설정에도 같은 값을 넣는다 — 불일치 시 agent 인증이 실패해 데이터가 들어오지 않는다. 값은 `sudo cat $DEPLOY_DIR/secrets/rabbitmq_password` 로 확인한다.

## 3. rollout (`deploy.sh`)

배포는 VM 에서 사람이 실행한다 — 실행 자체가 배포 게이트(release 성공이 자동 배포로 이어지지 않는다). 배포할 버전의 이미지가 GHCR 에 발행돼 있어야 한다 — 발행 절차는 `docs/guides/release.md`.

```bash
sudo $DEPLOY_DIR/deploy.sh vX.Y.Z
```

선택 env: `DEPLOY_DIR`·`REPO_ID`·`IMAGE_REPO`·`RAW_BASE`(기본은 이 저장소 기준), `HEALTH_RETRIES`(기본 30)·`HEALTH_INTERVAL`(기본 5초).

`deploy.sh` 시퀀스:
1. 선행 조건 확인 — `docker`·`cosign`·`flock` 존재와 `$DEPLOY_DIR/.env` 존재. version 형식 가드(`vX.Y.Z`) 후 이미지 태그 산출 (`...:X.Y.Z`).
2. 배포 락 — `flock` 으로 동시 실행을 막는다. 두 배포가 겹치면 `.env` 의 핀과 `.last-good` 이 서로를 덮어 rollback 대상이 뒤섞인다.
3. cosign verify — 이미지가 이 repo `release.yml`(GitHub OIDC)에서 서명됐는지 검증. 미통과 시 중단.
4. 배포 tag 의 compose 파일 둘(base·prod overlay)을 raw 에서 fetch 하고 교체 전 사본을 남긴다 (이미지-compose 토폴로지 버전 일치). dev override 는 받지 않는다.
5. capture-before-swap — `.env` 의 현재 `ENGINE_IMAGE`(직전 정상)를 `.last-good` 으로 보존 후 새 버전으로 핀.
6. `docker compose pull` -> `up -d`. base compose 의 migrate init-container 가 앱 서비스 기동 전 `alembic upgrade head` 를 실행한다(depends_on) — 절차는 `docs/guides/migrate.md`.
7. health gate — compose 가 정의한 서비스가 모두 정상인지와 호스트에서 web `/health` 200 이 나오는지를 함께 확인한다(재시도 횟수·간격은 `HEALTH_RETRIES`·`HEALTH_INTERVAL`). 정상 기준은 restart 정책으로 가른다 — compose 가 재시작하지 않겠다고 선언한 서비스(`migrate`)만 종료 코드 0 이 완료를 뜻하고, 나머지는 running 이며 healthcheck 가 있으면 healthy 여야 한다.
8. 실패 시 rollback — 서비스 상태를 출력한 뒤 `.last-good` 이미지와 직전 compose 사본으로 되돌려 재기동하고 health 를 재확인한다. 적용된 마이그레이션은 남는다 — 구버전이 새 스키마에서 동작해야 성립한다. `.last-good` 없으면(최초 배포) 자동 rollback 불가.

되돌리기: 이전 버전으로 `deploy.sh v<이전>` 재실행.

## 4. 단일 호스트 수동 기동 (deploy.sh 없이)

평가(PoC)·간단 확인 시. repo checkout 기준으로 compose 직접 기동:

```bash
cp .env.example .env               # COMPOSE_FILE 포함(base+prod 자동 머지) · 평문 비번 없음
install -d -m 0700 secrets
printf '%s' "$(openssl rand -base64 32)" > secrets/<항목명>   # 목록: docker-compose.prod.yml 의 secrets:
chmod 644 secrets/*
# ENGINE_IMAGE 로 배포 버전 핀 (필수 — 미설정이면 compose 가 파싱 단계에서 거부한다):
echo 'ENGINE_IMAGE=ghcr.io/z-converter-assessment/assessment-engine:1.2.1' >> .env
docker compose up -d              # base+prod pull-and-run. web http://localhost:8000
```

GHCR public — 토큰 없이 pull. secret 이 없거나 뻔한 값이면 환경과 무관하게 기동을 거부한다(fail-fast). 영속 볼륨을 외부 디스크에 두려면 `PGDATA_HOST`/`MQ_DATA_HOST` 주입(미설정 시 named volume).

비번은 file-secret 채널 단일 — `./secrets/*` 가 `/run/secrets/*` 로 마운트돼 secret 이 컨테이너 env(`docker inspect`)에 뜨지 않는다. 주입 경로와 권한 근거는 `docs/reference/contracts/env.md`.

## 5. 비밀번호 교체 (`rotate-secret.sh`)

교체 대상은 `postgres`·`rabbitmq` 둘이다.

```bash
sudo $DEPLOY_DIR/rotate-secret.sh postgres
sudo $DEPLOY_DIR/rotate-secret.sh rabbitmq
```

선택 env: `DEPLOY_DIR`·`HEALTH_RETRIES`(기본 24)·`HEALTH_INTERVAL`(기본 5초).

서버 계정 변경 -> `secrets/{target}_password` 갱신 -> web·consumer·worker 재생성 순서로 돌고, 재생성 동안 수 초 다운타임이 있다. health 가 회복되지 않으면 서버 계정과 파일을 원래 값으로 되돌린다. `deploy.sh` 와 같은 락을 잡아 배포와 겹치지 않는다.

`rabbitmq` 를 교체하면 agent 설정의 같은 값도 함께 바꾼다 (2절과 동일 제약).

## 6. 운영 contract 한눈

| contract | 본 repo 위치 | 충족할 것 |
|---------|-----------|-------------------|
| 이미지 무결성 | `release.yml` cosign 서명 | 배포 전 `cosign verify` (`deploy.sh` 자동) |
| 환경변수 | `docs/reference/contracts/env.md` | `.env` + `secrets/*` 채움 |
| Secret | `config.py` 필드 제약 + `_validate_*_secrets` | 미설정·빈값·뻔한 값 거부 (환경 무관) — strong random 주입 |
| Schema | 이미지 안 `_alembic.ini` + `migrations/` | base compose migrate init-container 자동 실행 |
| graceful shutdown | #F11 (`docs/reference/consumer.md`) | compose `stop_grace_period` 충분 (SIGTERM) |
| 헬스 endpoint | `GET /health` (web) | `deploy.sh` health gate 가 compose healthcheck 결과와 함께 확인 |
| 관측 | `LOG_FORMAT=json` (#F7) | log aggregator collector (외부) |

## 7. 트러블슈팅

| 증상 | 원인·조치 |
|------|----------|
| `Settings()` 진입 시점 `ValueError` | 비밀번호 미설정·뻔한 값, 또는 secret 파일과 환경변수 동시 존재. `secrets/*` 배치와 `.env` 를 함께 점검 (`docs/reference/contracts/env.md`) |
| migrate 컨테이너 실패 — `extension "timescaledb" is not available` | postgres 이미지는 `timescale/timescaledb-ha` 라 평시 문제없음. 외부 managed DB 사용 시 `CREATE EXTENSION IF NOT EXISTS timescaledb` 사전 실행 (`docs/guides/migrate.md`) |
| consumer 가 broker 연결 실패 반복 | `RABBITMQ_*`·vhost 권한 검토 (`docs/reference/rabbitmq.md`) |
| `/health` 200 인데 inventory 안 들어옴 | 에이전트 별도 install·broker 연결 필요. agent 측 점검 (`docs/reference/contracts/agent-data.md`) |
| `deploy.sh` cosign verify 실패 | 이미지가 release.yml 서명본이 아님(태그 오타·미발행). release 발행 여부·태그 확인 |
| health 실패 -> 자동 rollback | `.last-good` 이미지로 복귀 — 스키마는 되돌아가지 않는다. 원인(마이그레이션 실패·설정 오류)은 `docker compose logs` 로 확인 후 재배포 |
| `deploy.sh` 가 "다른 배포가 진행 중" 으로 중단 | 같은 VM 에서 배포·비밀번호 교체가 이미 실행 중이다. 끝난 뒤 재실행. 비정상 종료로 락이 남았다면 `$DEPLOY_DIR/.deploy.lock` 을 잡은 프로세스를 확인 |
| 비밀번호 교체 후 앱이 붙지 못함 | `rotate-secret.sh` 가 서버 계정과 `secrets/*` 를 원래 값으로 되돌린다. `docker compose logs` 로 원인 확인 후 재시도 |

## 8. 한계

- 단일 VM·단일 prod 환경. staging 승격·다수 호스트·zero-downtime(blue-green)은 미도입 — 필요 시 별도 ADR.
- 배포 매체 docker compose 단일. wheel+venv·k8s 등 다른 매체 미지원.
- 인터넷 노출 hardened prod(HTTPS ingress·reverse proxy)는 배포 환경이 compose 앞단에 추가.
