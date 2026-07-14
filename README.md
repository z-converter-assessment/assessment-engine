# assessment-engine

고객사 내부 네트워크의 호스트 인벤토리·시계열 메트릭을 수집·저장하고, 규칙 기반으로 호스트별 자원 적정성(right-sizing)과 운영 신호(OS 지원종료·용량 부족 등)를 진단해 자원 재배치·용량 의사결정을 보조하는 B2B 내부 포털.

고객사 네트워크 내에 엔진이 설치되고, 각 호스트의 C 기반 에이전트가 인벤토리·메트릭을 수집해 MQ에 직접 발행한다. Consumer가 메시지를 소비해 DB에 저장하고, web 이 수집 데이터를 규칙 기반으로 분석해 호스트별 자원 적정성(right-sizing, USE Method)과 운영 신호를 진단하고 보고서·대시보드로 제공한다. 운영자는 web UI 의 모니터링 화면·보고서·JSON Export·원격 설치 task 를 활용해 자원 재배치·용량 의사결정을 진행한다.

본 repo 는 엔진 애플리케이션, docker compose 배포(prod base · dev override 핫리로드), 엔진 rollout(`deploy.sh` — VM 에서 실행), VM 부트스트랩(`bootstrap.sh` — docker·cosign·deploy.sh 설치)으로 구성된다.

---

## 아키텍처

```
 +------------------------------------------------------------------+
 |  Agent (C, separate repo: assessment-agent)                      |
 |  collector  : /proc scrape + inventory/metrics/error publish     |
 |  worker     : task.install consume + OS script exec + result     |
 +-----+----------------------------------------+-------------------+
       | inventory/metrics/error + task.result  ^ task.install.<agent_id>
       | (server.* / worker.result)             | (agent consumes)
       v                                        |
 +------------------------------------------------------------------+
 |  RabbitMQ                                                        |
 |  - assessment exchange       : server.inventory/metrics/error    |
 |  - assessment.tasks exchange : task.install.<agent_id>           |
 |                                + task.result -> worker.result    |
 |  - DLX/DLQ per exchange                                          |
 +--+----------------+----------------------------------------------+
    | server.*       | task.result
    v                v
 +-------------------------------+  +-------------------------------+
 |  Consumer (aio-pika)          |  |  Redis                        |
 |  - parse/idempot/persist      |  |  - cache / online TTL         |
 |  - time invariants            |  |  - idempotency SET NX         |
 |  - agent restart signals      |  |  - agent restart counter      |
 |  - task.result -> Task UPDATE |  |  - fail-open on RedisError    |
 +--------------+----------------+  +--------------+----------------+
                v                                  |
 +-------------------------------+                 |  SUBSCRIBE
 |  TimescaleDB                  |                 |
 |  - 5 timeseries tables        |                 |
 |  - server_inventory + history |                 |
 |  - tasks (audit log)          |                 |
 |  - diagnostic_jobs (report    |                 |
 |    snapshots)                 |                 |
 +--------------+----------------+                 |
                ^  read / emit / claim             |
                |                                  |
 +--------------+----------------------------------+---------------+
 |  FastAPI (uvicorn, port 8000)                                    |
 |  - SSR  : dashboard / detail / env+server report + history       |
 |  - REST : assessment / right-sizing / tasks / exports / metrics  |
 |  - charts : client-side fetch of REST (no push)                  |
 |  - rule-based right-sizing (recommendation.py, USE Method)       |
 |  - report emit -> diagnostic_jobs (pending; worker generates)    |
 |  - publishes task.install (assessment.tasks exchange)            |
 |  - plain HTTP ; prod TLS at external ingress                     |
 +------------------------------------------------------------------+
 +------------------------------------------------------------------+
 |  Worker (background process, assessment_engine.worker)           |
 |  - report job-claim (FOR UPDATE SKIP LOCKED) -> generate         |
 |    snapshot -> diagnostic_jobs (succeeded / failed)              |
 |  - install task reaper : deadline-overdue pending -> timeout     |
 |  - graceful shutdown (shared stop_event, in-flight loss 0)       |
 +------------------------------------------------------------------+
```

---

## 스택

| 영역 | 기술 |
|------|------|
| 애플리케이션 | Python 3.12 · FastAPI · pydantic · uvicorn · aio-pika · SQLAlchemy async · asyncpg · Jinja2 · loguru · httpx |
| DB / 캐시 / 브로커 | TimescaleDB (PostgreSQL 16) · Redis 7 · RabbitMQ 3.13 |
| Schema 관리 | Alembic 단일 진실 |
| 진단 | 규칙 기반 right-sizing (USE Method, `recommendation.py` — web 인라인 계산) |
| 관측 | loguru `LOG_FORMAT=text\|json` (구조화 로그) |
| 패키징 | uv + hatchling. CI 산출물 = Docker image (GHCR, 서명·SBOM·provenance) |
| 정적 자원 | Chart.js (CDN) · Cytoscape.js (네트워크 토폴로지, vendored) · 외부 `.js` + `defer` |

---

## CI 파이프라인

- git flow — `feature/*`·`fix/*` → `develop` PR(squash) → `develop` → `main` PR(merge) → `main`에 `v*` tag push → release(이미지 발행) → VM에서 `deploy.sh vX.Y.Z` 실행.
- 버전은 git tag 단일 진실 (hatch-vcs가 빌드 시 derive) — repo에 버전 미저장, bump 커밋 없음. branch protection + Conventional Commits PR title 강제.

| workflow | trigger | 검증·작업 |
|----------|---------|------|
| `pr-title-check.yml` | main PR opened·edited·synchronize·reopened | PR title이 Conventional Commits 형식 (`feat:`·`fix:`·`docs:` 등) 강제 |
| `ci.yml` | main PR | lint(ruff+hadolint) → test-unit·frontend typecheck·wheel build(병렬) → test-integration |
| `alembic-check.yml` | main PR | ORM·migrations 라운드트립 정합 |
| `codeql.yml` | main PR | CodeQL SAST (SQL injection·secret leak·XSS 정적 분석, Security 탭 alert) |
| `release.yml` | `main`에 tag `v*` push · workflow_dispatch | 멀티아치 엔진 이미지 빌드(버전=tag, hatch-vcs) → GHCR push + cosign 서명 + SBOM(SPDX) + SLSA provenance |

develop PR·push 는 CI 게이트가 없다 — develop 는 통합 브랜치로 게이트 없이 받고, 검증은 develop→main PR 에서 1회로 통일한다.

CI(코드 quality + 이미지 발행)는 GitHub Actions가 담당한다. 배포(rollout)는 GitHub Actions가 아니라 배포 대상 VM에서 `deploy.sh vX.Y.Z` 를 실행한다 — 내부망 outbound-only VM이라 밖에서 push하지 않고 VM이 이미지를 pull한다(아래 배포 절).

---

## 배포 산출물

semver tag `v*` push 시 릴리즈가 내놓는 산출물. 배포 매체는 docker compose 단일.

| 산출물 | 위치 |
|--------|------|
| Docker image (multi-arch `amd64,arm64`) | GHCR `ghcr.io/z-converter-assessment/assessment-engine:0.1.0`+`:0.1`+`:0`+`:latest` (semver tag `v0.1.0` -> 이미지 태그는 `v` 없는 `0.1.0`) |
| cosign 서명 + SBOM (SPDX) + SLSA provenance | 이미지 attestation (별도 파일 아님) — `cosign verify ghcr.io/z-converter-assessment/assessment-engine:0.1.0` 로 검증 |
| Alembic migrations·`_alembic.ini` | 이미지 동봉 (`hatch.force-include`) — base compose migrate init-container 가 기동 전 자동 실행 |

---

## 개발 (dev)

dev = base + `docker-compose.override.yml` 핫리로드. 코드 수정이 컨테이너 restart 없이 반영된다.

```bash
cp env.dev.example .env
docker compose up -d      # web http://localhost:8000
docker compose down -v    # 종료 (데이터 삭제)
```

IDE 자동완성·테스트 (compose 만 띄울 땐 불필요. 전제 `uv` 0.4+):

```bash
uv sync --group dev               # .venv 생성 + dev 그룹(pytest·ruff·types)
uv run pytest tests/unit/         # 단위
uv run pytest tests/integration/  # 통합 (testcontainers)
uv run ruff check .               # lint
uv run alembic check              # ORM·migrations 정합
```

---

## 운영 산출물

| 산출물 | URL / 설명 |
|--------|-----------|
| 모니터링 화면 | `/` (환경 개요 · 사이드바 "모니터링" 그룹) |
| 환경 보고서 (규칙 기반 진단 통합) | `/reports/environment?view=customer\|engineer` |
| 서버 보고서 (규칙 기반 진단 통합) | `/reports/servers?ids=...&view=customer\|engineer` |
| JSON Export | `POST /api/exports/inventory` |
| Install task | 서버 상세에서 원격 설치 task 발행 (`task.install`) |

---

## 배포 (prod)

배포 대상은 내부망 운영 VM 한 대다. 최초 1회 서버 구성으로 빈 VM에서 엔진이 실행될 때까지 세팅한 뒤, 이후 배포는 새 태그 발행 + `deploy.sh` 실행만 반복한다.

### 최초 서버 구성 (빈 VM에서 엔진 실행까지)

Debian/Ubuntu VM(GitHub로 outbound HTTPS 가능)에서 순서대로 진행한다.

1. 부트스트랩 — docker engine·compose·cosign·디렉토리·`deploy.sh`를 구성한다. `bootstrap.sh`를 받아 실행(public repo — clone 불요):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/z-converter-assessment/assessment-engine/main/bootstrap.sh -o bootstrap.sh
   sudo bash bootstrap.sh
   ```
   실행 후 `/opt/assessment-engine/`에 `.env`(env.example 템플릿)·`secrets/`·`deploy.sh`가 놓인다.

2. secret 파일 배치 (강 random, 권한 644 — postgres 공식 이미지가 non-root 유저로 읽으므로 600이면 Permission denied 로 기동 실패):
   ```bash
   cd /opt/assessment-engine
   printf '%s' "$(openssl rand -base64 32)" > secrets/postgres_password
   printf '%s' "$(openssl rand -base64 32)" > secrets/rabbitmq_password
   chmod 644 secrets/*
   ```
   랜덤이어도 잃어버리지 않는다 — 값은 `secrets/` 파일에 저장돼 앱이 자동으로 읽고, 필요 시 `sudo cat secrets/<name>`으로 확인한다(psql·RabbitMQ 관리 UI 등). 웹 포털은 무인증이라 별도 로그인이 없다. 단 `rabbitmq_password`는 외부 agent가 broker 발행에 쓰는 값이라 agent 설정에도 동일하게 넣어야 한다(불일치 시 agent 인증 실패로 데이터 미수집).

3. `/opt/assessment-engine/.env` 운영값을 채운다 — `POSTGRES_USER`·`RABBITMQ_USER`(변경 권장)·`ZDM_DEFAULT_IP` 등 환경에 맞게. 비번은 위 secret 파일 채널이라 `.env`에 넣지 않는다.

4. 이미지 발행 — `main`에 `git tag vX.Y.Z && git push origin vX.Y.Z`. `release.yml`이 이미지를 빌드·서명해 GHCR에 발행한다 (배포할 이미지가 있어야 함).

5. 배포 — VM에서:
   ```bash
   sudo /opt/assessment-engine/deploy.sh vX.Y.Z
   ```
   cosign verify → 그 태그의 compose fetch → pull → migration(init-container) → up → `/health` 확인이 진행되고, 실패 시 직전 정상 이미지로 자동 rollback. 엔진이 여기서 처음 뜬다.

### 이후 배포

새 버전을 올릴 때 두 단계만 반복한다:
1. `main`에 새 태그 push → `release.yml`이 이미지 발행.
2. VM에서 `sudo /opt/assessment-engine/deploy.sh vX.Y.Z`.

되돌리기도 이전 버전으로 `deploy.sh v<이전>` 을 실행하면 된다.

### 단일 호스트 수동 기동 (deploy.sh 없이)

평가·runner 미구성 시. prod = base + `docker-compose.secrets.yml`(file-secret). 비번을 `./secrets/*` 파일로 주입 — 컨테이너 env에 안 뜬다.

```bash
cp env.example .env                         # COMPOSE_FILE 포함(base+secrets) · 평문 비번 없음

mkdir -p secrets                            # 비번 파일 (강 random, 권한 644 — non-root 컨테이너 유저 호환)
printf '%s' "$(openssl rand -base64 32)" > secrets/postgres_password
printf '%s' "$(openssl rand -base64 32)" > secrets/rabbitmq_password
chmod 644 secrets/*

docker compose up -d                        # base+secrets pull-and-run. web http://localhost:8000
```

`APP_ENV=prod` 라 secret 부재·weak 면 기동 거부(fail-fast). GHCR public — 토큰 없이 pull. 외부 디스크 볼륨은 `PGDATA_HOST`/`MQ_DATA_HOST` 주입(선택).
