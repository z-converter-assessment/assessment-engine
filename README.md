# assessment-portal

온프레미스 서버 인벤토리를 수집·저장하는 B2B 내부 포털.

고객사 네트워크 내에 서버 엔진이 설치되고, 네트워크 내 각 서버의 **C 기반 에이전트**가 메트릭을 수집해 MQ에 직접 발행한다. Consumer가 메시지를 소비해 DB에 저장한다.

---

## 아키텍처

```
 Agent (C)
 각 서버에서 실행
 /proc 기반 수집
      │
      │ inventory / metrics / error
      ▼
 RabbitMQ ──────────────────── DLX / DLQ
 메시지 브로커                  nack · TTL 만료
 라우팅 · TTL
      │
      ▼
 Consumer (aio-pika)
 파싱 · 멱등성 · 저장
      │                  │
      ▼                  ▼
 TimescaleDB          Redis
 시계열 저장           캐시 · 온라인 상태
 hypertable           PUB/SUB
      ▲                  │
      │                  │ metrics.events
      └──── FastAPI ─────┘
            SSR · REST API · SSE
```

---

## 스택

| 구성 | 기술 |
|------|------|
| Query (SSR) | FastAPI + Jinja2 |
| Consumer | aio-pika (순수 비동기 컨슈머) |
| 메시지 브로커 | RabbitMQ |
| DB | TimescaleDB (PostgreSQL + SQLAlchemy async + asyncpg) |
| 캐시 / 온라인 상태 | Redis 7 |
| 에이전트 (별도 레포) | C 기반 바이너리 |

---

## 핵심 설계

- 에이전트는 `/proc` 기반 **raw 누적값**을 발행. CPU%·IOPS·kBps는 Web이 두 시점의 delta로 계산.
- 메트릭은 TimescaleDB hypertable에 시계열 저장. 온라인 상태는 Redis TTL(90s)로 판정.
- Consumer 저장 → Redis PUB/SUB → Web SSE → 브라우저 AJAX — 실시간 갱신 파이프라인.

---

## 사전 요구사항

**포털 서버 단독 실행**

| 환경 | 소프트웨어 | 버전 |
|------|-----------|------|
| macOS | [Docker Desktop](https://www.docker.com/products/docker-desktop/) | 4.x+ |
| Linux | [Docker Engine](https://docs.docker.com/engine/install/) + [Docker Compose](https://docs.docker.com/compose/install/) | Engine 27.x · Compose v2 |

**에이전트 포함 전체 환경 (추가)**

| 소프트웨어 | 버전 | 비고 |
|-----------|------|------|
| [VirtualBox](https://www.virtualbox.org/) | 7.1+ | Apple Silicon 포함 |
| [Vagrant](https://www.vagrantup.com/) | 2.4.x | |

---

## 환경변수

dev는 루트 `.env` 평문, prod는 `secrets/*` Docker secrets로 주입. 전체 키 목록은 [docs/env.md](docs/env.md), 정책·dev/prod 분리는 [docs/dev-prod.md](docs/dev-prod.md) 참조.

---

## IDE 로컬 환경 세팅 (선택)

런타임은 Docker 컨테이너에서 동작하지만, 자동완성·타입 체크를 위해 로컬 가상환경에 의존성을 설치한다.

**전제**: Python 3.12 이상 (`pyproject.toml`의 `requires-python = ">=3.12"`).
미설치 시 [python.org](https://www.python.org/downloads/) 또는 OS 패키지 매니저(`brew install python@3.12` / `apt install python3.12` 등).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install uv
uv pip install -e "."
```

---

## 실행

세 가지 시나리오: **Docker만 (dev)** / **Docker + Vagrant 풀 파이프라인 (dev)** / **prod**.
어떤 시나리오든 시작 전에 `.env` 부터 준비.

```bash
cp .env.example .env                       # 엔진 환경변수 (모든 시나리오 공통)
```

### A. Docker만 (포털 서버 단독, dev)

에이전트 없이 web/consumer/DB/MQ/Redis만 띄움. UI 확인·DB 접속 검증용.

```bash
# 기동 (docker-compose.override.yml 자동 적용 — APP_ENV=dev)
docker compose up --build -d

# 로그
docker compose logs -f web
docker compose logs -f consumer

# 종료 (데이터 유지)
docker compose down

# 종료 + 데이터 삭제 (postgres_data 볼륨 제거)
docker compose down -v
```

### B. Docker + Vagrant 풀 파이프라인 (dev)

VM 3대 + 에이전트까지 — 실제 메트릭 흐름 검증. 자세한 절차는 [docs/pipeline.md](docs/pipeline.md).

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

# 2. Alembic으로 schema 사전 적용 (lifespan은 prod에서 schema bootstrap skip)

# 3. 기동
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 종료
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
```

운영 체크리스트: [docs/dev-prod.md](docs/dev-prod.md) §10.

---

## 접속

| 주소 | 설명 |
|------|------|
| http://localhost:8000/servers/ | 서버 인벤토리 Web UI |
| http://localhost:8000/health | 헬스체크 |
| http://localhost:8000/docs | FastAPI Swagger UI |
| http://localhost:15672 | RabbitMQ 관리 콘솔 |
| localhost:5432 | PostgreSQL |

---

## 테스트

```bash
pip install -e ".[dev]"   # 최초 1회
python -m pytest          # 전체 (unit + integration)
```

실행 명령·설정·Fixture·테스트 작성 패턴은 [docs/testing.md](docs/testing.md).

---

## 파이프라인 검증 (Vagrant VM)

에이전트(C 바이너리) → RabbitMQ → Consumer → DB → Web UI 전체 파이프라인을 실제 VM 환경에서 검증한다.
Vagrant로 VM 3대(Ubuntu / Rocky Linux / Debian)를 띄우고, 각 VM에서 에이전트가 메트릭을 발행해 포털에 수집되는 것을 직접 확인한다.

기동·종료 명령은 위 [실행 §B](#b-docker--vagrant-풀-파이프라인-dev) 참조. 자세한 절차·VM 구성·트러블슈팅은 [docs/pipeline.md](docs/pipeline.md).

---

## 개발 문서

- [`docs/components/`](docs/components) — 컴포넌트별 설계·기술 구현 (agent·consumer·db·redis·web)
- [`docs/infra/`](docs/infra) — 인프라 구성 (Docker·Vagrant)
- [`docs/pipeline.md`](docs/pipeline.md) — 파이프라인 검증 절차 (Vagrant VM)
- [`docs/tradeoffs.md`](docs/tradeoffs.md) — 설계 선택으로 인한 트레이드오프 (멱등성·캐시 일관성·시계열 누적 등)
- [`docs/env.md`](docs/env.md) — 환경변수 전체 키 목록
- [`docs/dev-prod.md`](docs/dev-prod.md) — dev/prod 환경 전략 + secret 정책 + 운영 체크리스트
- [`docs/testing.md`](docs/testing.md) — 단위·통합 테스트 실행·설정·작성 패턴