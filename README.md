# assessment-portal

온프레미스 서버 인벤토리를 수집·저장하는 B2B 내부 포털.

고객사 네트워크 내에 서버 엔진이 설치되고, 네트워크 내 각 서버의 **C99/C++03 기반 에이전트**가 메트릭을 수집해 MQ에 직접 발행한다. Consumer가 메시지를 소비해 DB에 저장한다.

## 사전 요구사항

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Docker Compose v2 포함)

---

## 스택

| 구성 | 기술 |
|------|------|
| Query (SSR) | FastAPI + Jinja2 |
| Consumer | aio-pika (순수 비동기 컨슈머) |
| 메시지 브로커 | RabbitMQ |
| DB | PostgreSQL (SQLAlchemy async + asyncpg) |
| 실제 에이전트 | C99/C++03 바이너리 (MQ 직접 발행) |
| 테스트 시뮬레이터 | `tools/agent/` (MQ 직접 발행 검증용) |

---

## 실행

### 1. 환경변수 설정
```bash
cp .env.example .env
```

### 2. 메인 스택 실행
```bash
docker compose up --build -d
```

```bash
# web 로그
docker compose logs -f web

# consumer 로그
docker compose logs -f consumer
```

```bash
# 종료 [데이터 유지]
docker compose down

# 종료 [데이터 삭제]
docker compose down -v
```

---

## 테스트

실제 에이전트(C99 바이너리) 대신 컨테이너로 메트릭 수집·발행을 검증한다.  
메인 스택(`docker compose up`)이 실행 중인 상태에서 진행한다.

### 1. 시뮬레이터 환경변수 설정
```bash
cd tools/agent
cp .env.example .env
```

`.env` 주요 항목:

| 키 | 기본값 | 설명 |
|----|--------|------|
| `RABBITMQ_HOST` | `host.docker.internal` | 메인 스택 RabbitMQ 주소 |
| `RABBITMQ_USER` | `assessment` | |
| `RABBITMQ_PASS` | `assessment` | |
| `RABBITMQ_ROUTING_KEY` | `metric` | 메인 스택 큐 이름과 일치해야 함 |
| `PUBLISH_INTERVAL_SEC` | `60` | 발행 주기 (초) |
| `EXTERNAL_IP` | `111.111.111.111` | 에이전트가 보고할 외부 IP |
| `DRY_RUN` | `0` | `1`로 설정 시 MQ 미발행, 페이로드만 stdout 출력 |

### 2. 에이전트 실행

```bash
# 단일 에이전트
docker compose up --build -d
```

```bash
# 복수 에이전트 (서버 3대 시뮬레이션)
docker compose up --build --scale agent=3 -d
```

```bash
# 로그 확인
docker compose logs -f
```

```bash
# 종료
docker compose down
```

### 3. 결과 확인

메인 스택의 consumer 로그에서 수신 확인:
```bash
# 루트 디렉토리에서
docker compose logs -f consumer
```

웹 UI에서 수집된 서버 목록 확인:
```
http://localhost:8000/servers/
```

---

## 접속

| 주소 | 설명 |
|------|------|
| http://localhost:8000/servers/ | 서버 인벤토리 웹 UI |
| http://localhost:15672 | RabbitMQ 관리 콘솔 |
| localhost:5432 | PostgreSQL |