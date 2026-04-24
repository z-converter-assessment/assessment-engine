# assessment-portal

온프레미스 서버 인벤토리를 수집·저장하는 B2B 내부 포털.

고객사 네트워크 내에 서버 엔진이 설치되고, 네트워크 내 각 서버의 **C99/C++03 기반 에이전트**가 메트릭을 수집해 MQ에 직접 발행한다. Consumer가 메시지를 소비해 DB에 저장한다.

## 사전 요구사항

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Docker Compose v2 포함)

---

## 스택

| 구성 | 기술 |
|---|---|
| Query (SSR) | FastAPI + Jinja2 |
| Consumer | aio-pika (순수 비동기 컨슈머) |
| 메시지 브로커 | RabbitMQ |
| DB | PostgreSQL (SQLAlchemy async + asyncpg) |
| 실제 에이전트 | C99/C++03 바이너리 (MQ 직접 발행) |

---

## 실행

### 1. 환경변수 설정
```bash
cp .env.example .env
```

### 2. 메인 스택 실행
```bash
# 실행
docker compose up --build -d
```

```bash
# web 로그
docker compose logs -f web
```
```bash
# consumer 로그
docker compose logs -f consumer
```

```bash
# 종료 [데이터 유지]
docker compose down
```
```bash
# 종료 [데이터 삭제]
docker compose down -v
```

---

## 모듈·통합 테스트

테스트 의존성은 운영 이미지에 포함되지 않으므로 로컬 가상환경에서 실행한다.

### 1. 가상환경 준비 (최초 1회)

```bash
python3 -m venv .venv
```
```bash
source .venv/bin/activate
```
```
pip install -e '.[test]'
```

### 2. 단위 테스트 (Docker 불필요)

```bash
pytest tests/unit
```

### 3. 통합 테스트 (Docker 필요 — testcontainers가 PostgreSQL 컨테이너를 자동으로 띄움)

```bash
pytest tests/integration
```

### 4. 전체 실행

```bash
pytest
```

---

## 환경변수 (루트 `.env`)

| 키 | 기본값 | 설명 |
|---|---|---|
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
| `RABBITMQ_ROUTING_KEY_INVENTORY` | `server.inventory` | 에이전트 ↔ 컨슈머 계약 |
| `RABBITMQ_ROUTING_KEY_METRICS` | `server.metrics` | 에이전트 ↔ 컨슈머 계약 |
| `RABBITMQ_ROUTING_KEY_ERROR` | `server.error` | 에이전트 ↔ 컨슈머 계약 |
| `WEB_PORT` | `8000` | |

---

## 접속

| 주소 | 설명 |
|---|---|
| http://localhost:8000/servers/ | 서버 인벤토리 웹 UI |
| http://localhost:8000/docs | FastAPI Swagger UI |
| http://localhost:8000/redoc | FastAPI ReDoc |
| http://localhost:15672 | RabbitMQ 관리 콘솔 |
| localhost:5432 | PostgreSQL |