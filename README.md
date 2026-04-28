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
| DB | TimescaleDB (PostgreSQL + SQLAlchemy async + asyncpg) |
| 캐시 / 온라인 상태 | Redis 7 |
| 실제 에이전트 | C99/C++03 바이너리 (MQ 직접 발행) |

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
| `REDIS_HOST` | `redis` | docker-compose가 서비스명으로 오버라이드 — `.env` 설정 불필요 |
| `REDIS_PORT` | `6379` | |
| `WEB_PORT` | `8000` | |

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

## 접속

| 주소 | 설명 |
|---|---|
| http://localhost:8000/servers/ | 서버 인벤토리 웹 UI |
| http://localhost:8000/health | 헬스체크 |
| http://localhost:8000/docs | FastAPI Swagger UI |
| http://localhost:8000/redoc | FastAPI ReDoc |
| http://localhost:15672 | RabbitMQ 관리 콘솔 |
| localhost:5432 | PostgreSQL |

---

## 통합 테스트 (Vagrant VM)

에이전트(C 바이너리) → RabbitMQ → Consumer → DB → Web UI 전체 파이프라인 검증.
```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                 HOST MACHINE                                 │
│                                                                              │
│   ┌──────────────────────────────────────────────────────────────────────┐   │
│   │                 DOCKER COMPOSE (Assessment Engine)                   │   │
│   │                                                                      │   │
│   │   [ FastAPI ] <───> [ RabbitMQ ] <─── [ Redis ]                      │   │
│   │      :8000              :5672             (Cache)                    │   │
│   │        │                  ▲                                          │   │
│   │        │                  │                                          │   │
│   │   [ PostgreSQL ] <── [ Consumer ]                                    │   │
│   │      :5432           (Task Engine)                                   │   │
│   └───────────────────────────┬──────────────────────────────────────────┘   │
│                               │ 10.0.2.2 (Gateway)                           │
│           ┌───────────────────┼───────────────────┐                          │
│           │                   │                   │                          │
│   ┌───────▼───────┐   ┌───────▼───────┐   ┌───────▼───────┐                  │
│   │     VM 1      │   │     VM 2      │   │     VM 3      │                  │
│   │ (Ubuntu 22.04)│   │ (Rocky Linux9)│   │  (Debian 12)  │                  │
│   ├───────────────┤   ├───────────────┤   ├───────────────┤                  │
│   │ web-server-01 │   │ db-server-01  │   │backup-srv-01  │                  │
│   │ - /proc       │   │ - /proc       │   │ - /proc       │                  │
│   │ - machine-id  │   │ - machine-id  │   │ - machine-id  │                  │
│   │ - Agent (C99) │   │ - Agent (C99) │   │ - Agent (C99) │                  │
│   └───────────────┘   └───────────────┘   └───────────────┘                  │
└──────────────────────────────────────────────────────────────────────────────┘
```
- Vagrant NAT 환경에서 VM → 호스트 접근 주소: **`10.0.2.2`**
- 3개 VM이 동시에 각자의 메트릭을 발행 → Web UI에서 서버 3대로 확인
- `vagrant up` 완료 시 각 VM에서 에이전트가 **자동 빌드 → systemd 서비스 등록 → 시작**

### 추가 사전 요구사항

| 소프트웨어 | 설치 방법 | 용도 |
|-----------|----------|------|
| VirtualBox 7.1+ | [virtualbox.org](https://www.virtualbox.org/) | VM 하이퍼바이저 |
| Vagrant 2.4.x | [vagrantup.com](https://www.vagrantup.com/) | VM 프로비저닝 |

> **Apple Silicon (ARM64)**: VirtualBox 7.1+부터 ARM VM 지원. bento 박스가 arm64 변형을 자동으로 선택한다.
> **x86 Windows / Linux**: 동일한 Vagrantfile에서 x86_64 박스가 자동 선택되며 더 안정적으로 동작한다.

### 디렉토리 구조 전제

```
(작업 디렉토리)/
├── assessment-engine/   ← Vagrantfile 위치
└── assessment-agent/    ← 에이전트 소스 (git clone 필요)
```

### VM 구성

| VM | Box | OS | 시뮬레이션 |
|----|-----|----|-----------|
| `web-server-01` | bento/ubuntu-22.04 | Ubuntu 22.04 | 웹 서버 |
| `db-server-01` | bento/rockylinux-9 | Rocky Linux 9 | DB 서버 (RHEL 계열) |
| `backup-server-01` | bento/debian-12 | Debian 12 | 백업 서버 |

> Rocky Linux 9는 프로비저닝 시 EPEL + CRB 저장소를 자동으로 활성화해 빌드 의존성을 설치한다.

### 실행

```bash
# 1. assessment-engine 기동
cd assessment-engine
cp .env.example .env
docker compose up -d
```

```bash
# 2. VM 기동 (최초 1회 — OS·패키지 설치·에이전트 빌드로 수분 소요)
vagrant up
```

프로비저닝 중 자동으로 수행되는 작업:
1. 빌드 의존성 패키지 설치 (gcc, librabbitmq-dev, libcjson-dev 등)
2. `.env` 생성 (`RABBITMQ_HOST=10.0.2.2` 포함)
3. `make` — 에이전트 빌드
4. systemd `assessment-agent.service` 등록 및 시작

engine이 내려간 상태에서 VM을 올리면 에이전트가 RabbitMQ 접속 실패 → `Restart=on-failure`로 10초마다 재시도하므로 engine 기동 후 자동으로 연결된다.

### 결과 확인

```bash
# 에이전트 로그
vagrant ssh web-server-01 -c "journalctl -u assessment-agent -f"
```

http://localhost:8000/servers/ 에서 서버 3대 온라인 확인.
60초 주기로 메트릭이 갱신되며 각 서버의 상세 페이지에서 CPU·메모리·디스크·네트워크 확인.

### VM 관리
```bash
vagrant halt                    # 전체 정지 (machine-id 유지)
```
```bash
vagrant reload web-server-01    # 특정 VM 재기동
```

### VM 삭제
```bash
vagrant destroy -f              # 전체 삭제 (재기동 시 새 machine-id → 새 서버로 DB 등록)
```

### engine 삭제
```bash
# 종료 [데이터 유지]
docker compose down
```
```bash
# 종료 [데이터 삭제]
docker compose down -v
```
