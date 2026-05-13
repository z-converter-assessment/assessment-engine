# assessment-portal

온프레미스 서버 인벤토리·메트릭을 수집·저장하고, 수집된 데이터를 기반으로 자원 사용량을 진단해 운영 의사결정을 보조하는 B2B 내부 포털.

고객사 네트워크 내에 서버 엔진이 설치되고, 네트워크 내 각 서버의 C 기반 에이전트가 메트릭을 수집해 MQ에 직접 발행한다. Consumer가 메시지를 소비해 DB에 저장하고, 진단 워커가 수집된 데이터를 분석해 진단 결과를 생성한다(ADR 0004 — 진단 로직 상세는 미정, 인프라만 결정). 운영자는 web UI에서 대시보드·보고서·JSON Export 산출물을 활용해 다음 단계 의사결정을 진행한다.

---

## 아키텍처

```
 +------------------------------------------------------------------+
 |  Agent (C)                                                       |
 |  /proc collection + remote task execution                        |
 +-----+-----------------------------------------------+------------+
       |                                               ^
       | inventory / metrics / error / task.result     | task command
       | (4 routing keys, agent -> engine)             | (RPC piggyback
       v                                               |  via reply_to)
 +------------------------------------------------------------------+
 |  RabbitMQ                                                        |
 |  - 4 routing keys agent->engine + 1 routing key engine internal  |
 |    (diagnostic.request)                                          |
 |  - DLX (dead letter exchange) / DLQ (dead letter queue)          |
 +-----+-----------------------------------------------+------------+
       |                                               ^
       v                                               |
 +------------------------------------------------------------------+
 |  Consumer (aio-pika) + Diagnostic Worker (aio-pika, ADR 0004)    |
 |  - parse / idempotency / persist                                 |
 |  - time invariants / agent restart counter                       |
 |  - RPC piggyback (Redis pending task -> reply_to publish) -------+
 |  - diagnostic worker: LLM call (mock / ollama)                   |
 +-----+---------------------------------------+--------------------+
       v                                       v
 +----------------------------+    +----------------------------------+
 |  TimescaleDB               |    |  Redis                           |
 |  - 5 timeseries tables     |    |  - cache / online TTL            |
 |  - server_inventory + hist |    |  - pending task                  |
 |  - tasks (audit log)       |    |  - agent restart counter         |
 |  - diagnostic_jobs         |    |  - PUB/SUB metrics.events        |
 +----------------------------+    +----------------------------------+
       ^                                       |
       |                                       |
       +-------- FastAPI ----------------------+
                - SSR: dashboard / detail / report
                - REST: discovery / tasks / exports / chart / diagnostics
                - SSE: live metrics updates (Consumer PUB -> Redis -> SSE)
```

### 통신 패턴 용어

- RPC piggyback (Remote Procedure Call piggyback) — 별도 task 명령 큐·polling endpoint 없이 `server.metrics` 메시지의 `reply_to`에 명령 얹어 회신. reply 채널은 RabbitMQ 빌트인 `amq.rabbitmq.reply-to`. latency = metrics 주기. 흐름·트레이드오프: ADR 0002 + `docs/architecture/agent.md`.
- SSE (Server-Sent Events) — HTTP 단방향 server push (W3C 표준, `Content-Type: text/event-stream`). 흐름: Consumer DB 저장 → Redis `PUBLISH metrics.events` → Web SSE endpoint가 Redis `SUBSCRIBE` → 브라우저 event push → JS가 AJAX로 최신 데이터 fetch.
- Ollama — 로컬 LLM 런타임. 본 엔진 ADR 0004 — 진단 워커가 외부 유료 API 대신 같은 호스트/사내 GPU의 ollama HTTP 호출, 데이터 외부 유출 0. 현재 `LLM_PROVIDER=mock` 전용(`ollama` 분기 미구현).

---

## 스택

애플리케이션 (Python 3.12)

| 구성 | 기술 | 비고 |
|------|------|------|
| Web — SSR (Server-Side Rendering) + REST + SSE | FastAPI + Jinja2 + uvicorn | dev는 reload 모드 |
| Consumer | aio-pika (순수 비동기 컨슈머) | 4 routing key (agent -> engine) 소비 |
| Diagnostic Worker | aio-pika + LLM client (mock / ollama) | ADR 0004 — `diagnostic.request` 큐 소비 |
| Diagnostic Scheduler | croniter (cron 발화) | ADR 0004 — 주기 진단 job enqueue + retention DELETE |
| Migrate (init container) | Alembic | ADR 0005 — postgres healthy 후 1회 실행 후 종료, 앱 4종이 그 뒤 기동 |
| ORM / DB driver | SQLAlchemy async + asyncpg | |
| 로깅 | loguru | print/sys.stdout 금지 (#F7) |
| HTTP 클라이언트 | httpx | discovery probe 등 외부 HTTP 호출 |
| 패키지 매니저 | uv | pip 호환, 의존성 해결·설치 속도 |

인프라 / 데이터

| 구성 | 기술 | 비고 |
|------|------|------|
| 메시지 브로커 | RabbitMQ 3.13 (+ management UI) | DLX/DLQ, `amq.rabbitmq.reply-to` 빌트인 RPC piggyback |
| DB | TimescaleDB (PostgreSQL 16 + hypertable) | 5 시계열 테이블 + inventory + tasks + diagnostic_jobs |
| 캐시 / 온라인 상태 | Redis 7 | cache / pending task / restart counter / metrics.events PUB/SUB |
| 컨테이너 | Docker + docker-compose | dev override 자동 적용, prod는 명시 호출 (#A) |

배포 / 검증

| 구성 | 기술 | 비고 |
|------|------|------|
| dev 파이프라인 검증 VM | Lima + Apple Virtualization Framework (macOS) | 7 VM (Debian 12/13 · Ubuntu 24.04 · CentOS Stream 9 · openSUSE Leap 15 · Rocky 9 · AlmaLinux 9) + 시연 분류·attention 분포 (`docs/operations/lima.md`·`docs/operations/pipeline.md`) |
| OpenStack staging | Terraform + Ansible (vault 암호화) | 예상 시나리오 — 4 VM 분산(bastion + DB + MW + 앱) ADR 0006 (실제 도입 시점에 정정 의무, `deploy/openstack/` 전체 예상 설계) |
| 테스트 | pytest + pytest-asyncio + testcontainers + ruff | `docs/operations/testing.md` |

Frontend (정적 자원)

| 구성 | 기술 | 비고 |
|------|------|------|
| 차트 라이브러리 | Chart.js (CDN) | 번들러 미도입, IIFE 노출 (`docs/tradeoffs.md` T9) |
| JS 모듈화 | 외부 `.js` 파일 + `defer` 로드 | 인라인 `<script>` 신규 금지 (#E7·#F5) |
| 실시간 갱신 | SSE (Server-Sent Events) | Consumer PUB -> Redis -> Web SSE -> 브라우저 |

에이전트

| 구성 | 기술 | 비고 |
|------|------|------|
| 에이전트 (별도 레포) | C 기반 바이너리 | `/proc` raw 수집 + RabbitMQ 직접 publish (`assessment-agent` 레포) |

---

## 핵심 설계

### 데이터 수집·저장
- 에이전트는 `/proc` 기반 raw 누적값을 발행. CPU%·IOPS·kBps는 Web이 두 시점의 delta로 계산.
- 메트릭은 TimescaleDB hypertable에 시계열 저장. 온라인 상태는 Redis TTL(90s)로 판정.
- Consumer 저장 → Redis PUB/SUB → Web SSE → 브라우저 AJAX — 실시간 갱신 파이프라인.

### Counter reset 정밀 식별
- 시계열 4테이블에 `boot_time`·`agent_started_at` 컬럼 보존. 두 시점 boot_time 비교로 시스템 재부팅 시 delta 건너뛰기 (NULL fallback `d<0` 휴리스틱).
- Calculator(dashboard)와 차트 SQL 동일 정책 — SQL은 `LAG()` + `IS DISTINCT FROM` (NULL-safe 비교)으로 직전 row와 boot_time 차이 시 delta NULL 처리.
- Reboot/Restart 이벤트 차트 vertical marker로 운영 가시성.
- 상세 메커니즘: `docs/architecture/agent.md` "활용 중인 필드" + `docs/architecture/db/timescaledb.md` `_chart_*` 패턴.

### 운영 가시성·시그널
- 시계 invariant 로그 — `boot_time > agent_started_at` 또는 `agent_started_at > collected_at` 위반 시 warning (VM 시계 동기화 문제 조기 감지).
- 에이전트 재시작 카운터 — 1h 슬라이딩 윈도우, 임계값 초과 시 crash loop alert.

### 대시보드
- 환경 요약 — 총 N대 / 온라인·오프라인 / 자원 합계(vCPU·메모리·디스크) / 역할 분포 pill.
- 환경 평균 활용률 도넛 — CPU/메모리/디스크 14일 평균 사용률 (`recommendation.WINDOW_DAYS` 윈도우, 임계 색 분기 60·80%).
- 프로비저닝 분포 도넛 — 14일 측정값 기반 분류 3 카테고리(언더·정상·오버 프로비저닝) + 중앙 "언더 프로비저닝 N대" 강조.
- 주의 신호 카드 — 통신 끊김·디스크 사용률 임박·자원 부족(trigger 3종 활성/비활성)·디스크 잔여 30일·OS EOL·에이전트 재시작 빈번 6 카탈로그.
- 행별 권장 조치 컬럼 — 도넛 색과 동기 라벨(상향·축소·종료·조치 불필요).

### Assessment 산출물
- 서버 발견 — IP HTTP probe로 미등록 서버 도달성 검사 (Ansible 배포 워크플로우 1단계).
- JSON Export v3 — 선택 서버의 정제 inventory + 사용량 통계(p95·peak)를 OpenStack/Terraform/SDK 입력용 표준 JSON으로 다운로드. envelope에 `period_window` + `size_class_guide` 포함 — 자동화 도구 reproducibility.
- 보고서 (양식 A 고객용 / 양식 B 엔지니어용) — 측정값 기반 자원 사용 진단. 양식 A는 KPI + 위험도 요약, 양식 B는 15컬럼 정량 표 + 자동 진단 텍스트.
- ZConverter Install task — 선택 서버에 변환 도구 설치 명령 발행. RPC piggyback (`amq.rabbitmq.reply-to`)으로 에이전트가 다음 metrics 발행 시 명령 수신 -> 실행 -> `task.result` 큐로 결과 보고.

상세 정의: `docs/architecture/agent.md` "Task RPC piggyback" 절 + `docs/architecture/inventory-export.md` v3 스키마.

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

---

## 환경변수

dev는 루트 `.env` 평문, prod는 `secrets/*` Docker secrets로 주입. 전체 키 목록은 `docs/operations/env.md`, 정책·dev/prod 분리는 `docs/operations/dev-prod.md` 참조.

---

## IDE 로컬 환경 세팅 (선택)

런타임은 Docker 컨테이너에서 동작하지만, 자동완성·타입 체크를 위해 로컬 가상환경에 의존성을 설치한다.

전제: Python 3.12 이상 (`pyproject.toml`의 `requires-python = ">=3.12"`).
미설치 시 [python.org](https://www.python.org/downloads/) 또는 OS 패키지 매니저(`brew install python@3.12` / `apt install python3.12` 등).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install uv
uv pip install -e "."
```

---

## 실행

세 가지 시나리오: Docker만 (dev) / Docker + Lima 풀 파이프라인 (dev) / prod.
어떤 시나리오든 시작 전에 `.env` 부터 준비.

```bash
cp .env.example .env                       # 엔진 환경변수 (모든 시나리오 공통)
```

### A. Docker만 (포털 서버 단독, dev)

에이전트 없이 web/consumer/DB/MQ/Redis만 띄움. UI 확인·DB 접속 검증용.

```bash
# 기동 (docker-compose.override.yml 자동 적용 — APP_ENV=dev)
# postgres healthy → migrate(alembic upgrade head) 자동 → web/consumer/worker/scheduler 기동
docker compose up --build -d

# 로그
docker compose logs -f web
docker compose logs -f consumer
docker compose logs migrate          # 마이그레이션 적용 로그 (한 번만 실행 후 종료)

# 종료 (데이터 유지 — 다음 up 시 schema·데이터 그대로 복원)
docker compose down

# 종료 + 데이터 삭제 (postgres_data 볼륨 제거 — 완전 초기화)
docker compose down -v
```

처음 기동이면 첫 alembic upgrade가 모든 테이블·hypertable·extension을 자동 생성. 모델·마이그레이션 변경 후 재기동하면 변경분만 적용.

### B. Docker + Lima 풀 파이프라인 (dev)

Lima 7 VM + 에이전트까지 — 실제 메트릭 흐름 + 시연 분류 분포(over/optimal/under/insufficient_data) + attention 발화(agent_unstable·gap_warnings) 검증. 자세한 절차는 `docs/operations/lima.md` + `docs/operations/pipeline.md`.

```bash
cp infra/agent.env.example infra/agent.env  # 에이전트 secret 채널 (최초 1회)

./dev-up.sh    # docker compose up + web 헬스체크 + limactl start + agent install (7 VM)
./dev-down.sh  # limactl delete + docker compose down -v (LIMA_VMS 단일 진실 source)
```

### C. prod 기동 / D. OpenStack staging (예상 시나리오)

dev 집중 범위 초과 — 명령·체크리스트는 분기 위임:
- prod 기동(`secrets/*` + 명시 compose 호출): `docs/operations/dev-prod.md`
- OpenStack 분산 4 VM 배포: `docs/operations/scenarios/openstack.md` + `deploy/openstack/README.md` (ADR 0006, 실 도입 시 정정 의무)

---

## 데이터베이스 스키마 관리 (Alembic)

모든 환경 Alembic 마이그레이션 1개 진실. `docker compose up` 시 `migrate` 컨테이너가 `alembic upgrade head` 1회 실행 후 종료, 그 위에 앱 서비스 기동.

핵심:
- ORM 모델 변경 시 마이그레이션 파일 동시 작성 의무 (PR에 함께 commit). 한쪽만 올리면 다른 개발자 환경 깨짐.
- 새 마이그레이션: `docker compose run --rm migrate alembic revision --autogenerate -m "..."` → `migrations/versions/*.py` 검토 (hypertable·CREATE EXTENSION·partial index는 수동 `op.execute()` 보강) → 라운드트립 검증(`upgrade head` → `downgrade -1` → `upgrade head`).
- `alembic check` CI(`.github/workflows/alembic-check.yml`)가 drift 차단 — 모델만 바꾸고 마이그레이션 누락 시 PR reject.

상세 명령·워크플로·트러블슈팅: `docs/operations/alembic.md` (CLAUDE.md #C4).

---

## 접속

| 주소 | 설명 |
|------|------|
| http://localhost:8000/servers/ | 대시보드 Web UI (목록 / 활용률·프로비저닝 도넛 / 주의 신호 / 발견 / Install / Export / 보고서 진입점) |
| http://localhost:8000/servers/report?ids=...&view=customer&period_days=14 | 고객 보고서 (양식 A — KPI + 위험도 요약) |
| http://localhost:8000/servers/report?ids=...&view=engineer&period_days=14 | 엔지니어 보고서 (양식 B — 15컬럼 정량 + 자동 진단) |
| http://localhost:8000/health | 헬스체크 (shallow — 컨테이너 healthcheck용) |
| http://localhost:8000/docs | FastAPI Swagger UI (discovery·tasks·exports·diagnostics·chart 모든 endpoint) |
| http://localhost:8000/zconverter.tar.gz | Agent install bundle (mode=0o755, ADR 0002·deliverables.md) |
| http://localhost:15672 | RabbitMQ 관리 콘솔 |
| http://localhost:5050 | pgAdmin (dev override 전용 — DB GUI). server는 미리 등록되어 password만 입력 |
| localhost:5432 | PostgreSQL |

---

## 테스트

`.venv` 활성화 + dev extras (pytest·testcontainers·ruff) 설치 후 실행. 통합 테스트는 testcontainers가 TimescaleDB 컨테이너를 자동 spawn하므로 Docker daemon 필요.

```
source .venv/bin/activate
uv pip install -e ".[dev]"   # 최초 1회 (위 IDE 세팅과 같은 venv에 dev extras 추가)
python -m pytest             # 전체 (unit + integration)
python -m pytest tests/unit/ # 단위만 — DB 무관, 빠름 (~0.2s)
```

실행 명령·설정·Fixture·테스트 작성 패턴은 `docs/operations/testing.md`.

---

## 파이프라인 검증 (Lima VM)

에이전트(C) → RabbitMQ → Consumer → DB → Web UI 전체 파이프라인을 7 VM(Debian 12/13·Ubuntu 24.04·CentOS Stream 9·openSUSE Leap 15·Rocky 9·AlmaLinux 9) 실제 환경에서 검증 + 시연 분류·attention 분포 가시화.

진행 순서(시연 가시화 우선): web(`agent_unstable` 1m 후) → offline(`gap_warnings` 5m+) → app(under_provisioned, swap_trigger) → monitor(optimal) → mq·cache·db(over_provisioned).

기동·종료 명령은 위 [실행 B](#b-docker--lima-풀-파이프라인-dev). VM 매트릭스·합성 부하·누적 사고 패턴 단일 진실: `docs/operations/lima.md`. 운영자 절차 요약: `docs/operations/pipeline.md`.

---

## 개발 문서

- `.claude/CLAUDE.md` — 결정·원칙·금지 단일 진실
- `docs/architecture/` — 컴포넌트별 deep dive (agent·consumer·diagnostic·redis·rabbitmq·deliverables·inventory-export · db/* · web/*)
- `docs/operations/` — 인프라·환경·검증 단일 진실 (docker·lima·pipeline·env·alembic·testing·dev-prod·conventions·observability)
- `docs/operations/scenarios/` — 스코프 초과 예상 시나리오 (OpenStack 등)
- `docs/adr/` — Architecture Decision Records
- `docs/tradeoffs.md` — 의식적 설계 선택과 한계 (T1~T11)
- `docs/operations/env.md` — 환경변수 전체 키 목록