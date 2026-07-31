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

배포 대상은 내부망 운영 VM 한 대다. 빈 VM 을 `bootstrap.sh` 로 1회 구성한 뒤, 이후 배포는 버전을 올려 `main` 에 머지하고 VM 에서 `deploy.sh vX.Y.Z` 를 실행하는 두 단계를 반복한다.

`deploy.sh` 는 cosign verify -> 그 태그의 compose fetch -> pull -> migration -> up -> `/health` 확인 순으로 진행하고, 실패하면 직전 정상 이미지로 되돌린다. 되돌리기도 이전 버전으로 같은 명령을 실행하면 된다.

절차·secret 배치·단일 호스트 수동 기동은 `docs/guides/deploy.md` 가 단일 진실이다.
