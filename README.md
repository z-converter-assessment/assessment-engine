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

루트 `.env`에서 주입. 전체 키 목록과 주의사항은 [docs/ENV.md](docs/ENV.md) 참조.

---

## IDE 로컬 환경 세팅 (선택)

런타임은 Docker 컨테이너에서 동작하지만, 자동완성·타입 체크를 위해 로컬 가상환경에 의존성을 설치한다.

`assessment-engine/` 루트에서 실행한다.

```bash
python -m venv .venv
source .venv/bin/activate
pip install uv
uv pip install -e "."
```

---

## 실행

### Docker만 (포털 서버 단독)

`assessment-engine/` 루트에서 실행한다.

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

## 파이프라인 검증 (Vagrant VM)

에이전트(C 바이너리) → RabbitMQ → Consumer → DB → Web UI 전체 파이프라인을 실제 VM 환경에서 검증한다.
Vagrant로 VM 3대(Ubuntu / Rocky Linux / Debian)를 띄우고, 각 VM에서 에이전트가 메트릭을 발행해 포털에 수집되는 것을 직접 확인한다.

→ [docs/TESTING.md](docs/TESTING.md)