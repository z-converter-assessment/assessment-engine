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
 |  - 7 metric timeseries tables |                 |
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

## 루트 구성

저장소 루트에 평평하게 놓인 파일들이다. 대부분 도구가 루트에서 찾기 때문에 다른 곳으로 옮길 수 없다.

| 파일 | 무엇 | 왜 루트인가 |
|------|------|------------|
| `pyproject.toml` · `uv.lock` | 파이썬 의존성·빌드·lint 설정 | uv 와 빌드 백엔드가 프로젝트 루트로 인식 |
| `package.json` · `pnpm-lock.yaml` · `tsconfig.json` | 표현계층 타입 계약 도구 (dev·CI 전용, 번들·빌드 아님) | 검사 대상이 `src/` 안 정적 JS 라 도구와 대상을 갈라놓을 이유가 없다 |
| `Dockerfile` | 엔진 이미지 (web·consumer·worker·migrate 공용) | 빌드 컨텍스트가 루트 |
| `docker-compose.yml` | prod-safe base | compose 가 루트에서 자동 인식 |
| `docker-compose.override.yml` | dev 전용 (로컬 빌드·bind mount·핫리로드) | base 와 자동 머지 |
| `docker-compose.prod.yml` | prod file-secret overlay | 위와 같음 |
| `.env.example` · `.env.dev.example` | 배포·dev 환경변수 템플릿 | `cp` 해서 `.env` 로 쓰는 진입점 |
| `bootstrap.sh` | 배포 VM 1회성 구성 | raw URL 로 받는 파일이라 경로가 운영 절차에 고정 |
| `deploy.sh` | 엔진 rollout | 위와 같음 |
| `README.md` | 본 문서 | GitHub 이 루트에서 렌더 |
| `.gitignore` · `.dockerignore` · `.claudeignore` | 각 도구의 제외 목록 | 도구가 컨텍스트 루트에서 읽음 |

프론트엔드 설정이 섞여 보이지만 별도 프로젝트가 아니다. 번들러도 빌드 산출물도 없고, FastAPI 가 내보내는 OpenAPI 에서 TS 타입을 생성해 `tsc --checkJs` 로 클라이언트 JS 를 검사하는 용도다. 서빙되는 JS 는 빌드를 거치지 않고 `src/assessment_engine/web/static/` 에서 그대로 나간다.

각 파일의 상세는 `docs/guides/local-dev.md`(compose·Dockerfile), `docs/guides/deploy.md`(배포 스크립트), `docs/reference/contracts/env.md`(환경변수), `docs/reference/web/type-contract.md`(타입 계약)가 갖는다.

---

## 스택

| 영역 | 기술 |
|------|------|
| 애플리케이션 | Python 3.12 · FastAPI · pydantic · uvicorn · aio-pika · SQLAlchemy async · asyncpg · Jinja2 · loguru · httpx |
| DB / 캐시 / 브로커 | TimescaleDB (PostgreSQL 16) · Redis 7 · RabbitMQ 3.13 |
| Schema 관리 | Alembic 단일 진실 |
| 진단 | 규칙 기반 right-sizing (USE Method, `recommendation.py` — web 인라인 계산) |
| 관측 | loguru `LOG_FORMAT=text\|json` (구조화 로그) |
| 패키징 | uv (빌드 백엔드 `uv_build`). CI 산출물 = Docker image (GHCR, 서명·SBOM·provenance) |
| 정적 자원 | Chart.js · Cytoscape.js (네트워크 토폴로지) — 둘 다 vendored (`static/js/vendor/`, 내부망 offline) · 외부 `.js` + `defer` |

---

## CI 파이프라인

- git flow — `feature/*`·`fix/*` → `develop` PR(squash) → `develop` → `main` PR(merge) → release(이미지 발행) → VM에서 `deploy.sh vX.Y.Z` 실행.
- 버전은 `pyproject.toml` 의 `version` 단일 진실 — 릴리즈 절차는 `docs/guides/release.md`. 브랜치·태그 보호와 required check 는 GitHub ruleset (`docs/guides/ci-setup.md`).

| workflow | 검증·작업 |
|----------|------|
| `pr-title-check.yml` | PR title 이 Conventional Commits 형식인지 |
| `ci.yml` | lint(ruff+hadolint) · 단위 테스트 · 프론트 타입 계약 · wheel build · 통합 테스트 |
| `alembic-check.yml` | ORM·migrations 라운드트립 정합 |
| `codeql.yml` | CodeQL SAST (Security 탭 alert) |
| `release.yml` | `main` push 시 멀티아치 엔진 이미지 빌드 → GHCR push + cosign 서명 + SBOM(SPDX) + SLSA provenance |

CI(코드 quality + 이미지 발행)는 GitHub Actions가 담당한다. 배포(rollout)는 GitHub Actions가 아니라 배포 대상 VM에서 `deploy.sh vX.Y.Z` 를 실행한다 — 내부망 outbound-only VM이라 밖에서 push하지 않고 VM이 이미지를 pull한다(아래 배포 절).

---

## 배포 산출물

릴리즈가 내놓는 것은 GHCR 이미지 하나다 — cosign 서명·SBOM·provenance 가 이미지 attestation 으로 붙고, Alembic migrations 도 그 안에 동봉된다. 태그 체계·검증 명령은 `docs/guides/release.md`.

---

## 환경변수와 비밀번호

설정은 `.env` 하나로 들어간다. 어느 템플릿을 복사했는지가 환경을 정한다 — `.env.dev.example` 이면
dev(핫리로드), `.env.example` 이면 배포용이다. 그 안의 `COMPOSE_FILE` 이 어떤 compose 파일을 합칠지도 정한다.

비밀번호에는 기본값이 없다. 미설정이면 환경과 무관하게 기동이 실패하고, `password`·`admin`·`root`·`changeme`
같은 뻔한 값도 거부된다. 주입 경로는 둘인데 환경에 따라 다르다.

| | 채널 | 값을 만드는 주체 |
|---|------|----------------|
| dev | `.env` 평문 (`POSTGRES_PASSWORD`·`RABBITMQ_PASSWORD`) | 템플릿에 이미 값이 들어 있다 — 그대로 쓴다 |
| 배포 | `secrets/*` 파일 -> 컨테이너 `/run/secrets/*` | `bootstrap.sh` 가 없는 것만 생성 |

배포에서 파일 채널을 쓰는 이유는 노출 회피다. env 로 넣으면 `docker inspect`·`compose config`·`/proc/environ`
에 평문이 그대로 뜬다. 그래서 `.env.example` 에는 비밀번호 키 자체가 없다.

두 채널을 동시에 두면 안 된다. 우선순위가 `환경변수 > .env > secrets/` 라 파일이 조용히 무시되는데,
그 상태를 감지해 기동을 막는다. 배포용 `.env` 에 비밀번호 키를 되살리지 않으면 마주칠 일은 없다.

직접 만들어야 할 때는 이렇게 한다. 파일명은 `docker-compose.prod.yml` 의 `secrets:` 항목과 같아야 하고,
`echo` 는 개행을 붙이므로 쓰지 않는다.

```bash
printf '%s' "$(openssl rand -base64 32)" > secrets/<항목명>
chmod 644 secrets/*          # 컨테이너의 non-root 유저가 읽어야 한다
```

권한 근거와 키 카탈로그는 `docs/reference/contracts/env.md`, 배포 절차는 `docs/guides/deploy.md` 가 갖는다.

---

## 개발 (dev)

dev = base + `docker-compose.override.yml` 핫리로드. 코드 수정이 컨테이너 restart 없이 반영된다.

```bash
cp .env.dev.example .env
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

배포 대상은 내부망 운영 VM 한 대다. 빈 VM 을 `bootstrap.sh` 로 1회 구성한 뒤, 이후 배포는 버전을 올려 `main` 에 머지하고 VM 에서 `deploy.sh vX.Y.Z` 를 실행하는 두 단계를 반복한다.

`deploy.sh` 는 공급망 검증부터 health gate 까지를 한 번에 수행하고 실패하면 직전 정상 이미지로 되돌린다. 되돌리기도 이전 버전으로 같은 명령을 실행하면 된다. 단계별 상세는 `docs/guides/deploy.md` 3절.

절차·secret 배치·단일 호스트 수동 기동은 `docs/guides/deploy.md` 가 단일 진실이다.
