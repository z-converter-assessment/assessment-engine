# Docker

정책: CLAUDE.md #A. 본 문서는 docker-compose 운영 단일 진실 — Dockerfile·compose 파일 구조·서비스 카탈로그·healthcheck·기동 순서.

docker-compose는 엔진 그 자체 — web·consumer·diagnostic-worker·diagnostic-scheduler·postgres·rabbitmq·redis·migrate 8 서비스가 고객사 네트워크 내 설치 단위. Python 앱(web·consumer·worker·scheduler·migrate)은 로컬 빌드 단일 이미지 + command 분기, 인프라(postgres·rabbitmq·redis)는 공식 이미지.

---

## 파일 구조

```
Dockerfile                    — web·consumer·diagnostic-worker·diagnostic-scheduler 공용 이미지 (루트 — prod 도 활용 가능한 빌드 산출물)
dev/docker-compose.yml        — dev 단일 compose (#A0). dev/.env 평문 + 포트 노출 + 코드 마운트 + APP_ENV=dev. pgadmin은 profiles:[gui] 분리
dev/.env.example              — dev compose 기준 환경변수 카탈로그. `cp dev/.env.example dev/.env`
.env.example                  — prod 운영자 카탈로그 (루트). dev compose 와 무관 — 외부 인프라 운영자가 채워서 사용
.dockerignore                 — COPY . . 시 제외 경로
dev/pipeline-up.sh        — Docker → migrate → web 헬스체크 → Lima(limactl start + agent install) 순서 기동. COMPOSE_FILE=dev/docker-compose.yml export
dev/pipeline-down.sh      — Lima(limactl stop + delete) → docker compose down -v
```

dev compose 호출은 본 파일이 dev/ 디렉토리에 있어 자동 인식 안 됨. 루트에서는 `-f dev/docker-compose.yml` 명시:

```bash
docker compose -f dev/docker-compose.yml up --build -d
docker compose -f dev/docker-compose.yml down -v
# 또는 export 한 번
export COMPOSE_FILE=dev/docker-compose.yml
docker compose up --build -d
```

본 repo는 기능 개발용 docker-compose만 다룬다 (CLAUDE.md #A0, ADR 0012). prod 배포 인프라(IaC) 결정은 본 repo 범위 밖 — prod secret·환경변수 contract는 `docs/operations/env.md` + `config.py` `_validate_prod_*`에 코드·docs로만 표현. prod compose 변형은 본 repo에 두지 않음.

---

## Dockerfile

```dockerfile
FROM python:3.12-slim
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
RUN pip install uv

# 1단: 의존성만 install (uv.lock 기준, --no-install-project로 src 무관)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# 2단: src copy 후 project 자체(editable)만 추가
COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/opt/venv/bin:$PATH"
```

### 단일 이미지 + command 분기

web·consumer·diagnostic 워커·스케줄러·migrate 모두 같은 이미지를 쓰고, docker-compose의 `command` 필드로 진입점을 분기한다.

| 서비스 | command | 진입점 |
|--------|---------|--------|
| web | `python -m assessment_engine.web` | `src/assessment_engine/web/__main__.py` → uvicorn 기동 (override에서 reload) |
| consumer | `python -m assessment_engine.consumer` | `src/assessment_engine/consumer/__main__.py` → `asyncio.run(consumer.main.main())` |
| diagnostic-worker | `python -m assessment_engine.diagnostic` | ADR 0004 — `diagnostic.request` 큐 소비, LLM 호출 |
| diagnostic-scheduler | `python -m assessment_engine.diagnostic.scheduler` | ADR 0004 — 주기 진단 작업 enqueue |
| migrate | `alembic upgrade head` | postgres healthy 후 1회 실행하고 종료 (`restart: "no"`). ADR 0005 |

이미지가 1개라 빌드/푸시·패치 운영 비용이 최소화된다. 의존성 패키지(SQLAlchemy·aio-pika·redis·FastAPI 등)도 양쪽이 모두 사용하므로 분리 이득이 적다.

### 빌드 캐시 전략 — 2단 uv sync

```dockerfile
COPY pyproject.toml uv.lock ./                     # ← 의존성 명세 + lock만 먼저
RUN uv sync --frozen --no-install-project --no-dev # ← deps 전용 레이어 (cache hit 대상)
COPY . .                                            # ← 소스 코드
RUN uv sync --frozen --no-dev                       # ← project(editable)만 추가
```

`pyproject.toml`·`uv.lock`만 먼저 복사 → deps install → `COPY . .` → project install 순서. 소스 코드만 변경 시 1단(deps install) 레이어는 cache hit으로 재실행되지 않는다 (보통 1초 미만).

`pyproject.toml`·`uv.lock` 둘 중 하나라도 변경 시 deps 재설치(60s+) 발생.

### `uv sync --frozen` 패턴

- `uv sync`: pyproject.toml + uv.lock을 정합 검사한 후, lockfile에 고정된 트랜지티브 버전 그대로 install. `uv pip install -e .`(pyproject만 봄)와 달리 lockfile 무시 불가능 → reproducible build.
- `--frozen`: lockfile/pyproject drift 시 build 실패. lockfile 갱신 누락을 빌드 단계에서 catch.
- `--no-dev`: pyproject `[dependency-groups].dev`(pytest·ruff·testcontainers) 미포함. prod 이미지 슬림화.
- `--no-install-project`: project 자체는 skip하고 외부 deps만 install (1단 layer cache 분리용).
- editable install: uv는 hatchling project를 기본 editable로 install. 호스트 마운트(`./:/app`)와 결합해 코드 변경 즉시 반영.
- `UV_PROJECT_ENVIRONMENT=/opt/venv`: venv를 `/app` 바깥에 둔다. dev override의 `./:/app` bind mount가 `/app/.venv`를 호스트로 마스킹하는 충돌을 회피.

### 의존성 추가 워크플로

`pyproject.toml`만 수정한 PR은 `--frozen` build에서 fail한다. 다음 둘 중 하나로 lockfile 동시 갱신 의무:

```bash
uv add <pkg>          # pyproject.toml + uv.lock 동시 갱신
uv lock               # pyproject.toml 수동 편집 후 lockfile만 재생성
```

두 파일을 같은 commit에 포함. CLAUDE.md #F9 "신규 의존성" 항목과 일치.

### 멀티스테이지/multi-arch 미사용

- 멀티스테이지 빌드 미적용 — Python 슬림 이미지가 이미 작고 (~150MB), 빌드 도구가 추가로 필요 없음.
- multi-arch 빌드도 미적용 — 운영 타겟이 Linux/x86_64로 고정.

### CI 빌드 산출물 (ADR 0012)

본 repo CI 산출물은 Docker image가 아닌 Python wheel — ADR 0012 채택 후. Dockerfile + docker-compose는 dev·기능 개발 환경 한정. prod 운영은 외부 인프라 책임 (`docs/operations/deployment.md` 4절 inline 예시 + `docs/operations/release.md`).

`.github/workflows/ci.yml`의 `build` job — `uv build` + 빌드된 wheel을 fresh venv에 install + import·정적 자원 포함 검증. Docker image build verify는 본 워크플로에서 제거됨 (Dockerfile 정합 자체는 dev `docker compose build`로 확인).

`.github/workflows/release.yml` — semver tag(`v*`) push 시 wheel + sdist + SHA256SUMS를 GitHub Release artifact로 자동 첨부. 인프라 측은 release page·API로 다운로드. 사내 폐쇄망 mirror 필요 시 인프라 측 결정 (devpi·MinIO 등).

---

## dev/docker-compose.yml

### 서비스 구성 (기본 8개 — pgadmin은 profiles:[gui]로 분리, 명시 호출 시만 가동)

| 서비스 | 이미지 | 역할 | 적용 환경 |
|--------|--------|------|-----------|
| `postgres` | `timescale/timescaledb:latest-pg16` | 메인 DB + TimescaleDB 확장 | dev / prod |
| `rabbitmq` | `rabbitmq:3.13-management-alpine` | 메시지 브로커 (AMQP + 관리 UI) | dev / prod |
| `redis` | `redis:7-alpine` | 캐시·온라인 TTL·PUB/SUB | dev / prod |
| `migrate` | 로컬 빌드 | `alembic upgrade head` 1회 실행 후 종료 (ADR 0005). 앱 서비스 4종이 `depends_on: service_completed_successfully`로 그 뒤 기동 | dev / prod |
| `web` | 로컬 빌드 | FastAPI SSR + API + StaticFiles | dev / prod |
| `consumer` | 로컬 빌드 | aio-pika 컨슈머 (server.* + task.result 큐) | dev / prod |
| `diagnostic-worker` | 로컬 빌드 | `diagnostic.request` 큐 소비, LLM 호출 (ADR 0004) | dev / prod |
| `diagnostic-scheduler` | 로컬 빌드 | 주기 진단 작업 enqueue (ADR 0004) | dev / prod |
| `pgadmin` | `dpage/pgadmin4` | DB GUI (`profiles:[gui]` 전용). `docker compose --profile gui up -d pgadmin`으로 명시 호출 — idle 250 MiB 절감 | dev gui only |

### 포트 노출

| 서비스 | 호스트 포트 | 컨테이너 포트 | 용도 |
|--------|------------|--------------|------|
| postgres | `${POSTGRES_PORT:-5432}` | 5432 | psql 직접 접속 (디버그) |
| rabbitmq | `${RABBITMQ_PORT:-5672}` | 5672 | AMQP — Lima VM 에이전트가 `host.lima.internal:5672`로 접근 |
| rabbitmq | `${RABBITMQ_MANAGEMENT_PORT:-15672}` | 15672 | 관리 UI |
| web | `${WEB_PORT:-8000}` | 8000 | HTTP — 브라우저 + `/static/*` 정적 자원 |

redis·consumer는 포트 미노출. 모두 docker 네트워크 내부에서만 접근.

### 네임드 볼륨

```yaml
volumes:
  postgres_data:
```

`postgres_data` 네임드 볼륨 1개. PostgreSQL 데이터 디렉토리(`/var/lib/postgresql/data`)를 마운트한다.

| 동작 | 명령 | postgres_data |
|------|------|---------------|
| 일반 종료 | `docker compose down` | 보존 |
| 완전 초기화 | `docker compose down -v` | 삭제 |

`down -v`가 필요한 시나리오:
- ORM 모델에 컬럼/제약(`UniqueConstraint` 등) 추가 — `create_all`은 기존 테이블에 변경 적용 안 함.
- TimescaleDB hypertable 정의 변경.
- 테스트 시나리오 초기화.

redis·rabbitmq는 볼륨 없음 — 재시작 시 상태 초기화. 멱등성 키·메시지 큐·캐시 모두 휘발성. 운영 환경이라면 `rabbitmq_data` / `redis_data` 볼륨 추가 검토.

### 코드 마운트 (DEV 전용)

```yaml
web:
  volumes: [./:/app]
consumer:
  volumes: [./:/app]
```

호스트 프로젝트 루트를 컨테이너 `/app`에 마운트. `Dockerfile`의 `COPY . .`는 빌드 시점 복사라 이후 호스트 변경이 반영 안 되지만, 이 마운트가 위에 덮여 코드 변경이 컨테이너 내부에 즉시 노출된다.

조합 효과:
- `web`: uvicorn `reload=True` (`src/assessment_engine/web/__main__.py`)가 파일 변경 감지 → 자동 재기동. 새로고침만으로 변경 확인.
- `consumer`: reload 없음. 변경 후 `docker compose restart consumer` 필요.
- `src/assessment_engine/web/static/js/chart-utils.js` 같은 정적 자원도 별도 빌드 없이 즉시 서빙.

프로덕션 마이그레이션 가이드: `volumes: ./:/app` 제거 → `Dockerfile`의 `COPY . .` 결과만 사용. uvicorn `reload=True`도 제거 (`src/assessment_engine/web/__main__.py`).

보안 — `.env` 노출: 코드 마운트의 부작용으로 호스트 `.env`가 컨테이너 안 `/app/.env`에 그대로 노출된다 (`-rw-r--r--` root 소유). `.dockerignore`에 `.env`가 명시되어 있어 이미지 빌드(`COPY . .`) 시점에는 제외되지만, 런타임 코드 마운트는 빌드 산출물이 아니므로 `.dockerignore`가 적용되지 않는다. 프로덕션에서 코드 마운트를 제거하면 `.env`도 자동으로 컨테이너 안에서 사라진다.

### Redis 인라인 설정

```yaml
command: redis-server --maxmemory 256mb --maxmemory-policy volatile-lru
```

별도 설정 파일 없이 커맨드라인으로 redis.conf 옵션을 전달.

- `maxmemory 256mb` — B2B 내부 포털 규모 기준. 키 수·값 크기가 작아 충분.
- `volatile-lru` — TTL이 설정된 키만 evict 대상. TTL 없는 `cache:resolve:{public_id}` 키를 보호하면서 만료 임박 캐시·온라인 키를 우선 제거.
- 멱등성 키(`idempotent:{message_id}`)는 24h TTL이 있어 evict 가능 → at-most-once 트레이드오프와 연결 (`docs/tradeoffs.md` T1, T11).

### 환경변수 주입

`.env` 파일 → `env_file:` 으로 전체 주입 후, docker 내부 서비스명을 `environment:`에서 오버라이드.

```yaml
env_file: .env
environment:
  POSTGRES_HOST: postgres   # .env의 localhost 값 오버라이드
  REDIS_HOST: redis
  RABBITMQ_HOST: rabbitmq   # consumer·diagnostic-worker·diagnostic-scheduler — web은 RabbitMQ 직접 사용 안 함
```

호스트에서 직접 실행 시 `.env`의 기본값(`localhost`)을 쓰고, 컨테이너에서는 `environment` 블록이 오버라이드.

| 변수 | 호스트 실행 | 컨테이너 |
|------|-------------|---------|
| POSTGRES_HOST | `localhost` (.env) | `postgres` (compose) |
| REDIS_HOST | `localhost` (.env, 명시 없음) | `redis` (compose) |
| RABBITMQ_HOST | `localhost` (.env) | `rabbitmq` (compose, MQ 사용 서비스 한정) |

### healthcheck

| 서비스 | 체크 명령 | interval | timeout | retries | start_period |
|--------|----------|----------|---------|---------|--------------|
| postgres | `pg_isready -U <user>` | 5s | 5s | 5 | — |
| rabbitmq | `rabbitmq-diagnostics ping` | 10s | 5s | 5 | — |
| redis | `redis-cli ping` | 5s | 5s | 5 | — |
| web | `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"` | 5s | 5s | 5 | 10s |

`web` healthcheck:
- 명령으로 `python -c ...`를 쓰는 이유: `curl`이 python:3.12-slim 이미지에 없음. python 표준 라이브러리로 해결.
- `start_period: 10s` — web lifespan(`CREATE EXTENSION + create_all + create_hypertable`)이 완료될 시간 확보. 이 구간의 실패는 `retries`에 포함되지 않는다.
- 헬스 엔드포인트(`/health`)는 단순 `{"status": "ok"}` JSON. DB·Redis 연결 검사 안 함 (deep healthcheck 안 함).

### 기동 순서 (`depends_on`)

```
postgres ─ healthy ─▶ migrate (alembic upgrade head, 1회 실행 후 exit)
                          │
                          ▼ service_completed_successfully
            ┌──────┬──────┴───────────┬──────────────────────┐
            ▼      ▼                  ▼                      ▼
           web   consumer    diagnostic-worker    diagnostic-scheduler
            ▲      ▲                  ▲                      ▲
   redis ───┴──────┴──────────────────┴──────────────────────┤
rabbitmq ──────────┴──────────────────┴──────────────────────┘
```

ADR 0005 표준: 모든 환경(dev·staging·prod) Alembic 단일 진실. `migrate` 컨테이너가 schema 준비 완료를 보장한 뒤 앱 4종 기동.

### restart 정책

모든 서비스 `restart: unless-stopped`:
- 컨테이너 비정상 종료(OOM·프로세스 크래시·healthcheck 누적 실패) 시 Docker가 자동 재시작.
- `docker compose down`으로 명시적으로 내릴 때는 재시작 안 함.
- 호스트 재부팅 시 자동 기동 (Docker daemon이 enable되어 있다면).

---

## dev/pipeline-up.sh / dev/pipeline-down.sh

운영자 절차·VM 매트릭스: `docs/development/pipeline.md` + `docs/development/pipeline.md`. 본 절은 docker 관점 동작만:

- `dev/pipeline-up.sh` [1/4] `docker compose up -d --build` → [2/4] migrate 완료 대기(180s) → [3/4] web 헬스체크(180s) → [4/4] Lima 4 VM.
- 헬스체크 타임아웃 초과 시 migrate/web 로그 30라인 dump 후 exit.
- `dev/pipeline-down.sh`: Lima 4 VM 제거 → `docker compose down -v`(postgres_data 삭제). 다음 dev-up은 빈 DB에서 시작 → `migrate`가 모든 schema·hypertable 신규 생성.

---

## 운영 노트

### 코드 변경 → 컨테이너 반영 매트릭스

| 변경 위치 | web | consumer | 추가 작업 |
|-----------|-----|----------|----------|
| Python 코드 (web/) | uvicorn auto-reload | — | 없음 |
| Python 코드 (consumer/, db/, config.py) | uvicorn auto-reload | 미반영 | `docker compose restart consumer` |
| 정적 자원 (web/static/) | 즉시 (브라우저 cache 주의) | — | 브라우저 강제 새로고침 |
| Jinja2 템플릿 (web/templates/) | 즉시 | — | 없음 |
| `pyproject.toml` (의존성) | 미반영 | 미반영 | `docker compose up --build -d` (의존성 레이어 재빌드) |
| `Dockerfile` | 미반영 | 미반영 | `docker compose up --build -d` |
| `dev/docker-compose.yml` | 부분 | 부분 | `docker compose up -d` (변경된 서비스만 재생성) |
| ORM 모델 (컬럼·제약 추가) | 새 모델 로드는 reload되나 DB 스키마는 미반영 | 동일 | (ADR 0005) `alembic revision --autogenerate` → `docker compose restart migrate` → 앱 서비스 재기동. 마이그레이션 누락 시 `alembic check` 차단 |

### 디버깅 유용 명령

```bash
docker compose logs -f web                       # web 실시간 로그
docker compose logs consumer --since=10m         # consumer 최근 10분
docker compose exec postgres psql -U assessment -d assessment   # DB 접속
docker compose exec redis redis-cli              # Redis 접속
docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready   # 큐 적재량
docker compose ps                                # 컨테이너 상태
```

### 흔한 트러블

| 증상 | 원인 | 해결 |
|------|------|------|
| web 헬스체크 unhealthy | lifespan에서 모델 로딩 실패 (`TypeError: non-default argument follows default` 등) | `docker compose logs web` 확인 후 코드 수정 → reload 자동 처리 |
| consumer가 metrics 받았지만 server 미등록 | inventory 도착 전 metrics가 먼저 옴 (DB 초기화 직후 등) | 자동 처리됨 — consumer가 placeholder inventory 생성 후 정상 저장 (`auto-registered server from metrics ...` 로그). 다음 inventory 도착 시 풀 정보로 자동 업데이트 |
| `docker compose up` 후 시간 초과 | 첫 빌드는 의존성 설치(60s+) + TimescaleDB 이미지 풀(~200MB) 필요 | 첫 기동만 5분 정도 여유 |
| `pg_isready` 통과했지만 web에서 connection refused | postgres healthcheck는 `--listen` 단계 통과 후 수행되지만 TimescaleDB 확장 로딩이 진행 중일 수 있음 | start_period(`web` 측) 활용 — 이미 적용됨 |

---

## RabbitMQ 운영

본 문서는 docker-compose 관점(컨테이너 정의·볼륨·헬스체크)만 다룬다. RabbitMQ broker 자체의 운영 — vhost 개념·권한 모델·토폴로지·dev/prod 분기·Production 전환 체크리스트는 `docs/architecture/rabbitmq.md` 단일 진실.