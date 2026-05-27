# assessment-engine

온프레미스 서버 인벤토리·메트릭을 수집·저장하고, 수집된 데이터를 기반으로 자원 사용량을 진단해 운영 의사결정을 보조하는 B2B 내부 포털.

고객사 네트워크 내에 서버 엔진이 설치되고, 네트워크 내 각 서버의 C 기반 에이전트가 메트릭을 수집해 MQ에 직접 발행한다. Consumer가 메시지를 소비해 DB에 저장하고, 진단 워커가 수집된 데이터를 규칙 기반으로 분석해 진단 결과를 생성한다. 운영자는 web UI 에서 대시보드·보고서·JSON Export·원격 설치 task 산출물을 활용해 다음 단계 의사결정을 진행한다.

본 repo 는 엔진 자체 (애플리케이션 + dev 시연용 docker compose · OrbStack 매트릭스 등 `dev/` 격리 자산) 만 다룬다. 배포 인프라 (IaC — Terraform · Ansible · SaltStack 등) 와 prod 운영 (systemd unit · k8s manifest 등) 은 본 repo 범위 밖. 본 repo 가 제공하는 산출물·contract 를 외부 인프라 코드에 통합해 운영한다.

---

## 아키텍처

```
 +------------------------------------------------------------------+
 |  Agent (C, separate repo: assessment-agent)                      |
 |  collector  : /proc scrape + inventory/metrics/error publish     |
 |  worker     : task.install consume + OS script exec + result     |
 +-----+----------------------------------------+-------------------+
       | inventory/metrics/error                ^ task.install
       | (server.* routing keys)                |
       v                                        |
 +------------------------------------------------------------------+
 |  RabbitMQ                                                        |
 |  - assessment exchange       : server.inventory/metrics/error    |
 |                                + diagnostic.request              |
 |  - assessment.tasks exchange : task.install.<composite_id>    |
 |                                + task.result -> worker.result    |
 |  - DLX/DLQ per exchange                                          |
 +--+----------------+-----------------+-----------------+----------+
    | server.*       | diagnostic.req  | task.result     ^ task.install
    v                v                 v                 | publish
 +-------------------------------+  +-------------------------------+
 |  Consumer (aio-pika)          |  |  Diagnostic (ADR 0004 + 0010  |
 |  - parse/idempot/persist      |  |          + 0023 + 0024 + 0025)|
 |  - time invariants            |  |  Worker:                      |
 |  - agent restart signals      |  |   - rule-based classify       |
 |  - task.result -> Task        |  |   - retrieve RAG context      |
 |    row 6-column UPDATE        |  |     (pgvector, opt-in)        |
 |                               |  |   - narrate (USE Method via   |
 |                               |  |     recommendation.py +       |
 |                               |  |     RAG-grounded LLM)         |
 |                               |  |  Trigger: web POST only       |
 |                               |  |   (no cron, ADR 0023)         |
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
 |  - SSR  : dashboard / detail / env+server report + history       |
 |  - REST : discovery / tasks / exports / diagnostics (poll)       |
 |  - SSE  : live metrics (Consumer PUB -> Redis -> SSE)            |
 |  - /metrics : Prometheus scrape target (ADR 0011)                |
 |  - plain HTTP (dev) ; prod = external ingress (out of scope)     |
 +------------------------------------------------------------------+
```

상세 흐름: `docs/architecture/` (모듈별 deep dive).

---

## 스택

| 영역 | 기술 |
|------|------|
| 애플리케이션 | Python 3.12 · FastAPI · uvicorn · aio-pika · SQLAlchemy async · asyncpg · Jinja2 · loguru · httpx |
| DB / 캐시 / 브로커 | TimescaleDB (PostgreSQL 16) · Redis 7 · RabbitMQ 3.13 · pgvector (ADR 0024) |
| Schema 관리 | Alembic 단일 진실 |
| 진단 | 규칙 기반 (USE Method) + 단일 ollama LLM (ADR 0025) + RAG opt-in (ADR 0024, mxbai-embed-large + HNSW) |
| 관측 | loguru `LOG_FORMAT=text\|json` + Prometheus `/metrics` |
| 패키징 | uv + hatchling. CI 산출물 = Python wheel |
| 정적 자원 | Chart.js CDN, 외부 `.js` 파일 + `defer` |
| 에이전트 (별도 repo) | C 단일 바이너리, RabbitMQ 직접 publish |

---

## CI 파이프라인

- git flow — `feature/*`·`fix/*` → `develop` PR(squash) → `develop` → `main` PR(merge) → `main`에 `v*` tag push → release (ADR 0030).
- 버전은 git tag 단일 진실 (hatch-vcs가 빌드 시 derive) — repo에 버전 미저장, bump 커밋 없음. branch protection + Conventional Commits PR title 강제.

| workflow | trigger | 검증·작업 |
|----------|---------|------|
| `pr-title-check.yml` | PR (target main/develop) opened·edited | PR title이 Conventional Commits 형식 (`feat:`·`fix:`·`docs:` 등) 강제 |
| `ci.yml` | develop PR · main PR · develop push | lint(ruff+hadolint) → test-unit → test-integration (develop push·main PR), wheel build (main PR) |
| `alembic-check.yml` | develop PR · main PR | ORM·migrations 라운드트립 정합 |
| `codeql.yml` | main PR · 주간 cron | CodeQL SAST (SQL injection·secret leak·XSS 정적 분석, Security 탭 alert) |
| `security.yml` | main PR · 주간 cron (Mon 09:00 UTC) | pip-audit dependency CVE 검사 |
| `release.yml` | `main`에 tag `v*` push · workflow_dispatch | uv build wheel + sdist (버전=tag, hatch-vcs) + SHA256SUMS + SBOM + Sigstore signature → GitHub Release + GHCR image(multi-arch) 자동 첨부 |

본 repo는 CI 영역만 (코드 quality + artifact 생성). CD(배포·secret 주입·롤백)는 외부 인프라 책임.

---

## 배포 산출물

운영자 토폴로지 자율 선택 — wheel (systemd · venv) 또는 Docker image (docker · k8s) 어느 채널이든 즉시 사용 가능.

| 산출물 | 위치 · 참고 문서 |
|--------|--------------|
| Python wheel + sdist + SHA256SUMS | GitHub Release (semver tag `v*`) · `docs/operations/release.md` |
| Docker image (multi-arch `amd64,arm64`) | GHCR `ghcr.io/{org}/assessment-engine:v*`+`:0.1`+`:0`+`:latest` · ADR 0017 |
| SBOM (CycloneDX JSON) + Sigstore signature | wheel·sdist에 첨부 — 외부 인프라가 의존성 audit + `cosign verify-blob` 무결성 검증 |
| SBOM (SPDX, BuildKit attestation) + cosign keyless signature | image 첨부 — `cosign verify ghcr.io/.../assessment-engine:v0.1.0` 무결성 검증 |
| Alembic migrations·alembic.ini | wheel·image 동봉 (`hatch.force-include`) · `docs/operations/release.md` |
| 환경변수·secret contract | `docs/operations/env.md` |
| systemd unit reference | `docs/operations/deployment.md` 4절 |
| install·실행 절차 | `docs/operations/deployment.md` |

---

## 설치·실행 (prod)

wheel 하나가 3개 실행 모듈을 제공한다 (각각 별도 프로세스 — stateless, 같은 host 또는 분리·복제 N개):

- `python -m assessment_engine.web` — 포털 UI·REST·SSE·`/metrics` (HTTP :8000). 필수.
- `python -m assessment_engine.consumer` — inventory/metrics/error·task.result 소비·저장. 필수.
- `python -m assessment_engine.diagnostic` — engineer 보고서 narrative 합성. engineer AI 보고서 사용 시만 (ollama 도달 필요).

전제: Python 3.12+ / PostgreSQL 16 (`timescaledb`+`vector`) / RabbitMQ 3.13+ / Redis 7+ — 외부 인프라가 준비, 엔진은 도달만 전제.

단일 host 설치 (multi-node·Docker image·업그레이드·트러블슈팅은 `docs/operations/deployment.md`):

1) Release(`v*`) 받아 무결성 검증:
```bash
gh release download v0.1.2 -R <org>/assessment-engine -D /tmp/ae
cd /tmp/ae && sha256sum -c SHA256SUMS
```

2) venv + install:
```bash
sudo install -d -o "$USER" -g "$USER" /opt/assessment-engine
python3.12 -m venv /opt/assessment-engine/venv
/opt/assessment-engine/venv/bin/pip install /tmp/ae/assessment_engine-*.whl
```

3) `/etc/assessment-engine.env` 작성 (전체 키: `.env.example`·`docs/operations/env.md`):
```ini
APP_ENV=prod
LOG_FORMAT=json
POSTGRES_HOST=db.internal
POSTGRES_DB=assessment
POSTGRES_USER=assessment_app
POSTGRES_PASSWORD=<secret>
REDIS_HOST=redis.internal
RABBITMQ_HOST=mq.internal
RABBITMQ_USER=assessment_app
RABBITMQ_PASSWORD=<secret>
OLLAMA_BASE_URL=http://ollama.internal:11434
OLLAMA_MODEL=llama3.1:8b
```

4) DB 마이그레이션 1회 (멱등, wheel 동봉 alembic 설정):
```bash
set -a; . /etc/assessment-engine.env; set +a
V=/opt/assessment-engine/venv/bin/python
INI=$($V -c 'from importlib.resources import files; print(files("assessment_engine")/"_alembic.ini")')
$V -m alembic -c "$INI" upgrade head
```

5) systemd 영속화 — `/etc/systemd/system/assessment-engine-web.service`:
```ini
[Unit]
Description=Assessment Engine (web)
After=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/assessment-engine.env
ExecStart=/opt/assessment-engine/venv/bin/python -m assessment_engine.web
Restart=always
RestartSec=5s
KillSignal=SIGTERM
TimeoutStopSec=30s

[Install]
WantedBy=multi-user.target
```
consumer·diagnostic-worker unit은 `ExecStart` 모듈만 `assessment_engine.consumer`/`assessment_engine.diagnostic`로 교체. 등록:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now \
  assessment-engine-web assessment-engine-consumer assessment-engine-diagnostic-worker
```

6) 헬스 확인:
```bash
curl -fsS http://localhost:8000/health   # {"status":"ok"}
```

`APP_ENV=prod`은 약한 기본값(`assessment` 등)을 기동 시 거부(fail-fast). 환경변수 전체·secret 채널: `docs/operations/env.md`.

---

## 운영 산출물

| 산출물 | URL · 참고 문서 |
|--------|--------------|
| 대시보드 | `/servers/` · `docs/products/dashboard.md` |
| 환경 보고서 (보고서 + 환경 진단 통합) | `/reports/environment?view=customer\|engineer` · `docs/products/environment-report.md` |
| 서버 보고서 (보고서 + 서버 진단 통합) | `/servers/report?ids=...&view=customer\|engineer` · `docs/products/server-report.md` |
| JSON Export | `/api/exports/inventory` · `docs/products/json-export.md` |
| Install task | `docs/products/install-task.md` |

---

## Quick Start

dev 시연 · 파이프라인 검증 흐름 (엔진 dev compose + OrbStack 4 VM 매트릭스) 과 접속 endpoint 카탈로그는
`dev/README.md` 단일 진실. 루트는 운영 기준 메타·산출물만 유지.

VM 매트릭스 · 합성 부하 프로파일 · attention 발화 매핑 deep dive: `docs/development/pipeline.md`.

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
uv run alembic check                   # ORM·migrations 정합 (alembic-check.yml CI 와 동일)
```

`uv sync` 가 `.venv/` 안에 의존성 + 본 프로젝트 자체도 editable install — IDE 가 `src/assessment_engine/` 모듈 import 인식. dev 그룹 누락 시 IDE 가 pytest·ruff symbol 못 찾음 → 항상 `--group dev` 명시.

상세 (Docker 안 dev workflow·테스트 컨테이너·diagnostic ollama LLM): `docs/development/`.

---

## 문서

| 디렉토리 | 용도 |
|----------|------|
| `docs/README.md` | 카테고리·파일 인덱스 — 어떤 문서를 언제 보는지 길잡이 |
| `docs/development/` | 본 repo 안 dev 작업·코드 규약 (docker · dependencies · pipeline · testing · conventions) |
| `docs/operations/` | 외부 인프라가 활용할 contract (release · deployment · env · alembic · observability) |
| `docs/products/` | 운영 산출물 의의·근거 (dashboard · 환경 보고서 · 서버 보고서 · JSON Export · Install task) |
| `docs/architecture/` | 컴포넌트별 deep dive (agent · consumer · diagnostic · rabbitmq · redis · db · web) |
| `docs/adr/` | Architecture Decision Records (0001~) — "왜 이렇게 결정했나" + 트레이드오프 |
| `docs/tradeoffs.md` | 의식적 설계 선택과 한계 (T1~T14) |
