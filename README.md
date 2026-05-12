# assessment-portal

온프레미스 서버 인벤토리·메트릭을 수집·저장하고, 수집된 데이터를 기반으로 자원 사용량을 진단해 운영 의사결정을 보조하는 B2B 내부 포털.

고객사 네트워크 내에 서버 엔진이 설치되고, 네트워크 내 각 서버의 C 기반 에이전트가 메트릭을 수집해 MQ에 직접 발행한다. Consumer가 메시지를 소비해 DB에 저장하고, 진단 워커가 USE Method 룰 + LLM(mock/ollama, ADR 0004)으로 서버별·환경 전체 right-sizing 분류와 자연어 진단 narrative를 생성한다. 운영자는 web UI에서 대시보드(활용률·프로비저닝 분포 도넛·주의 신호 카드)와 보고서(고객용 양식 A / 엔지니어용 양식 B)·JSON Export v3 산출물로 다음 단계 클라우드 마이그레이션 또는 right-sizing 결정을 진행한다.

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
                - SSR: dashboard / detail / USE Method report
                - REST: discovery / tasks / exports / chart / diagnostics
                - SSE: live metrics updates (Consumer PUB -> Redis -> SSE)
```

### 통신 패턴 용어

본 다이어그램에 등장하는 두 가지 약어는 풀네임을 풀면 다음과 같다.

- RPC piggyback (Remote Procedure Call piggyback — 원격 프로시저 호출 업혀가기)
  - 별도의 task 명령 큐나 polling endpoint를 만들지 않고, 에이전트가 주기적으로 발행하는 `server.metrics` 메시지의 `reply_to` 필드에 명령을 얹어 회신하는 방식.
  - reply 채널은 RabbitMQ 빌트인 pseudo-queue `amq.rabbitmq.reply-to`. 큐 선언·정리 불필요, broker 부하 0.
  - 흐름: 운영자 `POST /api/v1/tasks/install` -> DB `tasks` INSERT + Redis `task:pending:{machine_id}` SET -> 다음 `server.metrics` 도착 시 consumer가 Redis EXISTS 확인 후 `message.reply_to`로 명령 publish -> 에이전트 실행 -> `task.result` 큐로 결과 보고.
  - 트레이드오프: latency = metrics 주기(즉시 push 아님). 별도 polling endpoint나 task queue를 만들지 않는 대가 (ADR 0002).

- SSE (Server-Sent Events — 서버 전송 이벤트)
  - 브라우저가 HTTP 연결을 열어두고 서버가 단방향으로 이벤트를 push하는 W3C 표준 (WebSocket과 달리 양방향 아님, HTTP 위에서 동작).
  - 응답 헤더 `Content-Type: text/event-stream`로 FastAPI `StreamingResponse`가 송출.
  - 흐름: Consumer가 메트릭 저장 후 Redis `PUBLISH metrics.events {...}` -> Web의 SSE endpoint가 Redis `SUBSCRIBE`로 받음 -> 브라우저로 event push -> 페이지 JS가 AJAX로 최신 데이터 fetch (PUB/SUB는 트리거, 데이터 자체는 별도 fetch).

- Ollama (오라마) — 로컬 LLM(Large Language Model) 런타임
  - 오픈소스 Go 기반 도구. 한 바이너리에 모델 패키지 매니저 + inference 서버 + HTTP API가 다 들어있음. Docker가 컨테이너 런타임이듯 ollama는 LLM 런타임.
  - 사용 흐름: `ollama pull llama3.1:8b`로 모델 다운로드 -> `ollama serve`(또는 백그라운드 자동 실행)가 `localhost:11434`에서 HTTP API 제공 -> 앱은 `POST /api/generate` 같은 endpoint로 호출. CUDA·tokenizer를 직접 다룰 필요 없음.
  - GGUF(GPT-Generated Unified Format) 모델 지원 — Llama / Mistral / Gemma / Qwen / Phi 등. CPU만으로도 동작, GPU(CUDA·Apple Metal) 있으면 자동 가속. 8B 모델은 RAM 약 8GB 필요.
  - 본 엔진 활용 (ADR 0004): 진단 워커가 외부 유료 API(Anthropic·OpenAI 등) 대신 같은 호스트 또는 사내 GPU 머신의 ollama로 HTTP 호출. 데이터(서버 메트릭·hostname·IP) 외부 유출 0, 비용 0 — 운영자 정책 "과금 발생 외부 API 호출 금지" 충족.
  - `LLM_PROVIDER=ollama` 분기는 미구현(`NotImplementedError`) — `mock` 전용. 도입 시 ADR 0004.

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
| 컨테이너 | Docker + docker-compose | dev override 자동 적용, prod는 명시 호출 (#A2) |

배포 / 검증

| 구성 | 기술 | 비고 |
|------|------|------|
| dev 파이프라인 검증 VM | Vagrant + VirtualBox | Ubuntu / Rocky / Debian 3대 + 에이전트 발행 검증 (`docs/operations/pipeline.md`) |
| OpenStack staging | Terraform + Ansible (vault 암호화) | 4 VM 분산 — bastion + DB + MW + 앱 (ADR 0006) |
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
- 시계열 4테이블에 `boot_time` / `agent_started_at` 컬럼 보존. 두 시점의 boot_time 비교로 시스템 재부팅 시 delta 건너뛰기 (d<0 일 때, fallback).
- Calculator(dashboard)와 차트 SQL이 동일 정책 적용 — 차트 SQL은 PostgreSQL window function `LAG()` + `IS DISTINCT FROM` 조합 사용.
  - `LAG(컬럼) OVER (PARTITION BY ... ORDER BY collected_at)` — 같은 partition 안에서 직전 row의 컬럼 값을 가져오는 SQL 표준 window function. `LAG`는 "지연·뒤로"의 그 LAG이고 약어가 아님 (반대 함수는 `LEAD`).
  - `IS DISTINCT FROM` — NULL-safe 부등 비교. 일반 `<>`/`!=`는 한쪽이 NULL이면 결과도 NULL이라 조건문에서 false로 처리되지만, `IS DISTINCT FROM`은 NULL과 값을 다르다고 판정 (옛 데이터의 boot_time NULL fallback 처리).
  - 함께 쓰면: 현재 row의 `boot_time`이 직전 row의 `LAG(boot_time)`과 다른 시점이면 시스템 재부팅으로 판정 -> 해당 구간 delta 폐기.
- Reboot/Restart 이벤트 차트 vertical marker로 운영 가시성.

### 운영 가시성·시그널
- 시계 invariant 로그 — `boot_time > agent_started_at` 또는 `agent_started_at > collected_at` 위반 시 warning (VM 시계 동기화 문제 조기 감지).
- 에이전트 재시작 카운터 — 1h 슬라이딩 윈도우, 임계값 초과 시 crash loop alert.

### 대시보드
- 환경 요약 — 총 N대 / 온라인·오프라인 / 자원 합계(vCPU·메모리·디스크) / 역할 분포 pill.
- 환경 평균 활용률 도넛 — CPU/메모리/디스크 14일 평균 사용률 (`recommendation.WINDOW_DAYS` 윈도우, 임계 색 분기 60·80%).
- 프로비저닝 분포 도넛 — 14일 USE Method 분류 3 카테고리(언더·정상·오버 프로비저닝) + 중앙 "언더 프로비저닝 N대" 강조.
- 주의 신호 카드 — 통신 끊김·디스크 사용률 임박·자원 부족(trigger 3종 활성/비활성)·디스크 잔여 30일·OS EOL·에이전트 재시작 빈번 6 카탈로그.
- 행별 권장 조치 컬럼 — 도넛 색과 동기 라벨(상향·축소·종료·조치 불필요).

### Assessment 산출물
- 서버 발견 — IP HTTP probe로 미등록 서버 도달성 검사 (Ansible 배포 워크플로우 1단계).
- JSON Export v3 — 선택 서버의 정제 inventory + 사용량 통계(p95·peak)를 OpenStack/Terraform/SDK 입력용 표준 JSON으로 다운로드. envelope에 `period_window` + `size_class_guide` 포함 — 자동화 도구 reproducibility.
- 보고서 (양식 A 고객용 / 양식 B 엔지니어용) — Brendan Gregg USE Method + AWS Compute Optimizer / Azure Advisor / GCP Recommender 임계값 기반 right-sizing 분류. 양식 A는 KPI + 위험도 요약, 양식 B는 15컬럼 정량 표 + 자동 진단 텍스트.
- ZConverter Install task — 선택 서버에 변환 도구 설치 명령 발행. RPC piggyback (`amq.rabbitmq.reply-to`)으로 에이전트가 다음 metrics 발행 시 명령 수신 -> 실행 -> `task.result` 큐로 결과 보고.

상세 정의: `docs/architecture/agent.md` "Task RPC piggyback" 절 + `docs/architecture/inventory-export.md` v3 스키마.

---

## 사전 요구사항

포털 서버 단독 실행

| 환경 | 소프트웨어 | 버전 |
|------|-----------|------|
| macOS | [Docker Desktop](https://www.docker.com/products/docker-desktop/) | 4.x+ |
| Linux | [Docker Engine](https://docs.docker.com/engine/install/) + [Docker Compose](https://docs.docker.com/compose/install/) | Engine 27.x · Compose v2 |

에이전트 포함 전체 환경 (추가)

| 소프트웨어 | 버전 | 비고 |
|-----------|------|------|
| [VirtualBox](https://www.virtualbox.org/) | 7.1+ | Apple Silicon 포함 |
| [Vagrant](https://www.vagrantup.com/) | 2.4.x | |

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

세 가지 시나리오: Docker만 (dev) / Docker + Vagrant 풀 파이프라인 (dev) / prod.
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

### B. Docker + Vagrant 풀 파이프라인 (dev)

VM 3대 + 에이전트까지 — 실제 메트릭 흐름 검증. 자세한 절차는 `docs/operations/pipeline.md`.

```bash
cp infra/agent.env.example infra/agent.env  # 에이전트 secret 채널 (최초 1회)

./dev-up.sh    # docker compose up + web 헬스체크 + vagrant up
./dev-down.sh  # vagrant destroy + docker compose down -v
```

### C. prod 기동 (참고)

`secrets/*` 파일 + 명시적 compose 호출. dev override 자동 적용 안 됨.

```bash
# 1. secret 파일 작성 (강한 random)
printf '%s' "$(openssl rand -base64 32)" > secrets/postgres_password
printf '%s' "$(openssl rand -base64 32)" > secrets/rabbitmq_password
chmod 0400 secrets/postgres_password secrets/rabbitmq_password

# 2. 호스트 측 secret 사전 검증 (mode·git tracking·길이 — Layer 1)
./scripts/check-prod-secrets.sh

# 3. 기동 (migrate 서비스가 alembic upgrade head를 자동 실행 후 종료, 그 다음 web/others 기동)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 큰 schema 변경 전 미리 검토하고 싶으면 (선택):
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm migrate alembic history
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm migrate alembic current

# 종료
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
```

운영 체크리스트: `docs/operations/dev-prod.md` 10절.

### D. OpenStack staging (분산 3 VM)

사내 폐쇄망 OpenStack tenant에 분산 배포 (ADR 0006). bastion VM(수동 생성)에서 다음 실행:

```bash
cd deploy/openstack

# 사전 준비
cp terraform/terraform.tfvars.example terraform/terraform.tfvars   # 실 값 채움
cp ansible/group_vars/all/vault.yml.example ansible/group_vars/all/vault.yml
ansible-vault encrypt ansible/group_vars/all/vault.yml --vault-password-file ~/.vault-pass

export OS_CLOUD=assessment-engine                    # ~/.config/openstack/clouds.yaml의 cloud 키
export OPENSTACK_KEY_PATH=~/.ssh/openstack-key.pem

# 전체 배포 (인프라 + DB + MW + 앱)
./scripts/deploy.sh up

# 코드만 재배포
./scripts/deploy.sh update-app

# 전체 제거
./scripts/deploy.sh down
```

자세한 절차·트러블슈팅은 `deploy/openstack/README.md`.

---

## 데이터베이스 스키마 관리 (Alembic)

dev/staging/prod 모든 환경이 Alembic 마이그레이션 1개 진실로 schema를 관리한다. `docker compose up` 시 자동으로 `migrate` 컨테이너가 `alembic upgrade head`를 1회 실행하고 종료한 뒤, 그 결과 위에 web/consumer/worker/scheduler가 기동한다.

### 핵심 원칙

- ORM 모델 (`src/assessment_engine/db/models/*.py`)을 변경하면 반드시 마이그레이션 파일을 함께 만들어야 한다. dev 환경도 마이그레이션 없이는 schema가 갱신되지 않는다.
- 마이그레이션 파일은 `migrations/versions/<revision>_<설명>.py`. PR에 ORM 모델 변경과 함께 commit.
- 마이그레이션 파일은 `upgrade()` + `downgrade()` 양방향이어야 한다. autogenerate가 만든 downgrade도 반드시 검토.

### 일상 작업 흐름

A. 모델을 변경했을 때 — 새 마이그레이션 만들기

```bash
# 1) ORM 모델 (src/assessment_engine/db/models/*.py) 수정한 후

# 2) DB가 띄워진 상태에서 autogenerate로 마이그레이션 stub 생성
docker compose run --rm migrate alembic revision --autogenerate -m "add server_uuid column"

# 3) 생성된 migrations/versions/<revision>_*.py 파일 열어 검토
#    - 잘못 추론된 부분 수정 (autogenerate 한계)
#    - hypertable·CREATE EXTENSION·partial index·CHECK 제약 등은 수동 op.execute() 보강
#    - downgrade()도 확인

# 4) 마이그레이션 적용 (dev — up이 자동 실행하지만, 변경분만 즉시 적용하고 싶으면)
docker compose run --rm migrate alembic upgrade head

# 5) 라운드트립 검증 — 한 단계 내렸다가 다시 올려보고 깨지지 않는지 확인
docker compose run --rm migrate alembic downgrade -1
docker compose run --rm migrate alembic upgrade head

# 6) commit 시 (1) ORM 모델 (2) 마이그레이션 파일 함께 — 한쪽만 올리면 다른 개발자 환경이 깨진다
git add src/assessment_engine/db/models/ migrations/versions/
git commit -m "..."
```

B. 다른 개발자의 변경을 pull 받았을 때

```bash
git pull
docker compose up -d   # migrate 컨테이너가 새 마이그레이션 자동 적용
```

별도 명령 없음 — `up`만으로 schema가 최신화된다. 만약 위 자동 적용이 미덥지 않다면 `docker compose run --rm migrate alembic upgrade head`를 명시 실행.

C. 현재 상태 확인

```bash
docker compose run --rm migrate alembic current       # 현재 적용된 revision
docker compose run --rm migrate alembic history       # 마이그레이션 이력
docker compose run --rm migrate alembic heads         # 적용 가능한 head (branch 분기가 있는 경우만 의미)
docker compose run --rm migrate alembic check         # ORM 모델 vs 마이그레이션 drift 검출
```

`alembic check`는 CI에서도 돌아간다 (`.github/workflows/alembic-check.yml`). 모델만 바꾸고 마이그레이션을 안 만들면 PR이 막힌다.

### 안전 규약

- 절대 같은 `revision` ID를 두 개 만들지 말 것 (autogenerate가 처리하지만 수동 작성 시 주의).
- prod에 적용하기 전 staging에서 똑같은 마이그레이션을 먼저 적용해 검증.
- 마이그레이션 안에 `op.execute("DROP TABLE ...")` 같은 파괴적 SQL을 쓸 때는 데이터 백업 확인.
- 새 hypertable·extension·partial index는 autogenerate가 못 잡으므로 직접 `op.execute()` 작성.

자세한 운영 절차·트러블슈팅은 `docs/operations/alembic.md`.

---

## 접속

| 주소 | 설명 |
|------|------|
| http://localhost:8000/servers/ | 대시보드 Web UI (목록 / 활용률·프로비저닝 도넛 / 주의 신호 / 발견 / Install / Export / 보고서 진입점) |
| http://localhost:8000/servers/report?ids=...&view=customer&period_days=14 | 고객 보고서 (양식 A — KPI + 위험도 요약) |
| http://localhost:8000/servers/report?ids=...&view=engineer&period_days=14 | 엔지니어 보고서 (양식 B — 15컬럼 정량 + 자동 진단) |
| http://localhost:8000/health | 헬스체크 (#F14 shallow) |
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

## 파이프라인 검증 (Vagrant VM)

에이전트(C 바이너리) → RabbitMQ → Consumer → DB → Web UI 전체 파이프라인을 실제 VM 환경에서 검증한다.
Vagrant로 VM 3대(Ubuntu / Rocky Linux / Debian)를 띄우고, 각 VM에서 에이전트가 메트릭을 발행해 포털에 수집되는 것을 직접 확인한다.

기동·종료 명령은 위 [실행 B](#b-docker--vagrant-풀-파이프라인-dev) 참조. 자세한 절차·VM 구성·트러블슈팅은 `docs/operations/pipeline.md`.

---

## 개발 문서

### 시스템 설계
- `docs/architecture` — 컴포넌트별 deep dive
  - `agent.md` / `consumer.md` / `diagnostic.md` / `redis.md` / `rabbitmq.md`
  - `db/` — models / dtos / repositories / timescaledb
  - `web/` — layering / routers / services / view-models / static-assets
  - `deliverables.md` — 산출물 흐름 (서버 발견 / Install task / JSON Export v3 / 보고서 양식 A·B)
- `docs/operations` — 인프라·환경·배포 (docker·vagrant·openstack·dev-prod·env·alembic·testing·pipeline·automation-conventions)
- `docs/adr` — Architecture Decision Records + 트레이드오프

### 산출물·워크플로우 정의
- `docs/architecture/agent.md` "Task RPC piggyback" — ZConverter Install task 등록·실행 흐름 (ADR 0002)
- `docs/architecture/deliverables.md` — 4 산출물 흐름 통합 진입점

### 핵심 운영 가이드
- `docs/operations/pipeline.md` — Vagrant VM E2E 검증 절차
- `docs/operations/dev-prod.md` — dev/prod 분리 + secret 정책 + 운영 체크리스트
- `docs/operations/env.md` — 환경변수 전체 키 목록