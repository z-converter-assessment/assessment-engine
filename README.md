# assessment-portal

온프레미스 서버 인벤토리·메트릭을 수집·저장하고, 수집된 데이터를 기반으로 자원 사용량을 진단해 운영 의사결정을 보조하는 B2B 내부 포털.

고객사 네트워크 내에 서버 엔진이 설치되고, 네트워크 내 각 서버의 C 기반 에이전트가 메트릭을 수집해 MQ에 직접 발행한다. Consumer가 메시지를 소비해 DB에 저장하고, 진단 워커가 수집된 데이터를 분석해 진단 결과를 생성한다(ADR 0004 — 진단 인프라). 운영자는 web UI 에서 대시보드·보고서·JSON Export·원격 설치 task 산출물을 활용해 다음 단계 의사결정을 진행한다.

---

## 아키텍처

```
 +------------------------------------------------------------------+
 |  Agent (C, 별도 레포 assessment-agent)                            |
 |  collector  : /proc 수집 + inventory/metrics/error publish        |
 |  worker     : task.install consume + install.sh exec + result     |
 +-----+-----------------------------------+------------------------+
       | inventory/metrics/error           ^ task.install
       | (server.* routing keys)           |
       v                                   |
 +------------------------------------------------------------------+
 |  RabbitMQ                                                        |
 |  - assessment exchange         : server.inventory/metrics/error  |
 |  - assessment.tasks exchange   : task.install.<machine_id>       |
 |                                  + task.result -> worker.result  |
 |  - DLX/DLQ per exchange                                          |
 +-----+-----------------------------------+------------------------+
       | server.*                          ^ task.install publish
       v                                   |
 +------------------------------------------------------------------+
 |  Consumer (aio-pika)            Diagnostic Worker (ADR 0004)     |
 |  - parse / idempotency / persist                                 |
 |  - time invariants / agent restart counter                       |
 |  - task.result -> Task row 6 컬럼 UPDATE                         |
 +-----+---------------------------------------+--------------------+
       v                                       v
 +----------------------------+    +----------------------------------+
 |  TimescaleDB               |    |  Redis                           |
 |  - 5 timeseries tables     |    |  - cache / online TTL            |
 |  - server_inventory + hist |    |  - idempotency SET NX            |
 |  - tasks (audit log)       |    |  - agent restart counter         |
 |  - diagnostic_jobs         |    |  - PUB/SUB metrics.events        |
 +----------------------------+    +----------------------------------+
       ^                                       |
       |                                       |
       +-------- FastAPI (uvicorn 2-port) -----+
                - SSR     port 8000 (plain HTTP) : dashboard / detail / report
                - REST    port 8000              : discovery / tasks / exports / diagnostics
                - SSE     port 8000              : live metrics (Consumer PUB -> Redis -> SSE)
                - install port 8443 (HTTPS only) : /zconverter.tar.gz (agent worker fetch)
```

### 통신 패턴 용어

- 별도 큐 모델 (ADR 0007) — 원격 작업 명령을 `assessment.tasks` exchange 의 머신별 큐 (`agent.tasks.<machine_id>`) 로 발행, 결과는 `worker.result` 큐로 수신. 큐 declare 책임은 엔진 (web) 가 task 발행 시점에 동적 declare — 발행 측 worker 는 declare 권한 없음.
- 2-port 분리 (ADR 0008 임시) — install bundle endpoint (`/zconverter.tar.gz`) 만 HTTPS port 8443, 운영자 web UI·API·healthcheck 는 plain HTTP port 8000. 원격 호스트 worker 의 HTTPS-only 정책 정합. 정석 후속은 agent 측 dev http toggle 또는 nginx ingress sidecar (별도 ADR).
- SSE (Server-Sent Events) — HTTP 단방향 server push. Consumer DB 저장 -> Redis `PUBLISH metrics.events` -> Web SSE endpoint 가 Redis `SUBSCRIBE` -> 브라우저 event push -> JS 가 AJAX 로 최신 데이터 fetch.
- Ollama — 로컬 LLM 런타임. ADR 0004 — 진단 워커가 외부 유료 API 대신 같은 호스트/사내 GPU 의 ollama HTTP 호출, 데이터 외부 유출 0. 현재 `LLM_PROVIDER=mock` 전용 (ollama 분기 `NotImplementedError`).

---

## 스택

애플리케이션 (Python 3.12)

| 구성 | 기술 | 비고 |
|------|------|------|
| Web — SSR + REST + SSE | FastAPI + Jinja2 + uvicorn (2-port asyncio.gather) | dev reload. install bundle endpoint HTTPS 한정 (ADR 0008 임시) |
| Consumer | aio-pika 비동기 컨슈머 | 4 큐 소비 (server.inventory/metrics/error + worker.result) |
| Diagnostic Worker | aio-pika + LLM client (mock / ollama) | ADR 0004 — `diagnostic.request` 큐 소비. ollama 분기 미구현 |
| Diagnostic Scheduler | croniter (cron 발화) | ADR 0004 — 주기 진단 job enqueue + retention DELETE |
| Migrate (init container) | Alembic | ADR 0005 — postgres healthy 후 1회 실행 후 종료, 앱 4종이 그 뒤 기동 |
| ORM / DB driver | SQLAlchemy async + asyncpg | |
| 로깅 | loguru | print/sys.stdout 금지 (#F7) |
| HTTP 클라이언트 | httpx | discovery probe 등 외부 HTTP 호출 |
| 패키지 매니저 | uv | pip 호환, 의존성 해결·설치 속도 |

인프라 / 데이터

| 구성 | 기술 | 비고 |
|------|------|------|
| 메시지 브로커 | RabbitMQ 3.13 (+ management UI) | 2 exchange (assessment + assessment.tasks) + 2 DLX, dev plain AMQP / prod AMQPS (rabbitmq.md §3) |
| DB | TimescaleDB (PostgreSQL 16 + hypertable) | 5 시계열 + inventory + tasks + diagnostic_jobs. Task 테이블에 6 신규 컬럼 (failure_reason / exit_code / duration_ms / stdout_tail / stderr_tail) |
| 캐시 / 온라인 상태 | Redis 7 | cache / idempotency / agent restart counter / metrics.events PUB/SUB |
| 컨테이너 | Docker + docker-compose | dev override 자동 적용, prod 명시 호출 (#A) |
| TLS (dev install bundle) | openssl self-signed CA + server cert | `infra/tls/gen-cert.sh` (idempotent, 10년 유효). Lima VM truststore inject (Debian/RHEL 분기) |

배포 / 검증

| 구성 | 기술 | 비고 |
|------|------|------|
| dev 파이프라인 검증 VM | Lima + Apple Virtualization Framework (macOS) | 7 VM (Debian 12/13 · Ubuntu 24.04 · Rocky 9 · openSUSE Leap 15 · AlmaLinux 9) + 시연 분류·attention 분포 (`docs/operations/lima.md`·`docs/operations/pipeline.md`) |
| OpenStack staging | Terraform + Ansible (vault 암호화) | 예상 시나리오 — 4 VM 분산(bastion + DB + MW + 앱) ADR 0006 (실 도입 시점에 정정 의무, `deploy/openstack/` 전체 예상 설계) |
| 테스트 | pytest + pytest-asyncio + testcontainers + ruff | `docs/operations/testing.md`. 456 tests (unit + integration) |

Frontend (정적 자원)

| 구성 | 기술 | 비고 |
|------|------|------|
| 차트 라이브러리 | Chart.js (CDN) | 번들러 미도입, IIFE 노출 (`docs/tradeoffs.md` T9) |
| JS 모듈화 | 외부 `.js` 파일 + `defer` 로드 | 인라인 `<script>` 신규 금지 (#E7·#F5) |
| 실시간 갱신 | SSE (Server-Sent Events) | Consumer PUB -> Redis -> Web SSE -> 브라우저 |
| Task 운영 가시성 | base.html 단일 task modal + task-modal.js polling | list "최근 작업" column + detail timeline + Web API |

에이전트

| 구성 | 기술 | 비고 |
|------|------|------|
| 에이전트 (별도 레포) | C 기반 단일 바이너리 (collector + worker 통합 루프) | `/proc` raw 수집 + RabbitMQ 직접 publish + worker 가 task.install consume·install.sh exec (`assessment-agent` 레포) |

---

## 핵심 설계

### 데이터 수집·저장
- 에이전트는 `/proc` 기반 raw 누적값을 발행. CPU%·IOPS·kBps 는 Web 이 두 시점의 delta 로 계산.
- 메트릭은 TimescaleDB hypertable 에 시계열 저장. 온라인 상태는 Redis TTL(90s)로 판정.
- Consumer 저장 -> Redis PUB/SUB -> Web SSE -> 브라우저 AJAX — 실시간 갱신 파이프라인.

### Counter reset 정밀 식별
- 시계열 4테이블에 `boot_time`·`agent_started_at` 컬럼 보존. 두 시점 boot_time 비교로 시스템 재부팅 시 delta 건너뛰기 (NULL fallback `d<0` 휴리스틱).
- Calculator(dashboard)와 차트 SQL 동일 정책 — SQL 은 `LAG()` + `IS DISTINCT FROM` (NULL-safe 비교).
- Reboot/Restart 이벤트 차트 vertical marker.
- 상세: `docs/architecture/agent.md` "활용 중인 필드" + `docs/architecture/db/timescaledb.md` `_chart_*` 패턴.

### 운영 가시성·시그널
- 시계 invariant 로그 — `boot_time > agent_started_at` 또는 `agent_started_at > collected_at` 위반 시 warning (VM 시계 동기화 문제 조기 감지). `task.result` 메시지는 두 필드 null 이라 본 검증 생략.
- 에이전트 재시작 카운터 — 1h 슬라이딩 윈도우, 임계값 초과 시 crash loop alert.

### 대시보드
- 환경 요약 — 총 N대 / 온라인·오프라인 / 자원 합계(vCPU·메모리·디스크) / 역할 분포 pill.
- 환경 평균 활용률 도넛 — CPU/메모리/디스크 14일 평균 사용률 (`recommendation.WINDOW_DAYS` 윈도우, 임계 색 분기 60·80%).
- 프로비저닝 분포 도넛 — 14일 측정값 기반 분류 3 카테고리(언더·정상·오버 프로비저닝).
- 주의 신호 카드 — 통신 끊김·디스크 사용률 임박·자원 부족·디스크 잔여 30일·OS EOL·에이전트 재시작 빈번 6 카탈로그.
- 행별 권장 조치 + "최근 작업" column — install task badge (success/failure/pending) + 클릭 시 modal 로 stdout/stderr/failure_reason 디버깅.

### Assessment 산출물
- 서버 발견 — IP HTTP probe 로 미등록 서버 도달성 검사 (Ansible 배포 워크플로우 1단계).
- JSON Export v3 — 선택 서버의 정제 inventory + 사용량 통계(p95·peak) 를 OpenStack/Terraform/SDK 입력용 표준 JSON 으로 다운로드. envelope 에 `period_window` + `size_class_guide` 포함.
- 보고서 (양식 A 고객용 / 양식 B 엔지니어용) — 측정값 기반 자원 사용 진단. 양식 A 는 KPI + 위험도 요약, 양식 B 는 15컬럼 정량 표 + 자동 진단 텍스트.
- ZConverter Install task — 선택 호스트에 변환 도구 설치 명령 발행 (ADR 0007). engine web 이 `task.install` 메시지를 `agent.tasks.<machine_id>` 큐로 publish -> 원격 worker 가 HTTPS 다운로드 (sha256·size 검증) + `install.sh` 실행 + `task.result` 발행 -> Task row 6 컬럼 UPDATE. 운영자 가시성: list "최근 작업" column + detail timeline + `GET /api/v1/tasks/{id}` / `GET /api/v1/tasks?server_public_id=...&cursor=...`.

상세 정의: `docs/architecture/agent.md` "task.install" / "task.result" 절 + `docs/architecture/inventory-export.md` v3 스키마 + `docs/architecture/deliverables.md`.

---

## 사전 요구사항

포털 서버 단독 실행

| 환경 | 소프트웨어 | 버전 |
|------|-----------|------|
| macOS | [Docker Desktop](https://www.docker.com/products/docker-desktop/) | 4.x+ |
| Linux | [Docker Engine](https://docs.docker.com/engine/install/) + [Docker Compose](https://docs.docker.com/compose/install/) | Engine 27.x · Compose v2 |

에이전트 포함 전체 환경 (추가, macOS)

| 소프트웨어 | 버전 | 비고 |
|-----------|------|------|
| [Lima](https://lima-vm.io/) | 1.0+ | `brew install lima` (Apple Virtualization Framework / QEMU 백엔드) |
| openssl | 시스템 default | dev TLS cert 생성 (`infra/tls/gen-cert.sh`, macOS LibreSSL 호환) |

---

## 환경변수

dev 는 루트 `.env` 평문, prod 는 `secrets/*` Docker secrets 로 주입. 전체 키 목록은 `docs/operations/env.md`, 정책·dev/prod 분리는 `docs/operations/dev-prod.md` 참조.

---

## IDE 로컬 환경 세팅 (선택)

런타임은 Docker 컨테이너에서 동작하지만, 자동완성·타입 체크를 위해 로컬 가상환경에 의존성을 설치한다.

전제: Python 3.12 이상 (`pyproject.toml`의 `requires-python = ">=3.12"`).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install uv
uv pip install -e "."
```

---

## 실행

세 가지 시나리오: Docker 만 (dev) / Docker + Lima 풀 파이프라인 (dev) / prod.
어떤 시나리오든 시작 전에 `.env` 부터 준비.

```bash
cp .env.example .env                       # 엔진 환경변수 (모든 시나리오 공통)
```

### A. Docker 만 (포털 서버 단독, dev)

에이전트 없이 web/consumer/DB/MQ/Redis 만 띄움. UI 확인·DB 접속 검증용.

```bash
# 기동 (docker-compose.override.yml 자동 적용 — APP_ENV=dev + dev cert mount + HTTPS port 8443)
docker compose up --build -d

# 로그
docker compose logs -f web
docker compose logs -f consumer
docker compose logs migrate          # 마이그레이션 적용 로그 (한 번만 실행 후 종료)

# 종료 (데이터 유지)
docker compose down

# 종료 + 데이터 삭제
docker compose down -v
```

처음 기동이면 첫 alembic upgrade 가 모든 테이블·hypertable·extension 을 자동 생성. dev TLS cert 는 `infra/tls/gen-cert.sh` 가 첫 `./dev-up.sh` 호출 시 자동 생성 (Docker 단독 시나리오에서는 `bash infra/tls/gen-cert.sh` 수동 호출).

### B. Docker + Lima 풀 파이프라인 (dev)

Lima 7 VM + 에이전트까지 — 실제 메트릭 흐름 + 시연 분류 분포 + attention 발화 검증. 자세한 절차는 `docs/operations/lima.md` + `docs/operations/pipeline.md`.

```bash
cp infra/agent.env.example infra/agent.env  # 에이전트 secret 채널 (최초 1회)

./dev-up.sh    # cert auto-gen + docker compose up + web 헬스체크 + limactl start + agent install (7 VM)
./dev-down.sh  # limactl delete + docker compose down -v (LIMA_VMS 단일 진실 source)
```

agent v3.2 빌드 호환성 — Debian 12 OK. Debian 13 trixie / RHEL family (Rocky/AlmaLinux) 는 libcurl·glibc 차이로 빌드 실패 (`src/util.c` syscall implicit declaration / libcurl CURLOPT_PROTOCOLS_STR 7.85+ 의존). agent 측 호환성 작업 또는 `USE_VENDORED=1` 도입으로 별도 해결.

### C. prod 기동 / D. OpenStack staging (예상 시나리오)

dev 집중 범위 초과 — 명령·체크리스트는 분기 위임:
- prod 기동(`secrets/*` + 명시 compose 호출): `docs/operations/dev-prod.md`
- OpenStack 분산 4 VM 배포: `docs/operations/scenarios/openstack.md` + `deploy/openstack/README.md` (ADR 0006, 실 도입 시 정정 의무)

---

## 데이터베이스 스키마 관리 (Alembic)

모든 환경 Alembic 마이그레이션 1개 진실. `docker compose up` 시 `migrate` 컨테이너가 `alembic upgrade head` 1회 실행 후 종료.

핵심:
- ORM 모델 변경 시 마이그레이션 파일 동시 작성 의무.
- 새 마이그레이션: `docker compose run --rm migrate alembic revision --autogenerate -m "..."` -> `migrations/versions/*.py` 검토 -> 라운드트립 검증 (`upgrade head` -> `downgrade -1` -> `upgrade head`).
- `alembic check` CI 가 drift 차단.

상세: `docs/operations/alembic.md` (CLAUDE.md #C4).

---

## 접속

| 주소 | 프로토콜 | 설명 |
|------|---------|------|
| http://localhost:8000/servers/ | plain HTTP | 대시보드 Web UI (목록 / 도넛 / 주의 신호 / 발견 / Install / Export / 보고서 / 최근 작업 진입점) |
| http://localhost:8000/servers/report?ids=...&view=customer&period_days=14 | plain HTTP | 고객 보고서 (양식 A) |
| http://localhost:8000/servers/report?ids=...&view=engineer&period_days=14 | plain HTTP | 엔지니어 보고서 (양식 B) |
| http://localhost:8000/health | plain HTTP | 헬스체크 |
| http://localhost:8000/docs | plain HTTP | FastAPI Swagger UI (discovery·tasks·exports·diagnostics·chart 모든 endpoint) |
| https://localhost:8443/zconverter.tar.gz | HTTPS (self-signed) | Agent install bundle (mode=0o755). ADR 0008 임시 — agent worker HTTPS-only 정책 정합. 호스트 브라우저는 `--cacert infra/tls/ca.pem` 또는 macOS Keychain 등록 |
| http://localhost:15672 | plain HTTP | RabbitMQ 관리 콘솔 |
| http://localhost:5050 | plain HTTP | pgAdmin (dev override 전용 — DB GUI) |
| localhost:5432 | TCP | PostgreSQL |

---

## 테스트

`.venv` 활성화 + dev extras (pytest·testcontainers·ruff) 설치 후 실행. 통합 테스트는 testcontainers 가 TimescaleDB 컨테이너를 자동 spawn 하므로 Docker daemon 필요.

```
source .venv/bin/activate
uv pip install -e ".[dev]"   # 최초 1회
python -m pytest             # 전체 (unit + integration) — 현재 456 tests
python -m pytest tests/unit/ # 단위만 — DB 무관, 빠름
```

실행 명령·설정·Fixture·테스트 작성 패턴: `docs/operations/testing.md`.

---

## 파이프라인 검증 (Lima VM)

에이전트(C) -> RabbitMQ -> Consumer -> DB -> Web UI 전체 파이프라인을 7 VM(Debian 12/13·Ubuntu 24.04·Rocky 9·openSUSE Leap 15·AlmaLinux 9) 실제 환경에서 검증 + 시연 분류·attention 분포 가시화.

진행 순서(시연 가시화 우선): web(`agent_unstable` 1m 후) -> offline(`gap_warnings` 5m+) -> app(under_provisioned, swap_trigger) -> monitor(optimal) -> mq·cache·db(over_provisioned).

기동·종료 명령은 위 [실행 B](#b-docker--lima-풀-파이프라인-dev). VM 매트릭스·합성 부하·누적 사고 패턴: `docs/operations/lima.md`. 운영자 절차 요약: `docs/operations/pipeline.md`.

---

## 개발 문서

- `.claude/CLAUDE.md` — 결정·원칙·금지 단일 진실
- `docs/architecture/` — 컴포넌트별 deep dive (agent·consumer·diagnostic·redis·rabbitmq·deliverables·inventory-export · db/* · web/*)
- `docs/operations/` — 인프라·환경·검증 단일 진실 (docker·lima·pipeline·env·alembic·testing·dev-prod·conventions·observability)
- `docs/operations/scenarios/` — 스코프 초과 예상 시나리오 (OpenStack 등)
- `docs/adr/` — Architecture Decision Records (0001~0008)
- `docs/tradeoffs.md` — 의식적 설계 선택과 한계 (T1~T11)
- `docs/operations/env.md` — 환경변수 전체 키 목록
