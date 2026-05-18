# assessment-portal

온프레미스 서버 인벤토리·메트릭을 수집·저장하고, 수집된 데이터를 기반으로 자원 사용량을 진단해 운영 의사결정을 보조하는 B2B 내부 포털.

고객사 네트워크 내에 서버 엔진이 설치되고, 네트워크 내 각 서버의 C 기반 에이전트가 메트릭을 수집해 MQ에 직접 발행한다. Consumer가 메시지를 소비해 DB에 저장하고, 진단 워커가 수집된 데이터를 규칙 기반으로 분석해 진단 결과를 생성한다. 운영자는 web UI 에서 대시보드·보고서·JSON Export·원격 설치 task 산출물을 활용해 다음 단계 의사결정을 진행한다.

본 repo는 엔진 자체(애플리케이션 + 동작 확인용 docker compose)만 다룬다. 배포 인프라(IaC — Terraform·Ansible·systemd unit 활용 등)는 본 repo 범위 밖. 본 repo가 제공하는 산출물·contract를 외부 인프라 코드에 통합해 운영한다.

---

## 아키텍처

```
 +------------------------------------------------------------------+
 |  Agent (C, separate repo: assessment-agent)                      |
 |  collector  : /proc scrape + inventory/metrics/error publish     |
 |  worker     : task.install consume + OS script exec + result    |
 +-----+----------------------------------------+-------------------+
       | inventory/metrics/error                ^ task.install
       | (server.* routing keys)                |
       v                                        |
 +------------------------------------------------------------------+
 |  RabbitMQ                                                        |
 |  - assessment exchange       : server.inventory/metrics/error    |
 |                                + diagnostic.request              |
 |  - assessment.tasks exchange : task.install.<machine_id>         |
 |                                + task.result -> worker.result    |
 |  - DLX/DLQ per exchange                                          |
 +--+----------------+-----------------+-----------------+----------+
    | server.*       | diagnostic.req  | task.result     ^ task.install
    v                v                 v                 | publish
 +-------------------------------+  +-------------------------------+
 |  Consumer (aio-pika)          |  |  Diagnostic (ADR 0004 + 0010) |
 |  - parse/idempot/persist      |  |  Worker:                      |
 |  - time invariants            |  |   - rule-based classify       |
 |  - agent restart signals      |  |   - narrate (no LLM call,     |
 |  - task.result -> Task        |  |     USE Method via            |
 |    row 6-column UPDATE        |  |     recommendation.py)        |
 |                               |  |  Scheduler:                   |
 |                               |  |   - cron tick                 |
 |                               |  |   - enqueue diag jobs         |
 |                               |  |   - retention DELETE          |
 +--------------+----------------+  +--------------+----------------+
                v                                  v
 +-------------------------------+  +-------------------------------+
 |  TimescaleDB                  |  |  Redis                        |
 |  - 5 timeseries tables        |  |  - cache / online TTL         |
 |  - server_inventory + history |  |  - idempotency SET NX         |
 |  - tasks (audit log)          |  |  - agent restart counter      |
 |  - diagnostic_jobs            |  |  - PUB/SUB metrics.events     |
 +--------------+----------------+  +--------------+----------------+
                ^                                  |
                |  read                            |  SUBSCRIBE
                |                                  |
 +------------------------------------------------------------------+
 |  FastAPI (uvicorn, port 8000)                                    |
 |  - SSR  : dashboard / detail / reports A,B / diagnostics         |
 |  - REST : discovery / tasks / exports / diagnostics              |
 |  - SSE  : live metrics (Consumer PUB -> Redis -> SSE)            |
 |  - /metrics : Prometheus scrape target (ADR 0011)                |
 |  - install bundle: /zconverter.tar.gz (agent worker fetch)       |
 |  - plain HTTP (dev) ; prod = external ingress (out of scope)     |
 +------------------------------------------------------------------+
```

상세 흐름: `docs/architecture/` (모듈별 deep dive).

---

## 스택

| 영역 | 기술 |
|------|------|
| 애플리케이션 | Python 3.12 · FastAPI · uvicorn · aio-pika · SQLAlchemy async · asyncpg · Jinja2 · loguru · httpx · croniter |
| DB / 캐시 / 브로커 | TimescaleDB (PostgreSQL 16) · Redis 7 · RabbitMQ 3.13 |
| Schema 관리 | Alembic 단일 진실 |
| 진단 | 규칙 기반 (USE Method) |
| 관측 | loguru `LOG_FORMAT=text\|json` + Prometheus `/metrics` |
| 패키징 | uv + hatchling. CI 산출물 = Python wheel |
| 정적 자원 | Chart.js CDN, 외부 `.js` 파일 + `defer` |
| 에이전트 (별도 repo) | C 단일 바이너리, RabbitMQ 직접 publish |

---

## CI 파이프라인

- git flow — `feature/*` → `dev` PR → merge → `main` PR → merge → release-please가 Release PR 자동 생성 → merge → `v*` tag (release-please bot 자동 push) → release.
- 사용자 push·tag 작성 없음 — 모두 GitHub Actions runner 안 자동. branch protection + Conventional Commits 강제.

| workflow | trigger | 검증·작업 |
|----------|---------|------|
| `pr-title-check.yml` | PR (target main/dev) opened·edited | PR title이 Conventional Commits 형식 (`feat:`·`fix:`·`docs:` 등) 강제 |
| `ci.yml` | PR (target main/dev) + push to main/dev (안전망) | ruff lint + hadolint → (pytest-unit + coverage + uv build wheel) → pytest-integration + coverage |
| `alembic-check.yml` | PR (target main/dev, paths: models·migrations·pyproject) + push 동일 paths (안전망) | ORM ↔ migrations 라운드트립 정합 |
| `codeql.yml` | PR · push to main/dev · 주간 cron | CodeQL SAST — SQL injection·secret leak·XSS 등 정적 분석 (Security 탭 alert) |
| `security.yml` | PR (paths: pyproject·uv.lock) · 주간 cron (Mon 09:00 UTC) | pip-audit dependency CVE 검사 |
| `auto-merge-dependabot.yml` | Dependabot PR | patch·minor update PR CI 통과 시 자동 merge (major는 운영자 manual) |
| `release-please.yml` | push to main | commit 분석 → Release PR 자동 생성·갱신 (pyproject.toml version bump + CHANGELOG.md). Release PR merge 시점에 tag(`v*`) 자동 push |
| `release.yml` | tag `v*` push | uv build wheel + sdist + SHA256SUMS + SBOM + Sigstore signature → GitHub Release 자동 첨부 |

본 repo는 CI 영역만 (코드 quality + artifact 생성). CD(배포·secret 주입·롤백)는 외부 인프라 책임.

---

## 배포 산출물

| 산출물 | 위치 · ref 문서 |
|--------|--------------|
| Python wheel + sdist + SHA256SUMS | GitHub Release (semver tag `v*`) · `docs/operations/release.md` |
| SBOM (CycloneDX JSON) + Sigstore signature | wheel·sdist에 첨부 — 외부 인프라가 의존성 audit + `cosign verify-blob` 무결성 검증 |
| Alembic migrations·alembic.ini | wheel 동봉 · `docs/operations/release.md` |
| 환경변수·secret contract | `docs/operations/env.md` · `docs/operations/prod-contract.md` |
| systemd unit reference | `docs/operations/deployment.md` 4절 |
| install·실행 절차 | `docs/operations/deployment.md` |

---

## 운영 산출물

| 산출물 | URL · ref 문서 |
|--------|--------------|
| 대시보드 | `/servers/` · `docs/products/dashboard.md` |
| 고객 보고서 (양식 A) | `/servers/report?ids=...&view=customer` · `docs/products/customer-report.md` |
| 엔지니어 보고서 (양식 B) | `/servers/report?ids=...&view=engineer` · `docs/products/engineer-report.md` |
| 환경 진단 (규칙 기반) | `/diagnostics` · `docs/products/environment-diagnostic.md` |
| 서버 진단 | `docs/products/server-diagnostic.md` |
| JSON Export | `/api/v1/exports/inventory` · `docs/products/json-export.md` |
| Install task | `docs/products/install-task.md` |

---

## Quick Start

본 절은 외부 인프라 코드 작성 전에 본 엔진의 동작·산출물을 1회 확인하기 위한 용도. 실제 prod 배포는 본 절 영역 밖.

전제: Docker 4.x+ (macOS Desktop 또는 Linux Engine 27.x + Compose v2).

엔진만 기동 (가장 단순):
```bash
cp .env.example .env
docker compose up --build -d        # web + consumer + diagnostic + DB + MQ + Redis 한 번에
docker compose down -v              # 종료 (데이터 삭제)
```

`migrate` 컨테이너가 alembic upgrade head를 자동 실행. 그 후 web 컨테이너가 헬스체크 통과하면 아래 접속 표의 endpoint 모두 동작.

엔진 + Lima VM 매트릭스 전체 시연 (macOS 한정 — 합성 부하·분류 분포 가시화):
```bash
./scripts/pipeline-up.sh                              # .env·dev/agent.env 자동 cp + Docker + Lima 7 VM
LIMA_VMS_FILTER=db-server-01,app-server-01 ./scripts/pipeline-up.sh  # 약식 (2 VM)
LIMA_VMS_FILTER=web-server-01 ./scripts/pipeline-up.sh               # 약식 (1 VM)
./scripts/pipeline-down.sh                            # 환경 전체 정리
```

상세: `docs/development/pipeline.md`.

---

## 개발 환경 셋업 (IDE 자동완성·테스트·로컬 실행)

본 절은 IDE (PyCharm·VS Code) 에서 코드 탐색·자동완성·테스트 실행을 위한 의존성 설치. Docker compose 만 띄울 때는 불필요 — 컨테이너 안에서 의존성을 갖고 있음.

전제: `uv` 0.4+ (`pip install uv` 또는 `brew install uv`).

```bash
# 의존성 동기화 — 운영 의존성 + dev 그룹 (pytest·ruff·hadolint·types 등) 모두 설치.
# pyproject.toml [dependency-groups].dev 가 dev 그룹 정의. uv 가 .venv/ 자동 생성.
uv sync --group dev

# IDE Python interpreter 를 본 .venv 로 지정:
#  - PyCharm: Settings → Project → Python Interpreter → Add Interpreter → Existing → .venv/bin/python
#  - VS Code: Cmd-Shift-P → "Python: Select Interpreter" → .venv/bin/python

# 테스트 (DB·Redis·broker 의존 없음 — testcontainers 가 자동 기동):
uv run pytest tests/unit/              # 단위 (DB 의존 0, 빠름)
uv run pytest tests/integration/       # 통합 (testcontainers 가 postgres/redis 자동 spawn)
uv run pytest                          # 전체

# 코드 quality:
uv run ruff check .                    # lint
uv run ruff format .                   # auto-format
uv run alembic check                   # ORM ↔ migrations 정합 (alembic-check.yml CI 와 동일)
```

`uv sync` 가 `.venv/` 안에 의존성 + 본 프로젝트 자체도 editable install — IDE 가 `src/assessment_engine/` 모듈 import 인식. dev 그룹 누락 시 IDE 가 pytest·ruff symbol 못 찾음 → 항상 `--group dev` 명시.

상세 (Docker 안 dev workflow·테스트 컨테이너·diagnostic mock LLM): `docs/development/`.

---

## 접속

dev 전체 endpoint가 plain HTTP port 8000. prod 외부 ingress 종단은 외부 인프라 책임.

| 주소 | 설명 |
|------|------|
| http://localhost:8000/servers/ | 대시보드 Web UI (목록 · 도넛 · 주의 신호 · 발견 · Install · Export · 보고서 · 최근 작업 진입점) |
| http://localhost:8000/servers/report?ids=...&view=customer&time_range=14d | 고객 보고서 (양식 A) |
| http://localhost:8000/servers/report?ids=...&view=engineer&time_range=14d | 엔지니어 보고서 (양식 B) |
| http://localhost:8000/reports/environment?view=customer&time_range=14d | 환경 보고서 (전체 등록 서버) |
| http://localhost:8000/reports/right-sizing-thresholds | Right-sizing 분류 임계값 참고자료 |
| http://localhost:8000/health | 헬스체크 |
| http://localhost:8000/metrics | Prometheus metrics — prod 외부 노출 금지 (reverse proxy internal-only) |
| http://localhost:8000/docs | FastAPI Swagger UI |
| http://localhost:8000/zconverter.tar.gz | Agent install bundle |
| http://localhost:15672 | RabbitMQ 관리 콘솔 |
| http://localhost:5050 | pgAdmin DB GUI |
| localhost:5432 | PostgreSQL |

---

## 문서

| 디렉토리 | 용도 |
|----------|------|
| `docs/development/` | 본 repo 안 dev 작업·코드 규약 (docker · pipeline · testing · conventions) |
| `docs/operations/` | 외부 인프라가 활용할 contract (release · deployment · env · prod-contract · alembic · observability · github-setup) |
| `docs/products/` | 운영 산출물 ref — 산출물별 의의·근거 (dashboard · 보고서 A/B · 환경/서버 진단 · JSON Export · Install task) |
| `docs/architecture/` | 컴포넌트별 deep dive (agent · consumer · diagnostic · broker · DB · web) |
| `docs/tradeoffs.md` | 의식적 설계 선택과 한계 |
