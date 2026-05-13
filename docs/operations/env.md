# 환경변수 (현황 카탈로그)

> 본 문서는 키 카탈로그다. 정책·dev/prod 분리·secret 단계는 `docs/operations/dev-prod.md` 참조.

루트 `.env`에서 주입 (dev). `.env.example`을 복사해 시작한다.

```bash
cp .env.example .env
```

prod에선 `.env` 대신 Docker secrets로 자격을 주입한다 — 자세한 정책은 `docs/operations/dev-prod.md` "Secret 정책".

## 주입 흐름

`.env`를 읽는 주체가 4곳이다. 각자 우선순위·시점·범위가 다르다.

```
                        ┌─────────────────────────────────────┐
   루트 .env  (호스트)   │  POSTGRES_HOST=postgres ...         │
                        └────────┬──────────┬──────────┬──────┘
                                 │          │          │
                ┌────────────────┘          │          └──────────────────┐
                │ (1)                       │ (2)                         │ (3)
                ▼                           ▼                             ▼
   docker-compose env_file        config.py BaseSettings        dev-up.sh source agent.env
   → 컨테이너 환경변수 주입       → Python 인스턴스 필드        → /etc/assessment-agent.env
   → environment: 블록이          → 환경변수 > .env > default   → Lima VM 안 에이전트로 전달
     일부 키 강제 오버라이드      (cwd /app/.env 도 read)       → RABBITMQ_HOST는 별도 주입
                │
                └─ (4) 컨테이너 안 Python 시작 시 (1)+(2)가 결합:
                       환경변수가 이미 주입돼 있으므로 (2)의 .env read는 redundant
                       (호스트 직접 실행 시에만 (2)의 .env가 의미 있음 — fallback)
```

### 우선순위 (pydantic-settings)
1. OS 환경변수 (docker-compose가 컨테이너에 주입한 값 / 호스트 셸 export)
2. `.env` 파일 (cwd 기준 — 컨테이너 안에서는 `/app/.env`)
3. config.py default

docker-compose `environment:` 블록은 `env_file:`보다 후순위로 적용되어 마지막 덮어쓰기가 된다 — 즉 컨테이너 안에서는 `environment:`가 항상 우선.

### 컨테이너 안의 `/app/.env` (DEV 한정)

`docker-compose.yml`의 `volumes: ./:/app` 코드 마운트로 호스트 `.env`가 컨테이너 안에 그대로 노출된다. 결과:

- pydantic-settings의 `env_file=".env"` 설정이 이 파일도 read → 환경변수가 우선이라 동작에 영향 없음 (redundant read).
- 컨테이너 안에 secret이 노출 — DEV는 OK, 프로덕션은 위험.

프로덕션 정책: `docker-compose.yml`의 `volumes: ./:/app` 제거. 이미 `.dockerignore`에 `.env`가 있어 `Dockerfile`의 `COPY . .` 단계에서는 제외되므로, 코드 마운트만 제거하면 컨테이너 안에 `.env`가 사라진다.

## 전체 키 목록 (`.env.example` 순서)

| 키 | 기본값 | 사용처 | 설명 |
|----|--------|--------|------|
| `APP_ENV` | `dev` | config.py / docker-compose | 환경 마커. `dev`/`staging`/`prod`. `prod`일 때 model_validator가 약한 default 거부 |
| `POSTGRES_HOST` | `postgres` | config.py / docker-compose | PostgreSQL 호스트 (docker-compose 서비스명) |
| `POSTGRES_PORT` | `5432` | config.py / docker-compose | |
| `POSTGRES_DB` | `assessment` | config.py / docker-compose | |
| `POSTGRES_USER` | `assessment` | config.py / docker-compose | |
| `POSTGRES_PASSWORD` | `assessment` | config.py / docker-compose | |
| `RABBITMQ_HOST` | `rabbitmq` | config.py | 컨슈머 broker 접속 (docker-compose 서비스명). 에이전트는 본 키를 사용하지 않음 — dev-up.sh가 `host.lima.internal` (Lima user-mode network alias) 별도 주입 |
| `RABBITMQ_PORT` | `5672` | config.py / docker-compose | |
| `RABBITMQ_VHOST` | `/assessment` | config.py / docker-compose / dev-up.sh | 전용 vhost. 에이전트와 동일 값 사용. AMQP URL의 `/`는 `%2F`로 인코딩 (config.py `broker_url` 자동 처리) |
| `RABBITMQ_USER` | `assessment` | config.py / docker-compose / dev-up.sh (infra/agent.env) | |
| `RABBITMQ_PASSWORD` | `assessment` | config.py / docker-compose / dev-up.sh (infra/agent.env) | |
| `RABBITMQ_MANAGEMENT_PORT` | `15672` | docker-compose | RabbitMQ 관리 콘솔 포트 노출 (config.py 미사용) |
| `RABBITMQ_EXCHANGE` | `assessment` | config.py / dev-up.sh (infra/agent.env) | 에이전트 - consumer routing 계약. 변경 시 양쪽 동기화 |
| `RABBITMQ_ROUTING_KEY_INVENTORY` | `server.inventory` | config.py / dev-up.sh (infra/agent.env) | 동일 |
| `RABBITMQ_ROUTING_KEY_METRICS` | `server.metrics` | config.py / dev-up.sh (infra/agent.env) | 동일 |
| `RABBITMQ_ROUTING_KEY_ERROR` | `server.error` | config.py / dev-up.sh (infra/agent.env) | 동일 |
| `RABBITMQ_ROUTING_KEY_TASK_RESULT` | `task.result` | config.py / dev-up.sh (infra/agent.env) | agent -> engine 작업 결과 보고. v4 신설 |
| `REDIS_HOST` | `redis` | config.py | (docker-compose 서비스명) |
| `REDIS_PORT` | `6379` | config.py | |
| `WEB_PORT` | `8000` | config.py / docker-compose | Web UI 접속 포트. 충돌 시 변경 |
| `SQLALCHEMY_ECHO` | `false` | config.py | SQLAlchemy 엔진 SQL 로깅. dev 디버깅 시 true (운영 환경은 false 유지 — 로그 폭증·secret 노출 위험) |
| `PGADMIN_PORT` | `5050` | docker-compose dev override | pgAdmin GUI 포트 (dev 전용) |
| `LLM_PROVIDER` | `mock` | config.py / docker-compose | AI 진단 LLM 클라이언트 (ADR 0004). `mock` 또는 `ollama`. 과금 발생 외부 API는 운영자 정책상 금지 |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | config.py | LLM_PROVIDER=ollama 시 사용 |
| `OLLAMA_MODEL` | `llama3.1:8b` | config.py | ollama 모델명 |
| `LLM_TIMEOUT_SECONDS` | `60` | config.py | LLM 호출 cap |
| `LLM_MOCK_LATENCY_SECONDS` | `2.0` | config.py | mock client 응답 sleep (UI progress 단계 표시 확인용) |
| `DIAGNOSTIC_ROUTING_KEY` | `diagnostic.request` | config.py | engine 내부 routing key (web·worker·scheduler 공통) |
| `DIAGNOSTIC_QUEUE_TTL_MS` | `86400000` | config.py | 큐 메시지 TTL 24h |
| `DIAGNOSTIC_QUEUE_MAX_LEN` | `100000` | config.py | 큐 max length |
| `DIAGNOSTIC_RETENTION_DAYS` | `90` | config.py | diagnostic_jobs 보존 일수 — 스케줄러가 발화 시 함께 DELETE |
| `DIAGNOSTIC_SCHEDULE_CRON` | `0 3 * * *` | config.py | 스케줄러 cron (KST 03시 매일) |
| `DIAGNOSTIC_ACTIVE_SERVER_WINDOW_HOURS` | `24` | config.py | 활성 서버 정의 — last_seen_at 윈도우 |
| `WORKER_JOB_TIMEOUT_SECONDS` | `300` | config.py | 워커 진단 1건 전체 cap (클라이언트 polling timeout과 정렬) |

## 주의사항

### 호스트명 정책

기본값의 호스트명(`postgres`, `rabbitmq`, `redis`)은 docker-compose 서비스명이다. docker-compose 네트워크 내부에서만 해석된다.

| 실행 환경 | HOST 값 | 비고 |
|----------|---------|------|
| docker-compose 컨테이너 (web/consumer) | `postgres` / `rabbitmq` / `redis` | docker-compose `environment:` 블록이 강제로 오버라이드 — `.env`의 HOST 값과 무관하게 항상 서비스명으로 들어간다 |
| 호스트 직접 실행 (IDE 디버깅 등) | `localhost` | `.env`의 HOST 값을 `localhost`로 바꿔야 컨테이너 외부에서 해당 포트로 접속 가능 |

docker-compose `environment:` 오버라이드는 `web` / `consumer` 양쪽에 명시되어 있어 컨테이너 내부에서는 `.env` HOST 값을 변경해도 효과 없음. 호스트 직접 실행 시에만 의미 있다.

### Lima 에이전트 secret 채널 (분리됨)

dev-up.sh는 엔진의 `.env`를 에이전트에 전달하지 않는다. 별도 파일 `infra/agent.env`에서만 read (`set -a; source infra/agent.env; set +a`로 host env export):

- `RABBITMQ_USER`, `RABBITMQ_PASSWORD`, `RABBITMQ_EXCHANGE`, `RABBITMQ_ROUTING_KEY_INVENTORY`, `RABBITMQ_ROUTING_KEY_METRICS`, `RABBITMQ_ROUTING_KEY_ERROR`, `RABBITMQ_ROUTING_KEY_TASK_RESULT`

`infra/agent.env`가 없으면 dev-up.sh가 즉시 에러. `cp infra/agent.env.example infra/agent.env` 후 운영 값으로 수정.

이 값들이 limactl shell heredoc 치환으로 Lima VM 안 `/etc/assessment-agent.env`에 옮겨지고, `RABBITMQ_HOST`는 dev-up.sh가 `host.lima.internal` (Lima user-mode network alias) 상수로 별도 주입한다.

`infra/agent.env` 변경 후 VM에 반영하려면 `./dev-up.sh` 재실행 (VM은 Running 유지, `/etc/assessment-agent.env`만 재생성 + agent restart).

분리 근거: `docs/operations/dev-prod.md` "에이전트 secret 채널 분리" 절.

### config.py가 환경변수로 받지 않는 키

다음은 `.env.example`에 없고 `src/assessment_engine/config.py`의 default로만 정의된다 — 운영 중 변경 빈도가 낮아 의도적으로 환경변수화하지 않음:

- `redis_ttl_idempotent` (24h), `redis_ttl_online` (90s), `redis_ttl_token` (1h)
- `redis_ttl_last_agent_start` (24h), `redis_ttl_agent_restarts` (1h 슬라이딩 윈도우)
- `redis_ttl_task_pending` (24h — pending task hot path 캐시)
- `redis_key_*` 패턴 (cache:* / idempotent / online / token / last_agent_start / agent_restarts / task:pending)
- `redis_channel_metrics`
- `agent_restart_alert_threshold` (3 — 1h 내 재시작 N회 도달 시 warning)

운영 환경에서 조정 필요 시 `BaseSettings` 필드라 환경변수로도 주입 가능하며, 이 경우 `.env`에 키 추가 + `docs/operations/env.md` 갱신. 현재 시점에는 default 값이 적절. `docs/tradeoffs.md` T2 개선 방향 참조.