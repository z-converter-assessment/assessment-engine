# 배포 가이드 — bootstrap + rollout

본 문서는 내부망 VM 1 대(단일 prod)에 엔진을 docker compose 매체로 배포·rollout 하는 단계별 가이드.

배포 매체는 docker compose 단일. 엔진 rollout(cosign verify -> compose fetch -> pull -> migration -> up -> health -> rollback)은 배포 대상 VM 에서 `deploy.sh` 를 실행해 수행한다. 내부망 outbound-only VM 이라 밖에서 push 하지 않고 VM 이 outbound 로 이미지·compose 를 pull 한다. VM 생성·OS 설정은 별도 준비된 VM 전제 — docker·cosign·deploy.sh 설치는 1회성 `bootstrap.sh`.

관련: artifact(이미지)는 `docs/guides/release.md`. 환경변수·secret contract 는 `docs/reference/contracts/env.md`.

## 1. 배포 모델 한눈

| 계층 | 담당 | 수단 |
|------|------|------|
| VM 생성·서브넷·OS 설정 (provisioning) | 별도 준비 VM 전제 | OpenStack 등 (본 repo 무관) |
| docker·cosign·디렉토리·secret·deploy.sh 배치 (bootstrap) | 1회성 | `bootstrap.sh` (멱등, raw 에서 curl) |
| 엔진 rollout (검증·pull·마이그레이션·health·rollback) | 본 repo | `deploy.sh` (VM 에서 실행) |

전제: 배포 대상 VM 은 GitHub·GHCR·Sigstore 로 outbound HTTPS 만 가능하면 된다 (inbound·SSH·runner 불요) — VM 이 스스로 이미지를 pull.

왜 GitHub Actions runner 를 안 쓰나: 내부망 VM 은 GitHub-hosted runner 가 도달 못 하고(inbound 없음), self-hosted runner 는 public repo 에서 fork PR 이 runner(=prod VM)에서 코드를 실행할 수 있어 GitHub 이 권고하지 않는다. 그래서 밖에서 push 하는 대신 VM 이 pull 하고, 배포는 사람이 VM 에서 `deploy.sh` 를 실행한다(= 배포 게이트).

## 2. VM 1회 부트스트랩

`bootstrap.sh` 가 (1) docker engine + compose plugin + cosign (2) 배포 디렉토리·secret 스캐폴딩·`.env` 템플릿 (3) `deploy.sh` 배치를 멱등 수행한다. public repo 라 raw 에서 받아 실행 — clone 불요, `.env` 템플릿과 `deploy.sh` 는 스크립트가 자체 fetch:

```bash
curl -fsSL https://raw.githubusercontent.com/z-converter-assessment/assessment-engine/main/bootstrap.sh -o bootstrap.sh
sudo bash bootstrap.sh
```

선택 env: `DEPLOY_DIR`(기본 `/opt/assessment-engine`), `COSIGN_VERSION`(기본 latest), `ENV_TEMPLATE_URL`·`DEPLOY_SCRIPT_URL`(기본 raw main).

부트스트랩 후 2 가지를 채운다:

1. `$DEPLOY_DIR/.env` — bootstrap 가 raw 에서 받은 `env.example` 템플릿. `POSTGRES_USER`·`RABBITMQ_USER`·`ZDM_DEFAULT_IP` 등 운영값 (secret 은 제외 — file 채널).
2. `$DEPLOY_DIR/secrets/*` — 강 random 비번 (권한 644 — postgres 공식 이미지가 non-root 유저로 `*_FILE` 을
   읽어 600이면 Permission denied 로 기동 실패. 보안 경계는 `secrets/` 디렉토리 0700 root 소유가 담당):
   ```bash
   printf '%s' "$(openssl rand -base64 32)" > $DEPLOY_DIR/secrets/postgres_password
   printf '%s' "$(openssl rand -base64 32)" > $DEPLOY_DIR/secrets/rabbitmq_password
   chmod 644 $DEPLOY_DIR/secrets/*
   ```

## 3. rollout (`deploy.sh`)

배포는 VM 에서 사람이 실행한다 — 실행 자체가 배포 게이트(release 성공이 자동 배포로 이어지지 않는다). 배포할 버전의 이미지가 GHCR 에 발행돼 있어야 한다 — 발행 절차는 `docs/guides/release.md`.

```bash
sudo $DEPLOY_DIR/deploy.sh vX.Y.Z
```

`deploy.sh` 시퀀스:
1. version 형식 가드(`vX.Y.Z`) + 이미지 태그 산출 (`...:X.Y.Z`).
2. cosign verify — 이미지가 이 repo `release.yml`(GitHub OIDC)에서 서명됐는지 검증. 미통과 시 중단.
3. 배포 tag 의 compose 파일을 raw 에서 fetch (이미지-compose 토폴로지 버전 일치).
4. capture-before-swap — `.env` 의 현재 `ENGINE_IMAGE`(직전 정상)를 `.last-good` 으로 보존 후 새 버전으로 핀.
5. `docker compose pull` -> `up -d`. base compose 의 migrate init-container 가 web/consumer 기동 전 `alembic upgrade head` 실행(depends_on) — migration 선행이 compose 로 보장.
6. health gate — web `/health` 200 확인(재시도). 통과 시 성공.
7. 실패 시 rollback — `.last-good` 이미지로 되돌려 재기동 + health 재확인. `.last-good` 없으면(최초 배포) 자동 rollback 불가.

되돌리기: 이전 버전으로 `deploy.sh v<이전>` 재실행.

## 4. 단일 호스트 수동 기동 (deploy.sh 없이)

평가(PoC)·간단 확인 시. repo checkout 기준으로 compose 직접 기동:

```bash
cp env.example .env               # COMPOSE_FILE 포함(base+secrets 자동 머지) · 평문 비번 없음
mkdir -p secrets
printf '%s' "$(openssl rand -base64 32)" > secrets/postgres_password
printf '%s' "$(openssl rand -base64 32)" > secrets/rabbitmq_password
chmod 644 secrets/*
# ENGINE_IMAGE 로 배포 버전 핀 (미설정 시 base 기본 = __ENGINE_VERSION__ placeholder, 정확 버전 명시 권장):
echo 'ENGINE_IMAGE=ghcr.io/z-converter-assessment/assessment-engine:0.1.0' >> .env
docker compose up -d              # base+secrets pull-and-run. web http://localhost:8000
```

GHCR public — 토큰 없이 pull. `APP_ENV=prod` 기본이라 secret 부재·weak 면 기동 거부(fail-fast). 영속 볼륨을 외부 디스크에 두려면 `PGDATA_HOST`/`MQ_DATA_HOST` 주입(미설정 시 named volume).

비번은 file-secret 채널 단일 — `./secrets/*`(644)이 `/run/secrets/*` 로 마운트돼 app 은 `secrets_dir`, postgres 는 `*_FILE`, rabbitmq 는 entrypoint wrapper 로 읽는다. secret 이 컨테이너 env(`docker inspect`)에 안 뜬다. 644인 이유 — Docker Compose file-secret 은 Swarm 과 달리 호스트 파일 권한을 컨테이너 안에 그대로 반영하는데, postgres 공식 이미지는 non-root `postgres` 유저로 전환한 뒤 그 파일을 읽어 600이면 Permission denied.

## 5. 운영 contract 한눈

| contract | 본 repo 위치 | 충족할 것 |
|---------|-----------|-------------------|
| 이미지 무결성 | `release.yml` cosign 서명 | 배포 전 `cosign verify` (`deploy.sh` 자동) |
| 환경변수 | `docs/reference/contracts/env.md` | `.env` + `secrets/*` 채움 |
| Secret | `config.py` `_validate_prod_*` | `APP_ENV=prod` weak default 거부 — strong random 주입 |
| Schema | 이미지 안 `_alembic.ini` + `migrations/` | base compose migrate init-container 자동 실행 |
| graceful shutdown | #F11 (`docs/reference/consumer.md`) | compose `stop_grace_period` 충분 (SIGTERM) |
| 헬스 endpoint | `GET /health` (web) | `deploy.sh` health gate + compose healthcheck |
| 관측 | `LOG_FORMAT=json` (#F7) | log aggregator collector (외부) |

## 6. 트러블슈팅

| 증상 | 원인·조치 |
|------|----------|
| `Settings()` 진입 시점 `ValueError` | weak default 거부 — `APP_ENV=prod` 인데 secret 파일 부재·weak. `secrets/*` 배치 점검 (`docs/reference/contracts/env.md`) |
| migrate 컨테이너 실패 — `extension "timescaledb" is not available` | postgres 이미지는 `timescale/timescaledb-ha` 라 평시 문제없음. 외부 managed DB 사용 시 `CREATE EXTENSION IF NOT EXISTS timescaledb` 사전 실행 (`docs/guides/migrate.md`) |
| consumer 가 broker 연결 실패 반복 | `RABBITMQ_*`·vhost 권한 검토 (`docs/reference/rabbitmq.md`) |
| `/health` 200 인데 inventory 안 들어옴 | 에이전트 별도 install·broker 연결 필요. agent 측 점검 (`docs/reference/contracts/agent-data.md`) |
| `deploy.sh` cosign verify 실패 | 이미지가 release.yml 서명본이 아님(태그 오타·미발행). release 발행 여부·태그 확인 |
| health 실패 -> 자동 rollback | `.last-good` 이미지로 복귀. 원인(마이그레이션 실패·설정 오류)은 `docker compose logs` 로 확인 후 재배포 |

## 7. 한계

- 단일 VM·단일 prod 환경. staging 승격·다수 호스트·zero-downtime(blue-green)은 미도입 — 필요 시 별도 ADR.
- 배포 매체 docker compose 단일. wheel+venv·k8s 등 다른 매체 미지원.
- 인터넷 노출 hardened prod(HTTPS ingress·reverse proxy)는 배포 환경이 compose 앞단에 추가.
