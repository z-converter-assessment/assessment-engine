# Docker 이미지·compose 구성

정책: CLAUDE.md #A. 본 문서는 이미지와 compose 구성이 지금 어떻게 되어 있는지를 다룬다. 기동·반영·디버깅 절차는 `docs/guides/local-dev.md`, 배포는 `docs/guides/deploy.md` 가 갖는다.

docker-compose 는 엔진 그 자체다 — web·consumer·worker·migrate·postgres·rabbitmq·redis 7 서비스가 고객사 네트워크 안 설치 단위다. Python 앱 넷은 단일 이미지에 command 로 갈리고, 인프라 셋은 공식 이미지를 쓴다.

---

## 이미지

### 단일 이미지 + command 분기

| 서비스 | command | 진입점 |
|--------|---------|--------|
| web | `python -m assessment_engine.web` | `web/__main__.py` -> uvicorn 기동 |
| consumer | `python -m assessment_engine.consumer` | `consumer/__main__.py` -> `asyncio.run(consumer.main.main())` |
| worker | `python -m assessment_engine.worker` | `worker/__main__.py` -> 보고서 생성 + install reaper |
| migrate | `alembic upgrade head` | postgres healthy 후 1회 실행하고 종료 (`restart: "no"`) |

이미지가 1개라 빌드·푸시·패치 비용이 최소다. 네 컴포넌트가 같은 의존성 집합(SQLAlchemy·aio-pika·redis·FastAPI)을 쓰므로 나눠도 크기 이득이 작다. dev/prod Dockerfile 도 나누지 않는다 (parity — dev 편의는 override bind mount 로만).

### multi-stage

builder 가 가상환경을 만들고 runtime 은 그것만 복사한다. uv 바이너리와 소스 트리가 최종 이미지에 남지 않는다. 릴리즈는 `linux/amd64` + `linux/arm64` 를 발행한다 (운영자 ARM 서버 직접 호환).

### 빌드 캐시 — 2단 uv sync

```dockerfile
COPY pyproject.toml uv.lock ./                     # 의존성 명세 + lock 만 먼저
RUN uv sync --frozen --no-dev --no-install-project # deps 전용 레이어 (cache hit 대상)
COPY src ./src                                     # 소스 코드
RUN uv sync --frozen --no-dev --no-editable        # project 만 추가
```

소스만 바뀌면 1단 레이어가 cache hit 이라 재실행되지 않는다 (보통 1초 미만). `pyproject.toml`·`uv.lock` 둘 중 하나라도 바뀌면 deps 재설치(60s+)가 돈다.

### uv 플래그

- `uv sync` — pyproject 와 lock 을 정합 검사한 뒤 lockfile 에 고정된 트랜지티브 버전 그대로 install. `uv pip install -e .`(pyproject 만 봄)와 달리 lockfile 을 무시할 수 없다.
- `--frozen` — lockfile 을 재해석하지 않고 그대로 쓴다. 빌드 시점이 언제든 같은 버전 집합이 깔린다. drift 자체를 실패로 잡는 것은 `uv lock --check` 이고 `--frozen` 은 검사하지 않는다.
- `--no-dev` — `[dependency-groups].dev` 미포함. prod 이미지 슬림화.
- `--no-install-project` — project 를 skip 하고 외부 deps 만 install (1단 layer cache 분리용).
- `--no-editable` — 소스를 가리키는 링크가 아니라 파일을 복사해 넣는다. 최종 이미지에 소스 트리가 없어도 동작한다. dev 는 호스트 패키지를 venv 안 같은 경로에 bind mount 해 코드 변경을 반영한다.
- `UV_PROJECT_ENVIRONMENT=/opt/venv` — venv 안 스크립트에 절대경로 shebang 이 박혀 builder 와 runtime 이 같은 경로를 써야 한다. 작업 디렉토리(`/app`)와 분리해 둔다.

베이스 이미지의 python minor 를 올릴 때는 `pyproject.toml` `requires-python`, Dockerfile `FROM` 과 `ALEMBIC_CONFIG`, site-packages 경로가 박힌 override 3줄(마운트·watch 경로)을 함께 고친다.

### CI 산출물

본 repo CI 산출물은 서명·SBOM·provenance 된 GHCR 엔진 이미지 하나다.

`ci.yml` 의 `build` job 은 `uv build` 로 wheel 을 만들어 fresh venv 에 install 하고 import·정적 자원 포함을 검증한다. Docker image 정합은 dev `docker compose build` 로 확인한다. `release.yml` 은 멀티아치 이미지를 GHCR 로 발행한다 — 상세는 `docs/guides/release.md`·`docs/guides/deploy.md`.

---

## compose 3 파일

base(prod-safe) 위에 dev override 또는 prod overlay 하나를 얹는다.

| 파일 | 역할 |
|------|------|
| `docker-compose.yml` | prod-safe base. 앱 서비스 `build:` 없음, GHCR 이미지 핀 pull, bind mount 없음. 볼륨 env 바인딩(`PGDATA_HOST`·`MQ_DATA_HOST`) |
| `docker-compose.override.yml` | dev 전용. 소스 빌드·`./src` bind mount·hot reload. 파일명 규칙으로 base 에 자동 머지 |
| `docker-compose.prod.yml` | prod overlay. file-secret 배선(`secrets:` 최상위 + 서비스 참조)만 얹는다. `COMPOSE_FILE` 로 명시해야 붙는다 |

어느 조합이 붙는지는 `.env` 의 `COMPOSE_FILE` 이 정한다. 이 변수가 없으면 compose 기본 규칙이 base+override 를 합치고, `.env.example`(배포 템플릿)은 `docker-compose.yml:docker-compose.prod.yml` 을 명시해 override 를 뺀다.

base 는 환경 색을 담지 않는다. prod 하드닝(강 secret·외부 secret 채널·HTTPS ingress)도 base 가 강제하지 않는다 — 외부 인프라 주입으로 달성하거나 `docs/reference/contracts/env.md` 의 검증 contract 가 받는다.

---

## base 사양

### 서비스 구성

| 서비스 | 이미지 | 역할 |
|--------|--------|------|
| `postgres` | `timescale/timescaledb-ha:pg16` | 메인 DB + TimescaleDB all-in-one |
| `rabbitmq` | `rabbitmq:3.13-management-alpine` | 메시지 브로커 (AMQP + 관리 UI) |
| `redis` | `redis:7-alpine` | 캐시·온라인 TTL·PUB/SUB |
| `migrate` | GHCR pull (dev: override 로컬 빌드) | `alembic upgrade head` 1회 실행 후 종료 |
| `web` | GHCR pull (dev: override 로컬 빌드) | FastAPI SSR + API + StaticFiles |
| `consumer` | GHCR pull (dev: override 로컬 빌드) | aio-pika 컨슈머 (server.* + task.result 큐) |
| `worker` | GHCR pull (dev: override 로컬 빌드) | 보고서 생성 + install task reaper |

### 포트 노출

| 서비스 | 호스트 포트 | 컨테이너 포트 | 용도 |
|--------|------------|--------------|------|
| postgres | `127.0.0.1:${POSTGRES_PORT:-5432}` | 5432 | psql 직접 접속 (디버그) |
| redis | `127.0.0.1:${REDIS_PORT:-6379}` | 6379 | redis-cli 직접 접속 (디버그) |
| rabbitmq | `${RABBITMQ_PORT:-5672}` | 5672 | AMQP — 외부 호스트의 에이전트가 메트릭·결과 발행 |
| rabbitmq | `127.0.0.1:${RABBITMQ_MANAGEMENT_PORT:-15672}` | 15672 | 관리 UI (SSH 터널) |
| web | `${WEB_PORT:-8000}` | 8000 | HTTP — 브라우저 + `/static/*` 정적 자원 |

AMQP 5672 만 0.0.0.0 이다 — 외부 호스트의 에이전트가 발행하는 통로라 노출이 필수다. 나머지는 loopback 에 묶어 VM 로컬 ops 접근으로 제한한다. consumer·worker 는 포트를 열지 않는다. 바인딩은 base 가 정하므로 dev·prod 가 같다.

### 영속 볼륨

postgres·rabbitmq 만 영속 볼륨을 갖고, env 바인딩으로 둔다.

- postgres — `${PGDATA_HOST:-postgres_data}:/home/postgres/pgdata/data` (timescaledb-ha 실제 PGDATA 경로)
- rabbitmq — `${MQ_DATA_HOST:-rabbitmq_data}:/var/lib/rabbitmq`

`PGDATA_HOST`·`MQ_DATA_HOST` 미설정이면 named volume 이고, host 절대경로를 주입하면 bind mount 다 (infra Cinder `/mnt/pgdata`·`/mnt/mqdata`). named volume 은 `docker compose down` 에서 보존되고 `down -v` 에서 삭제된다 — host bind 를 주입했으면 외부 디스크라 삭제되지 않는다.

redis 는 볼륨이 없다 — 재시작하면 캐시·온라인 TTL 이 초기화된다 (휘발성 의도, fail-open #C3).

### Redis 인라인 설정

```yaml
command: redis-server --maxmemory 256mb --maxmemory-policy volatile-lru
```

설정 파일 없이 커맨드라인으로 넘긴다.

- `maxmemory 256mb` — B2B 내부 포털 규모 기준. 키 수·값 크기가 작아 충분하다.
- `volatile-lru` — TTL 있는 키만 evict 대상이다. TTL 없는 `cache:resolve:{public_id}` 를 보호하면서 만료 임박 캐시·온라인 키를 먼저 버린다.
- 멱등성 키(`idempotent:{message_id}`)는 24h TTL 이 있어 evict 될 수 있다 -> at-most-once 트레이드오프 (`docs/explanation/tradeoffs.md` T1·T11).

### 환경변수 주입

`env_file:` 로 `.env` 전체를 넣고, docker 내부 서비스명만 `environment:` 가 덮는다.

| 변수 | 호스트 실행 | 컨테이너 |
|------|-------------|---------|
| `POSTGRES_HOST` | `localhost` (.env) | `postgres` |
| `REDIS_HOST` | `localhost` (.env) | `redis` |
| `RABBITMQ_HOST` | `localhost` (.env) | `rabbitmq` (MQ 사용 서비스 한정) |

키 카탈로그와 secret 채널은 `docs/reference/contracts/env.md` 가 갖는다.

### healthcheck

명령과 주기는 `docker-compose.yml` 의 각 서비스 `healthcheck:` 가 정한다. 두 가지만 여기 적는다.

postgres 는 `-h 127.0.0.1` 을 붙여 TCP 로 물어본다 — 초기화 중 unix socket 만 열린 구간을 healthy 로 오판하지 않기 위해서다.

web 은 명령이 `python -c ...` 다. `curl` 이 `python:3.12-slim` 에 없어서 표준 라이브러리로 대신한다. `start_period` 는 lifespan(broker channel·redis pool·http client 초기화)이 끝날 시간을 준다 — 이 구간의 실패는 `retries` 에 포함되지 않는다. 엔드포인트(`/health`)는 `{"status": "ok"}` 만 돌려주고 DB·Redis 연결은 검사하지 않는다.

### 기동 순서

```
postgres ─ healthy ─▶ migrate (alembic upgrade head, 1회 실행 후 exit)
                          │
                          ▼ service_completed_successfully
            ┌──────┬──────┴──────┐
            ▼      ▼             ▼
           web   consumer      worker
            ▲      ▲             ▲
   redis ───┼──────┼─────────────┤
rabbitmq ───┴──────┴─────────────┘
```

앱 3종이 postgres·redis·rabbitmq healthy 와 migrate 완료를 모두 기다린다 (`x-app-base` 공통 `depends_on`). 모든 환경에서 Alembic 이 스키마 단일 진실이고, `migrate` 컨테이너가 준비 완료를 보장한 뒤에야 앱이 뜬다.

### restart 정책

앱·인프라는 `unless-stopped`, `migrate` 는 `no` 다. 비정상 종료(OOM·크래시·healthcheck 누적 실패)면 Docker 가 다시 띄우고, `docker compose down` 으로 내릴 때는 다시 뜨지 않는다. 호스트 재부팅 시 자동 기동한다 (Docker daemon 이 enable 되어 있다면).

`deploy.sh` 의 health gate 가 이 정책으로 서비스 종류를 가른다 — `no` 면 `exited 0` 을, 나머지는 `running` + healthcheck 통과를 요구한다.

---

## dev override

bind mount 와 hot reload 는 override 에만 있다. base 는 불변 이미지다.

```yaml
volumes: [./src/assessment_engine:/opt/venv/lib/python3.12/site-packages/assessment_engine]
```

앱 패키지를 컨테이너 가상환경에 덮어씌운다. `migrations/` 가 패키지 안에 있어 이 마운트 하나로 마이그레이션 파일까지 함께 덮인다.

- `web` — uvicorn `reload=True`(`WEB_RELOAD=true`, override 주입)가 파일 변경을 감지해 재기동한다.
- `consumer`·`worker` — watchfiles 래퍼 entrypoint 가 `.py` 변경 시 프로세스를 재시작한다 (uvicorn 이 아니라서).
- `migrate` — 새 alembic revision 을 재빌드 없이 인식한다.
- 정적 자원 — `web/main.py` 미들웨어가 매 요청 `asset_v` 를 재발급해(`app.state.dev_assets`) `?v=` URL 이 매번 바뀌고 HTML·`/static/*` 응답에 `Cache-Control: no-store` 가 붙는다. 브라우저 disk cache 와 304 까지 회피하므로 `.py` 재시작 없는 정적 변경도 새로고침만으로 반영된다. prod 는 이 분기가 꺼진다 (cdn·long-cache).

prod 에는 override 가 배포되지 않아 위 전부가 없다 — Dockerfile 빌드 결과만 쓴다. `.dockerignore` 가 `.env` 를 이미지에서 빼고, base 에 코드 마운트가 없어 호스트 `.env` 가 컨테이너로 새지 않는다 (dev override bind 도 `./src` 한정이라 루트 `.env` 를 포함하지 않는다).

---

## 관련 문서

- `docs/guides/local-dev.md` — 기동·코드 반영·디버깅 절차
- `docs/guides/deploy.md` — VM rollout·배포 기동
- `docs/reference/contracts/env.md` — 환경변수 키 카탈로그·secret 채널
- `docs/reference/rabbitmq.md` — broker 자체 운영 (vhost·권한·토폴로지)
