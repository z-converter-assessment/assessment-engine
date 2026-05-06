# 환경변수

루트 `.env`에서 주입. `.env.example`을 복사해 시작한다.

```bash
cp .env.example .env
```

## 전체 키 목록

| 키 | 기본값 | 설명 |
|----|--------|------|
| `POSTGRES_HOST` | `postgres` | PostgreSQL 호스트 (docker-compose 서비스명) |
| `POSTGRES_DB` | `assessment` | |
| `POSTGRES_USER` | `assessment` | |
| `POSTGRES_PASSWORD` | `assessment` | |
| `POSTGRES_PORT` | `5432` | |
| `RABBITMQ_HOST` | `rabbitmq` | 컨슈머 접속용 (docker-compose 서비스명) |
| `RABBITMQ_USER` | `assessment` | |
| `RABBITMQ_PASSWORD` | `assessment` | |
| `RABBITMQ_PORT` | `5672` | |
| `RABBITMQ_MANAGEMENT_PORT` | `15672` | RabbitMQ 관리 콘솔 포트 |
| `RABBITMQ_EXCHANGE` | `assessment` | |
| `RABBITMQ_ROUTING_KEY_INVENTORY` | `server.inventory` | |
| `RABBITMQ_ROUTING_KEY_METRICS` | `server.metrics` | |
| `RABBITMQ_ROUTING_KEY_ERROR` | `server.error` | |
| `REDIS_PORT` | `6379` | |
| `WEB_PORT` | `8000` | Web UI 접속 포트. 충돌 시 변경 |

## 주의사항

- 기본값의 호스트명(`postgres`, `rabbitmq`, `redis`)은 docker-compose 서비스명이다. 컨테이너 외부에서 접속할 때는 `localhost`로 변경해야 한다.
- `REDIS_HOST`는 `.env.example` 미기재. 기본값 `redis`가 docker-compose 환경과 일치하므로 생략.