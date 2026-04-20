# assessment-portal

온프레미스 서버 인벤토리를 수집·저장하는 B2B 내부 포털.

## 사전 요구사항

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Docker Compose v2 포함)

---

## 스택

| 구성 | 기술 |
|------|------|
| Ingestion API | FastAPI |
| Query (SSR) | FastAPI + Jinja2 |
| Worker | taskiq + aio-pika |
| 메시지 브로커 | RabbitMQ |
| DB | PostgreSQL (SQLAlchemy async + asyncpg) |
| Agent | Alpine + bash (테스트용) |

---

## 실행

### 1. 환경변수 설정
```bash
cp .env.example .env
# POSTGRES_PASSWORD, RABBITMQ_PASS 값 변경
```

### 2. 메인 스택 실행
```bash
docker compose up --build -d
```

```bash
docker compose logs -f backend
docker compose logs -f worker
```

```bash
docker compose down      # 데이터 유지
docker compose down -v   # 데이터 삭제
```

### 3. 에이전트 실행
```bash
cd tools/agent
cp .env.example .env
# INGEST_API_URL 설정
docker compose up -d
```

```bash
docker compose logs -f agent
```

---

## 접속

| 주소 | 설명 |
|------|------|
| http://localhost:8000/servers/ | 서버 인벤토리 웹 UI |
| http://localhost:8000/docs | Swagger UI |
| http://localhost:8000/health | 헬스체크 |
| http://localhost:15672 | RabbitMQ 관리 콘솔 |
| localhost:5432 | PostgreSQL |

---

## Ingestion API

에이전트가 없을 때 수동으로 데이터를 Push할 수 있습니다.

```bash
curl -X POST http://localhost:8000/ingest/ \
  -H "Content-Type: application/json" \
  -d '{
    "hostname": "test-server",
    "nproc": "4",
    "mem_total_mb": 8192,
    "disks": [{"name": "sda", "size": "100G"}],
    "ip": {"internal": ["10.0.0.1"], "external": []}
  }'
```