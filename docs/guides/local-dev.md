# Docker

정책: CLAUDE.md #A. 본 문서는 docker-compose 운영 단일 진실 — Dockerfile·compose 파일 구조·서비스 카탈로그·healthcheck·기동 순서.

docker-compose는 엔진 그 자체 — web·consumer·worker·postgres·rabbitmq·redis·migrate 7 서비스가 고객사 네트워크 내 설치 단위. Python 앱(web·consumer·worker·migrate)은 단일 이미지 + command 분기(prod 는 GHCR pull, dev 는 로컬 빌드), 인프라(postgres·rabbitmq·redis)는 공식 이미지.

---

## 파일 구조

compose 2 파일 — base(prod-safe) + override(dev) 자동 머지:

```
docker-compose.yml          — prod-safe BASE. 앱 서비스 `build:` 없음, GHCR 이미지 핀 pull. bind mount 없음.
                              볼륨 env 바인딩(PGDATA_HOST·MQ_DATA_HOST). 배포 시 deploy.sh 가 버전 태그에서 raw fetch
docker-compose.override.yml — dev 전용. 소스 빌드(루트 Dockerfile)·`./src` bind mount·hot reload(watchfiles). `docker compose up` 시 base 에 자동 머지(override 우선). 배포 시 미사용
Dockerfile                  — 엔진 이미지 (web·consumer·worker·migrate 공용, multi-stage·non-root). base·override·CI/release·systemd·k8s 공용 단일 이미지 (dev-prod parity — dev/prod Dockerfile 분리 안 함)
env.example                — 배포 템플릿 (APP_ENV=prod·secret 필수). dev 검증 카탈로그는 루트 env.dev.example
env.dev.example            — dev 카탈로그 (APP_ENV=dev·weak default 허용·host=compose 서비스명)
.dockerignore               — 이미지 빌드 컨텍스트 제외 경로 (docs/·tests/·.env·.git 등)
```

compose 가 루트라 별도 `-f` 없이 base+override 자동 인식. dev 는 dev 카탈로그로, 배포는 base 단독:

```bash
# dev (소스 트리) — env.dev.example 카탈로그로 base+override 머지(로컬 빌드·핫리로드)
cp env.dev.example .env && docker compose up -d   # web http://localhost:8000. 코드 수정 반영은 `up --build -d`
# 배포 — 릴리즈 base + 루트 env.example(배포 템플릿) 채워서 base 단독
cp env.example .env && docker compose -f docker-compose.yml up -d   # GHCR 이미지 pull (override 제외)
docker compose down -v
```

dev 코드 반복은 override.yml 의 `./src` bind mount + hot reload(web=uvicorn reload, consumer=watchfiles)로 컨테이너 restart 없이 반영 — 의존성(pyproject) 변경 시에만 `up --build`. prod base 는 bind mount 없음(이미지 불변성). agent 가 붙는 VM 은 본 repo 범위 밖(OpenStack 공급).

prod 하드닝(APP_ENV=prod·강 secret·외부 secret 채널·HTTPS ingress)은 base 가 강제하지 않음 — infra env 주입으로 달성하거나 `docs/reference/contracts/env.md` + `config.py` `_validate_prod_*` contract.

---

## Dockerfile

```dockerfile
FROM ghcr.io/astral-sh/uv:0.11.16 AS uv

FROM python:3.12-slim AS builder
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./                   # 1단: 의존성만
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src                                   # 2단: 프로젝트만
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
COPY --from=builder --chown=app:app /opt/venv /opt/venv
USER app
ENTRYPOINT ["python", "-m"]
```

### 단일 이미지 + command 분기

web·consumer·worker·migrate 모두 같은 이미지를 쓰고, docker-compose의 `command` 필드로 진입점을 분기한다.

| 서비스 | command | 진입점 |
|--------|---------|--------|
| web | `python -m assessment_engine.web` | `src/assessment_engine/web/__main__.py` → uvicorn 기동 (override에서 reload) |
| consumer | `python -m assessment_engine.consumer` | `src/assessment_engine/consumer/__main__.py` → `asyncio.run(consumer.main.main())` |
| worker | `python -m assessment_engine.worker` | `src/assessment_engine/worker/__main__.py` → 보고서 생성 + install reaper |
| migrate | `alembic upgrade head` | postgres healthy 후 1회 실행하고 종료 (`restart: "no"`) |

이미지가 1개라 빌드·푸시·패치 운영 비용이 최소화된다. 네 컴포넌트가 같은 의존성 집합(SQLAlchemy·aio-pika·redis·FastAPI 등)을 쓰므로 나눠도 크기 이득이 작다.

### 빌드 캐시 전략 — 2단 uv sync

```dockerfile
COPY pyproject.toml uv.lock ./                     # ← 의존성 명세 + lock만 먼저
RUN uv sync --frozen --no-dev --no-install-project # ← deps 전용 레이어 (cache hit 대상)
COPY src ./src                                      # ← 소스 코드
RUN uv sync --frozen --no-dev --no-editable         # ← project 만 추가
```

`pyproject.toml`·`uv.lock`만 먼저 복사 → deps install → 소스 복사 → project install 순서. 소스 코드만 변경 시 1단(deps install) 레이어는 cache hit으로 재실행되지 않는다 (보통 1초 미만).

`pyproject.toml`·`uv.lock` 둘 중 하나라도 변경 시 deps 재설치(60s+) 발생.

### `uv sync --frozen` 패턴

- `uv sync`: pyproject.toml + uv.lock을 정합 검사한 후, lockfile에 고정된 트랜지티브 버전 그대로 install. `uv pip install -e .`(pyproject만 봄)와 달리 lockfile 무시 불가능 → reproducible build.
- `--frozen`: lockfile 을 재해석하지 않고 그대로 쓴다. 빌드 시점이 언제든 같은 버전 집합이 설치된다. drift 자체를 실패로 잡는 것은 `uv lock --check` 이며 `--frozen` 은 검사하지 않는다.
- `--no-dev`: pyproject `[dependency-groups].dev`(pytest·ruff·testcontainers) 미포함. prod 이미지 슬림화.
- `--no-install-project`: project 자체는 skip하고 외부 deps만 install (1단 layer cache 분리용).
- `--no-editable`: 소스를 가리키는 링크가 아니라 파일을 복사해 넣는다. 최종 이미지에 소스 트리가 없어도 동작한다. dev 는 호스트 패키지를 venv 안 같은 경로에 bind mount 해 코드 변경을 반영한다.
- `UV_PROJECT_ENVIRONMENT=/opt/venv`: venv 안 스크립트에 절대경로 shebang 이 박혀 builder 와 runtime 이 같은 경로를 써야 한다. 작업 디렉토리(`/app`)와 분리해 둔다.

베이스 이미지의 python minor 를 올릴 때는 `pyproject.toml` `requires-python`, Dockerfile `FROM` 과 `ALEMBIC_CONFIG`, 그리고 site-packages 경로가 박힌 override 3줄(마운트·watch 경로)을 함께 고친다.

### 의존성 추가 워크플로

`pyproject.toml` 을 고치면 `uv.lock` 도 같은 커밋에 넣는다 (근거·검사 방법은 `docs/guides/dependencies.md`). 둘 중 하나로 갱신한다:

```bash
uv add <pkg>          # pyproject.toml + uv.lock 동시 갱신
uv lock               # pyproject.toml 수동 편집 후 lockfile만 재생성
```

두 파일을 같은 commit에 포함. CLAUDE.md #F9 "신규 의존성" 항목과 일치.

### 멀티스테이지 + multi-arch

- 멀티스테이지 — builder 가 가상환경을 만들고 runtime 은 그것만 복사한다. uv 바이너리와 소스 트리가 최종 이미지에 남지 않는다.
- multi-arch — 릴리즈는 `linux/amd64` + `linux/arm64` 를 발행한다 (운영자 ARM 서버 직접 호환).

### CI 빌드 산출물

본 repo CI 산출물 = 서명·SBOM·provenance 된 GHCR 엔진 이미지. Dockerfile 은 dev·prod 공용 단일 이미지(dev-prod parity), docker compose 는 dev(override 핫리로드)·prod(secrets) 배포 매체. 배포는 VM 에서 `deploy.sh` 실행 — 상세는 `docs/guides/release.md`·`docs/guides/deploy.md`.

`.github/workflows/ci.yml`의 `build` job — `uv build` + 빌드된 wheel을 fresh venv에 install + import·정적 자원 포함 검증. Docker image 정합은 dev `docker compose build` 로 확인.

`.github/workflows/release.yml` — 서명·SBOM·provenance 된 멀티아치 엔진 이미지를 GHCR 로 발행. 사내 폐쇄망은 `docker save/load` 로 대응.

---

## docker-compose.yml (루트, 단일)

### 서비스 구성 (7개 — web·consumer·worker·migrate·postgres·rabbitmq·redis)

| 서비스 | 이미지 | 역할 | 적용 환경 |
|--------|--------|------|-----------|
| `postgres` | `timescale/timescaledb-ha:pg16` | 메인 DB + TimescaleDB all-in-one | dev / prod |
| `rabbitmq` | `rabbitmq:3.13-management-alpine` | 메시지 브로커 (AMQP + 관리 UI) | dev / prod |
| `redis` | `redis:7-alpine` | 캐시·온라인 TTL·PUB/SUB | dev / prod |
| `migrate` | GHCR pull (dev: override 로컬 빌드) | `alembic upgrade head` 1회 실행 후 종료. 앱 서비스가 `depends_on: service_completed_successfully`로 그 뒤 기동 | dev / prod |
| `web` | GHCR pull (dev: override 로컬 빌드) | FastAPI SSR + API + StaticFiles | dev / prod |
| `consumer` | GHCR pull (dev: override 로컬 빌드) | aio-pika 컨슈머 (server.* + task.result 큐) | dev / prod |
| `worker` | GHCR pull (dev: override 로컬 빌드) | 보고서 생성 + install task reaper | dev / prod |

### 포트 노출

| 서비스 | 호스트 포트 | 컨테이너 포트 | 용도 |
|--------|------------|--------------|------|
| postgres | `${POSTGRES_PORT:-5432}` | 5432 | psql 직접 접속 (디버그) |
| rabbitmq | `${RABBITMQ_PORT:-5672}` | 5672 | AMQP — 외부 호스트의 에이전트가 메트릭·결과 발행 |
| rabbitmq | `${RABBITMQ_MANAGEMENT_PORT:-15672}` | 15672 | 관리 UI |
| web | `${WEB_PORT:-8000}` | 8000 | HTTP — 브라우저 + `/static/*` 정적 자원 |

redis·consumer는 포트 미노출. 모두 docker 네트워크 내부에서만 접근.

### 영속 볼륨

```yaml
volumes:
  postgres_data:
  rabbitmq_data:
```

base 는 postgres·rabbitmq 영속 볼륨을 env 바인딩으로 둔다:
- postgres: `${PGDATA_HOST:-postgres_data}:/home/postgres/pgdata/data` (timescaledb-ha 실제 PGDATA 경로).
- rabbitmq: `${MQ_DATA_HOST:-rabbitmq_data}:/var/lib/rabbitmq`.
- `PGDATA_HOST`/`MQ_DATA_HOST` 미설정 시 named volume(`postgres_data`·`rabbitmq_data`), host 절대경로 주입 시 bind mount(infra Cinder `/mnt/pgdata`·`/mnt/mqdata`).

| 동작 | 명령 | named volume |
|------|------|---------------|
| 일반 종료 | `docker compose down` | 보존 |
| 완전 초기화 | `docker compose down -v` | 삭제 (host bind 주입 시엔 외부 디스크라 미삭제) |

`down -v`가 필요한 시나리오:
- ORM 모델에 컬럼/제약(`UniqueConstraint` 등) 추가 — alembic revision 미적용분 초기화 시.
- TimescaleDB hypertable 정의 변경.
- 테스트 시나리오 초기화.

redis 는 볼륨 없음 — 재시작 시 캐시·온라인 TTL 초기화 (휘발성 의도, fail-open #C3).

### 코드 마운트 (DEV 전용 — docker-compose.override.yml)

bind mount·hot reload 는 override.yml 에만 있다 (base 는 불변 이미지). override 가 앱 패키지를 컨테이너 가상환경에 덮어 코드 변경을 즉시 반영:

```yaml
# docker-compose.override.yml — web·consumer·worker·migrate 공용
volumes: [./src/assessment_engine:/opt/venv/lib/python3.12/site-packages/assessment_engine]
```

`migrations/` 가 패키지 안에 있어 이 마운트 하나로 마이그레이션 파일까지 함께 덮인다. 이미지에 설치된 패키지를 host 소스가 덮어 재빌드 없이 코드 변경이 반영되고, 의존성(pyproject) 변경 시에만 `up --build`.

조합 효과:
- `web`: uvicorn `reload=True`(`WEB_RELOAD=true`, override 주입)가 파일 변경 감지 -> 자동 재기동. 새로고침만으로 변경 확인.
- `consumer`: watchfiles 래퍼 entrypoint(override)가 `.py` 변경 시 프로세스 재시작.
- `migrate`: host `./migrations` bind 로 새 alembic revision 을 재빌드 없이 인식.
- 정적 자원(`.js`/`.css`/`.html`): dev 에서 `web/main.py` 미들웨어가 매 요청 `asset_v` 를 재발급(`app.state.dev_assets`, `app_env=="dev"` 일 때만 — APP_ENV 판정은 lifespan 단일 경로 #F4)해 `?v=` URL 이 매번 바뀌고 HTML·`/static/*` 응답에 `Cache-Control: no-store` 부여 — 브라우저 disk cache·304 까지 회피. `.py` 재시작이 없는 정적 변경(ASSET_V 가 프로세스 시작 시각 고정이라 캐시에 묻히던 경로)도 새로고침만으로 반영. prod 는 본 분기 비활성(cdn·long-cache).

prod (base 단독): override 미배포라 위 bind mount·reload 전부 없음 — `Dockerfile` 빌드 결과(불변 이미지)만 사용. `.dockerignore` 가 `.env` 를 이미지에서 제외하고, base 는 코드 마운트가 없어 호스트 `.env` 가 컨테이너에 노출되지 않는다 (dev override bind 는 `./src` 한정이라 루트 `.env` 미포함).

### Redis 인라인 설정

```yaml
command: redis-server --maxmemory 256mb --maxmemory-policy volatile-lru
```

별도 설정 파일 없이 커맨드라인으로 redis.conf 옵션을 전달.

- `maxmemory 256mb` — B2B 내부 포털 규모 기준. 키 수·값 크기가 작아 충분.
- `volatile-lru` — TTL이 설정된 키만 evict 대상. TTL 없는 `cache:resolve:{public_id}` 키를 보호하면서 만료 임박 캐시·온라인 키를 우선 제거.
- 멱등성 키(`idempotent:{message_id}`)는 24h TTL이 있어 evict 가능 → at-most-once 트레이드오프와 연결 (`docs/explanation/tradeoffs.md` T1, T11).

### 환경변수 주입

`.env` 파일 → `env_file:` 으로 전체 주입 후, docker 내부 서비스명을 `environment:`에서 오버라이드.

```yaml
env_file: .env
environment:
  POSTGRES_HOST: postgres   # .env의 localhost 값 오버라이드
  REDIS_HOST: redis
  RABBITMQ_HOST: rabbitmq   # consumer — web 도 task publish 위해 사용
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
- `start_period: 10s` — web lifespan(broker channel·redis pool·http client 초기화)이 완료될 시간 확보. 스키마는 `migrate` 서비스가 선행(`depends_on: service_completed_successfully`). 이 구간의 실패는 `retries`에 포함되지 않는다.
- 헬스 엔드포인트(`/health`)는 단순 `{"status": "ok"}` JSON. DB·Redis 연결 검사 안 함 (deep healthcheck 안 함).

### 기동 순서 (`depends_on`)

```
postgres ─ healthy ─▶ migrate (alembic upgrade head, 1회 실행 후 exit)
                          │
                          ▼ service_completed_successfully
            ┌──────┬──────┴
            ▼      ▼
           web   consumer
            ▲      ▲
   redis ───┴──────┤
rabbitmq ──────────┘
```

모든 환경(dev·staging·prod) Alembic 단일 진실. `migrate` 컨테이너가 schema 준비 완료를 보장한 뒤 앱 2종 기동.

### restart 정책

모든 서비스 `restart: unless-stopped`:
- 컨테이너 비정상 종료(OOM·프로세스 크래시·healthcheck 누적 실패) 시 Docker가 자동 재시작.
- `docker compose down`으로 명시적으로 내릴 때는 재시작 안 함.
- 호스트 재부팅 시 자동 기동 (Docker daemon이 enable되어 있다면).

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
| `docker-compose.yml` (루트) | 부분 | 부분 | `docker compose up -d` (변경된 서비스만 재생성) |
| ORM 모델 (컬럼·제약 추가) | 새 모델 로드는 reload되나 DB 스키마는 미반영 | 동일 | `alembic revision --autogenerate` → `docker compose restart migrate` → 앱 서비스 재기동. 마이그레이션 누락 시 `alembic check` 차단 |

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

본 문서는 docker-compose 관점(컨테이너 정의·볼륨·헬스체크)만 다룬다. RabbitMQ broker 자체의 운영 — vhost 개념·권한 모델·토폴로지·dev/prod 분기·Production 전환 체크리스트는 `docs/reference/rabbitmq.md` 단일 진실.