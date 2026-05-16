# 환경변수 카탈로그

정책: CLAUDE.md #A. 본 문서는 환경변수 키 카탈로그 단일 진실. 환경변수 정책·secret 단계·dev/prod 분리는 `docs/operations/prod-contract.md`.

dev: `cp .env.example .env`. prod 채널·정책: `docs/operations/prod-contract.md` "Secret 채널".

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
   docker-compose env_file        config.py BaseSettings        pipeline-up.sh source agent.env
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

## 컴포넌트별 read 매트릭스

본 repo의 4 컴포넌트(web·consumer·diagnostic-worker·diagnostic-scheduler)는 각자 다른 키 집합을 read. multi-node 분리 배포 시 어느 노드에 어느 키 inject할지 reference. 코드 측 단일 진실: 컴포넌트별 sub-module(`web/settings.py`·`consumer/settings.py`·`diagnostic/settings.py`)에서 자기 Settings 인스턴스화 (Composition Root, CLAUDE.md #F4).

| 키 그룹 | web | consumer | diagnostic-worker | diagnostic-scheduler |
|--------|:---:|:--------:|:-----------------:|:--------------------:|
| `APP_ENV`·`LOG_FORMAT` | 의무 | 의무 | 의무 | 의무 |
| `POSTGRES_*` | 의무 | 의무 | 의무 | 의무 |
| `REDIS_*` | 의무 | 의무 | 의무 | 의무 |
| `RABBITMQ_*` (broker 접속) | 의무 (진단 publish) | 의무 (consume) | 의무 (consume) | 의무 (publish) |
| `RABBITMQ_ROUTING_KEY_*`·`RABBITMQ_EXCHANGE` | 의무 | 의무 | 의무 | 의무 |
| `WORKER_*` (worker.result·task.install) | 의무 (task.install publish) | 의무 (worker.result consume) | 선택 | 선택 |
| `WEB_PORT`·`INSTALL_BUNDLE_URL`·`INSTALL_TIMEOUT_SEC` | 의무 | 사용 안 함 | 사용 안 함 | 사용 안 함 |
| `LLM_*`·`OLLAMA_*` | 사용 안 함 | 사용 안 함 | 의무 | 사용 안 함 |
| `DIAGNOSTIC_QUEUE_*`·`DIAGNOSTIC_ROUTING_KEY` | 의무 (publish) | 사용 안 함 | 의무 (consume) | 의무 (publish) |
| `DIAGNOSTIC_SCHEDULE_CRON`·`DIAGNOSTIC_RETENTION_DAYS`·`DIAGNOSTIC_ACTIVE_SERVER_WINDOW_HOURS` | 사용 안 함 | 사용 안 함 | 사용 안 함 | 의무 |
| `WORKER_JOB_TIMEOUT_SECONDS` | 사용 안 함 | 사용 안 함 | 의무 | 사용 안 함 |
| `SQLALCHEMY_ECHO` | 의무 | 의무 | 의무 | 의무 |

prod 검증(`_validate_prod_*`) 발동 위치 (multi-node 분리 시):
- web 노드: `WebSettings` + `DiagnosticSettings` → POSTGRES·RABBITMQ password weak default 거부
- consumer 노드: `ConsumerSettings` → POSTGRES·RABBITMQ password weak default 거부
- diagnostic-worker·scheduler 노드: `DiagnosticSettings` → POSTGRES·RABBITMQ password weak default 거부

본 repo 단일 진실 코드:
- `src/assessment_engine/config.py` — class 정의만 (인스턴스 0)
- `src/assessment_engine/web/settings.py` — WebSettings + DiagnosticSettings
- `src/assessment_engine/consumer/settings.py` — ConsumerSettings
- `src/assessment_engine/diagnostic/settings.py` — DiagnosticSettings
- `src/assessment_engine/db/session.py`·`db/redis.py` — 자체 WebSettings (모든 컴포넌트 공통 db layer)

## 정석 주입 패턴 (운영 복잡도 단계별)

| 단계 | 패턴 | 적합 환경 | 외부 인프라 구현 |
|------|------|----------|---------------|
| A. 단일 `.env` 모든 노드 동일 inject | 한 파일 전부 — 단순 | 단일 host 또는 dev | docker-compose `env_file`·systemd `EnvironmentFile=/etc/assessment-engine.env` |
| B. 컴포넌트별 `.env` 분리 | 노드별 자기 키만 | small multi-node | systemd unit별 `EnvironmentFile=/etc/<component>.env` |
| C. 계층화 — 공통 + 컴포넌트별 (권장) | `shared.env` (DB·MQ·Redis·LOG_FORMAT) + `<component>.env` (특화 키) | 4 node 분리 prod | Ansible `group_vars`(shared) + `host_vars`(component별). systemd `EnvironmentFile=` 여러 줄 |
| D. 중앙 secret store | Vault·Consul·AWS Parameter Store·k8s ConfigMap·External Secrets | 다중 환경·동적 회전 | 인프라 측 자체 운영 |

본 repo 책임 한계: 위 패턴 중 어느 채널 쓰든 pydantic Settings가 env·secrets_dir 둘 다 지원. 본 매트릭스는 reference — 실제 채널 선택·노드 분리 토폴로지는 외부 인프라 결정 (CLAUDE.md #A0).

prod-contract.md 7절 "Secret 채널" + deployment.md "단계별 흐름" 참조.

## 전체 키 목록 (`.env.example` 순서)

| 키 | 기본값 | 사용처 | 설명 |
|----|--------|--------|------|
| `APP_ENV` | `dev` | config.py / docker-compose | 환경 마커. `dev`/`staging`/`prod`. `prod`일 때 model_validator가 약한 default 거부 |
| `POSTGRES_HOST` | `postgres` | config.py / docker-compose | PostgreSQL 호스트 (docker-compose 서비스명) |
| `POSTGRES_PORT` | `5432` | config.py / docker-compose | |
| `POSTGRES_DB` | `assessment` | config.py / docker-compose | |
| `POSTGRES_USER` | `assessment` | config.py / docker-compose | |
| `POSTGRES_PASSWORD` | `assessment` | config.py / docker-compose | |
| `RABBITMQ_HOST` | `rabbitmq` | config.py | 컨슈머 broker 접속 (docker-compose 서비스명). 에이전트는 본 키를 사용하지 않음 — pipeline-up.sh가 `host.lima.internal` (Lima user-mode network alias) 별도 주입 |
| `RABBITMQ_PORT` | `5672` | config.py / docker-compose | |
| `RABBITMQ_VHOST` | `/assessment` | config.py / docker-compose / pipeline-up.sh | 전용 vhost. 에이전트와 동일 값 사용. AMQP URL의 `/`는 `%2F`로 인코딩 (config.py `broker_url` 자동 처리) |
| `RABBITMQ_USER` | `assessment` | config.py / docker-compose / pipeline-up.sh (dev/agent.env) | |
| `RABBITMQ_PASSWORD` | `assessment` | config.py / docker-compose / pipeline-up.sh (dev/agent.env) | |
| `RABBITMQ_MANAGEMENT_PORT` | `15672` | docker-compose | RabbitMQ 관리 콘솔 포트 노출 (config.py 미사용) |
| `RABBITMQ_EXCHANGE` | `assessment` | config.py / pipeline-up.sh (dev/agent.env) | 에이전트 - consumer routing 계약. 변경 시 양쪽 동기화 |
| `RABBITMQ_ROUTING_KEY_INVENTORY` | `server.inventory` | config.py / pipeline-up.sh (dev/agent.env) | 동일 |
| `RABBITMQ_ROUTING_KEY_METRICS` | `server.metrics` | config.py / pipeline-up.sh (dev/agent.env) | 동일 |
| `RABBITMQ_ROUTING_KEY_ERROR` | `server.error` | config.py / pipeline-up.sh (dev/agent.env) | 동일 |
| `RABBITMQ_WORKER_USER` | `assessment` | pipeline-up.sh (dev/agent.env) | 원격 호스트 worker 가 사용할 AMQP user. 비어 있으면 worker 자동 비활성 (collector 만 동작) |
| `RABBITMQ_WORKER_PASSWORD` | `assessment` | pipeline-up.sh (dev/agent.env) | RABBITMQ_WORKER_USER 의 암호. heredoc 안에서 `RABBITMQ_WORKER_PASS` 로 매핑 |
| `WORKER_TASK_EXCHANGE` | `assessment.tasks` | config.py / pipeline-up.sh (dev/agent.env) | task.install/task.result 전용 exchange. collector exchange 와 분리 |
| `WORKER_TASK_QUEUE_PREFIX` | `agent.tasks` | pipeline-up.sh (dev/agent.env) | 원격 호스트별 큐 prefix. full name = `<prefix>.<machine_id>` |
| `WORKER_TASK_RESULT_KEY` | `task.result` | pipeline-up.sh (dev/agent.env) | 원격 호스트 -> 엔진 결과 보고 routing key |
| `WORKER_DOWNLOAD_ALLOWED_HOSTS` | `host.lima.internal` | pipeline-up.sh (dev/agent.env) | task.install download.url 의 host 화이트리스트 (case-insensitive 정확 매치) |
| `REDIS_HOST` | `redis` | config.py | (docker-compose 서비스명) |
| `REDIS_PORT` | `6379` | config.py | |
| `REDIS_MAXMEMORY` | `256mb` | docker-compose (redis command) | Redis maxmemory cap. prod 에서 운영자가 튜닝 가능 |
| `REDIS_MAXMEMORY_POLICY` | `volatile-lru` | docker-compose (redis command) | maxmemory 도달 시 eviction policy. TTL 키 우선 evict — 본 프로젝트는 idempotent/online TTL 키 만료 가능 가정 |
| `WEB_PORT` | `8000` | config.py / docker-compose | Web UI 접속 포트. 충돌 시 변경 |
| `INSTALL_BUNDLE_URL` | `http://host.lima.internal:8000/zconverter.tar.gz` | config.py / .env | task.install download.url 에 박혀 발행. 분산 환경은 엔진 VM IP/hostname 으로 .env 수정 의무 (agent worker 가 본 URL 로 install bundle fetch). |
| `INSTALL_TIMEOUT_SEC` | `600` | config.py | install.sh wall-clock timeout. 원격 host worker 가 SIGTERM/SIGKILL |
| `SQLALCHEMY_ECHO` | `false` | config.py | SQLAlchemy 엔진 SQL 로깅. dev 디버깅 시 true (운영 환경은 false 유지 — 로그 폭증·secret 노출 위험) |
| `LOG_FORMAT` | `text` | config.py / 각 entry `setup_logging()` | 로그 출력 format. `text`(dev colorized·grep) 또는 `json`(외부 log aggregator indexing). prod은 `json` 권장 |
| `PGADMIN_PORT` | `5050` | docker-compose dev override | pgAdmin GUI 포트 (dev 전용) |
| `LLM_PROVIDER` | `mock` | config.py / docker-compose | 진단 narrative 합성 client (ADR 0004 + 0010). 현재 `mock`만 활성 (결정론 텍스트 합성), `ollama` 분기는 stub. 외부 LLM 도입 결정 시 활성 |
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

pipeline-up.sh는 엔진의 `.env`를 에이전트에 전달하지 않는다. 별도 파일 `dev/agent.env`에서만 read (`set -a; source dev/agent.env; set +a`로 host env export):

- `RABBITMQ_USER`, `RABBITMQ_PASSWORD`, `RABBITMQ_EXCHANGE`, `RABBITMQ_ROUTING_KEY_INVENTORY`, `RABBITMQ_ROUTING_KEY_METRICS`, `RABBITMQ_ROUTING_KEY_ERROR`
- `RABBITMQ_WORKER_USER`, `RABBITMQ_WORKER_PASSWORD`, `WORKER_TASK_EXCHANGE`, `WORKER_TASK_QUEUE_PREFIX`, `WORKER_TASK_RESULT_KEY`, `WORKER_DOWNLOAD_ALLOWED_HOSTS`

`dev/agent.env`가 없으면 pipeline-up.sh가 즉시 에러. `cp dev/agent.env.example dev/agent.env` 후 운영 값으로 수정.

이 값들이 limactl shell heredoc 치환으로 Lima VM 안 `/etc/assessment-agent.env`에 옮겨지고, `RABBITMQ_HOST`는 pipeline-up.sh가 `host.lima.internal` (Lima user-mode network alias) 상수로 별도 주입한다.

`dev/agent.env` 변경 후 VM에 반영하려면 `./scripts/pipeline-up.sh` 재실행 (VM은 Running 유지, `/etc/assessment-agent.env`만 재생성 + agent restart).

분리 근거: `docs/operations/prod-contract.md` "에이전트 secret 채널 분리" 절.

### config.py가 환경변수로 받지 않는 키

다음은 `.env.example`에 없고 `src/assessment_engine/config.py`의 default로만 정의된다 — 운영 중 변경 빈도가 낮아 의도적으로 환경변수화하지 않음:

- `redis_ttl_idempotent` (24h), `redis_ttl_online` (90s), `redis_ttl_token` (1h)
- `redis_ttl_last_agent_start` (24h), `redis_ttl_agent_restarts` (1h 슬라이딩 윈도우)
- `redis_key_*` 패턴 (cache:* / idempotent / online / token / last_agent_start / agent_restarts)
- `redis_channel_metrics`
- `agent_restart_alert_threshold` (3 — 1h 내 재시작 N회 도달 시 warning)

운영 환경에서 조정 필요 시 `BaseSettings` 필드라 환경변수로도 주입 가능하며, 이 경우 `.env`에 키 추가 + `docs/operations/env.md` 갱신. 현재 시점에는 default 값이 적절. `docs/tradeoffs.md` T2 개선 방향 참조.