# assessment-portal

온프레미스 서버 인벤토리를 수집·저장하는 B2B 내부 포털.

고객사 네트워크 내에 서버 엔진이 설치되고, 네트워크 내 각 서버의 **C99/C++03 기반 에이전트**가 메트릭을 수집해 MQ에 직접 발행한다. Consumer가 메시지를 소비해 DB에 저장한다.

---

## 아키텍처

```
 Agent (C99)
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
| 에이전트 (별도 레포) | C99/C++03 바이너리 |

---

## 핵심 설계

### 에이전트 메시지 스키마
3가지 routing key로 분기. 모든 메시지에 공통 메타데이터 포함.

| routing key | 용도 | TTL |
|-------------|------|-----|
| `server.inventory` | 기동 시 1회. 정적 인프라 정보 | 없음 |
| `server.metrics` | 1분 주기. raw 누적값 | 60s |
| `server.error` | 에이전트 수집·발행 실패 | 60s |

### TimescaleDB
메트릭은 `collected_at` 기준 hypertable로 파티셔닝. 4개 시계열 테이블 분리 저장.

| 테이블 | 설명 |
|--------|------|
| `server_inventory` | 정적 인벤토리. machine_id 기준 upsert |
| `server_metrics` | CPU·메모리·Load (hypertable) |
| `server_disk_io` | 장치별 IOPS (hypertable) |
| `server_net_io` | 인터페이스별 kBps (hypertable) |
| `server_mount_usage` | 마운트별 FS 사용량 (hypertable) |

에이전트는 raw 누적값을 발행하고, Web이 두 시점의 차(delta)로 CPU%·IOPS·kBps를 계산한다.

### Redis
| 역할 | 키 | TTL |
|------|-----|-----|
| 인벤토리 캐시 | `cache:inventory:{id}` | 300s |
| 메트릭 캐시 | `cache:metrics:{id}` | 60s |
| 온라인 상태 | `online:{id}` | 90s |
| 멱등성 | `idempotent:{message_id}` | 24h |
| 이벤트 버스 | `metrics.events` (PUB/SUB) | — |

Consumer가 메트릭 저장 시 `online:{id}` TTL을 갱신. 90초 안에 갱신이 없으면 자동 offline 판정.

### 실시간 갱신
Consumer 저장 → Redis `PUBLISH metrics.events` → Web SSE → 브라우저 AJAX `/metrics/latest` 재요청 → 대시보드 즉시 갱신.

---

## 사전 요구사항

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Docker Compose v2 포함)

---

## 환경변수 (루트 `.env`)

| 키 | 기본값 | 설명 |
|----|--------|------|
| `POSTGRES_HOST` | `postgres` | |
| `POSTGRES_DB` | `assessment` | |
| `POSTGRES_USER` | `assessment` | |
| `POSTGRES_PASSWORD` | `assessment` | |
| `POSTGRES_PORT` | `5432` | |
| `RABBITMQ_HOST` | `rabbitmq` | 컨슈머 접속용 |
| `RABBITMQ_USER` | `assessment` | |
| `RABBITMQ_PASSWORD` | `assessment` | |
| `RABBITMQ_PORT` | `5672` | |
| `RABBITMQ_MANAGEMENT_PORT` | `15672` | RabbitMQ 관리 콘솔 포트 |
| `RABBITMQ_EXCHANGE` | `assessment` | |
| `RABBITMQ_ROUTING_KEY_INVENTORY` | `server.inventory` | |
| `RABBITMQ_ROUTING_KEY_METRICS` | `server.metrics` | |
| `RABBITMQ_ROUTING_KEY_ERROR` | `server.error` | |
| `REDIS_PORT` | `6379` | |
| `WEB_PORT` | `8000` | |

---

## 실행

```bash
# 1. 환경변수 설정
cp .env.example .env

# 2. 실행
docker compose up --build -d

# 3. 로그 확인
docker compose logs -f web
docker compose logs -f consumer
```

```bash
# 종료 (데이터 유지)
docker compose down

# 종료 (데이터 삭제)
docker compose down -v
```

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

## IDE 로컬 환경 세팅 (선택)

런타임은 Docker 컨테이너에서 동작하지만, 자동완성·타입 체크를 위해 로컬 가상환경에 의존성을 설치한다.

```bash
python -m venv .venv
source .venv/bin/activate
pip install uv
uv pip install -e ".[test]"
```

---

## 테스트

→ [docs/TESTING.md](docs/TESTING.md)