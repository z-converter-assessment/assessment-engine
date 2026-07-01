# 배포 가이드 — bootstrap + rollout

본 문서는 내부망/OpenStack VM 1 대(단일 prod)에 엔진을 docker compose 매체로 배포·rollout 하는 단계별 가이드 (ADR 0048).

배포 매체는 docker compose 단일. 엔진 rollout(pull -> migration -> up -> health -> rollback)은 본 repo `deploy.yml`(self-hosted runner)이 소유한다. VM 자체의 provisioning(생성·OS 설정)은 본 repo 범위 밖 — docker engine 설치는 1회성 `bootstrap.sh` 로 편의 제공.

관련: artifact(이미지)는 `docs/operations/release.md`. 환경변수·secret contract 는 `docs/operations/env.md`.

## 1. 배포 모델 한눈

| 계층 | 소유 | 수단 |
|------|------|------|
| VM 생성·서브넷·OS 설정 (provisioning) | 범위 밖 | OpenStack 등 (본 repo 무관) |
| docker engine·compose·runner·secret 배치 (bootstrap) | 1회성 | `bootstrap.sh` (멱등) |
| 엔진 rollout (배포·마이그레이션·health·rollback) | 본 repo | `deploy.yml` (self-hosted runner) |

전제: 배포 대상 VM 은 GitHub 로 outbound HTTPS 만 가능하면 된다 (inbound·SSH 불요) — self-hosted runner 가 VM 에서 폴링·로컬 실행.

## 2. VM 1회 부트스트랩

`bootstrap.sh` 가 (1) docker engine + compose plugin (2) 배포 디렉토리·secret 스캐폴딩 (3) self-hosted runner systemd 서비스 등록을 멱등 수행한다.

```bash
# repo checkout 또는 bootstrap.sh + env.example 만 VM 에 복사 후:
sudo GITHUB_RUNNER_URL=https://github.com/z-converter-assessment/assessment-engine \
     GITHUB_RUNNER_TOKEN=<repo Settings > Actions > Runners > New self-hosted runner 토큰> \
     ./bootstrap.sh
```

선택 env: `DEPLOY_DIR`(기본 `/opt/assessment-engine`, `deploy.yml` `vars.DEPLOY_DIR` 와 일치), `RUNNER_USER`(기본 `assessment`), `RUNNER_LABELS`(기본 `assessment-prod`, `deploy.yml` runs-on 라벨과 일치 필수).

부트스트랩 후 3 가지를 채운다:

1. `$DEPLOY_DIR/.env` — `env.example` 복사본. `POSTGRES_USER`·`RABBITMQ_USER`·`ZDM_DEFAULT_IP` 등 운영값 (secret 은 제외 — file 채널).
2. `$DEPLOY_DIR/secrets/*` — 강 random 비번 (권한 600):
   ```bash
   printf '%s' "$(openssl rand -base64 32)" > $DEPLOY_DIR/secrets/postgres_password
   printf '%s' "$(openssl rand -base64 32)" > $DEPLOY_DIR/secrets/rabbitmq_password
   chmod 600 $DEPLOY_DIR/secrets/*
   ```
3. repo `production` Environment protection rule — reviewer 승인자 지정 (deploy.yml 승인 게이트).

self-hosted runner 보안 (운영 의무): fork PR·비신뢰 브랜치가 self-hosted runner 에서 실행되지 않게 repo 설정에서 "Require approval for all outside collaborators" + 배포 트리거를 protected 브랜치/태그로 제한. runner 는 `deploy.yml` 만 실행하도록 label 로 격리.

## 3. rollout (`deploy.yml`)

배포는 수동 트리거 + 승인 게이트 — release 성공 후 자동 배포하지 않는다 (prod 단일 VM 비가역 작업 앞 사람 게이트).

실행: GitHub Actions -> `deploy` workflow -> Run workflow -> `version` 에 `vX.Y.Z` 입력 -> `production` Environment 승인자가 승인 -> rollout.

rollout 시퀀스 (deploy.yml):
1. version 형식 가드(`vX.Y.Z`) + 이미지 태그 산출 (`...:X.Y.Z`).
2. 배포 tag 커밋의 compose 파일 checkout (이미지-compose 토폴로지 버전 일치).
3. cosign verify — 이미지가 이 repo `release.yml`(GitHub OIDC)에서 서명됐는지 검증. 미통과 시 배포 중단.
4. compose 파일을 `DEPLOY_DIR` 로 동기화 (`.env`·`secrets/` 는 보존).
5. capture-before-swap — `.env` 의 현재 `ENGINE_IMAGE`(직전 정상)를 `.last-good` 으로 보존 후 새 버전으로 핀.
6. `docker compose pull` -> `docker compose up -d`. base compose 의 migrate init-container 가 web/consumer 기동 전 `alembic upgrade head` 를 실행(depends_on) — migration 이 rollout 에 선행함이 compose 로 보장.
7. health gate — web `/health` 200 확인(재시도). 통과 시 성공.
8. 실패 시 rollback — `.last-good` 이미지로 `.env` 되돌려 재기동 + health 재확인. `.last-good` 없으면(최초 배포) 자동 rollback 불가 -> 수동 조치.

## 4. 단일 호스트 수동 기동 (deploy.yml 없이)

평가(PoC)·runner 미구성 시. repo checkout 기준으로 compose 직접 기동:

```bash
cp env.example .env               # COMPOSE_FILE 포함(base+secrets 자동 머지) · 평문 비번 없음
mkdir -p secrets
printf '%s' "$(openssl rand -base64 32)" > secrets/postgres_password
printf '%s' "$(openssl rand -base64 32)" > secrets/rabbitmq_password
chmod 600 secrets/*
# ENGINE_IMAGE 로 배포 버전 핀 (미설정 시 base 기본 = __ENGINE_VERSION__ placeholder, 정확 버전 명시 권장):
echo 'ENGINE_IMAGE=ghcr.io/z-converter-assessment/assessment-engine:0.1.0' >> .env
docker compose up -d              # base+secrets pull-and-run. web http://localhost:8000
```

GHCR public — 토큰 없이 pull (ADR 0035). `APP_ENV=prod` 기본이라 secret 부재·weak 면 기동 거부(fail-fast). 영속 볼륨을 외부 디스크에 두려면 `PGDATA_HOST`/`MQ_DATA_HOST` 주입(미설정 시 named volume).

비번은 file-secret 채널 단일(ADR 0046) — `./secrets/*`(600)이 `/run/secrets/*` 로 마운트돼 app 은 `secrets_dir`, postgres 는 `*_FILE`, rabbitmq 는 entrypoint wrapper 로 읽는다. secret 이 컨테이너 env(`docker inspect`)에 안 뜬다.

## 5. 운영 contract 한눈

| contract | 본 repo 위치 | 충족할 것 |
|---------|-----------|-------------------|
| 이미지 무결성 | `release.yml` cosign 서명 | 배포 전 `cosign verify` (deploy.yml 자동) |
| 환경변수 | `docs/operations/env.md` | `.env` + `secrets/*` 채움 |
| Secret | `config.py` `_validate_prod_*` | `APP_ENV=prod` weak default 거부 — strong random 주입 |
| Schema | 이미지 안 `_alembic.ini` + `migrations/` | base compose migrate init-container 자동 실행 |
| graceful shutdown | #F11 (`docs/architecture/consumer.md`) | compose `stop_grace_period` 충분 (SIGTERM) |
| 헬스 endpoint | `GET /health` (web) | deploy.yml health gate + compose healthcheck |
| 관측 | `LOG_FORMAT=json` (#F7) | log aggregator collector (외부) |

## 6. 트러블슈팅

| 증상 | 원인·조치 |
|------|----------|
| `Settings()` 진입 시점 `ValueError` | weak default 거부 — `APP_ENV=prod` 인데 secret 파일 부재·weak. `secrets/*` 배치 점검 (`docs/operations/env.md`) |
| migrate 컨테이너 실패 — `extension "timescaledb" is not available` | postgres 이미지는 `timescale/timescaledb-ha` 라 평시 문제없음. 외부 managed DB 사용 시 `CREATE EXTENSION IF NOT EXISTS timescaledb` 사전 실행 (`docs/operations/alembic.md`) |
| consumer 가 broker 연결 실패 반복 | `RABBITMQ_*`·vhost 권한 검토 (`docs/architecture/rabbitmq.md`) |
| `/health` 200 인데 inventory 안 들어옴 | 에이전트 별도 install·broker 연결 필요. agent 측 점검 (`docs/architecture/agent.md`) |
| deploy.yml cosign verify 실패 | 이미지가 release.yml 서명본이 아님(태그 오타·비공식 이미지). release 발행 여부·태그 확인 |
| health 실패 -> 자동 rollback | `.last-good` 이미지로 복귀. 원인(마이그레이션 실패·설정 오류)은 `docker compose logs` 로 확인 후 재배포 |

## 7. 한계 (ADR 0048)

- 단일 VM·단일 prod 환경. staging 승격·다수 호스트·zero-downtime(blue-green)은 미도입 — 필요 시 별도 ADR.
- 배포 매체 docker compose 단일. wheel+venv·k8s 등 다른 매체 미지원.
- 인터넷 노출 hardened prod(HTTPS ingress·reverse proxy)는 배포 환경이 compose 앞단에 추가 (본 repo 범위 밖).
