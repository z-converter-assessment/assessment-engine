# Docker

## 사용 맥락

docker-compose는 **엔진 그 자체**다. web·consumer·DB·MQ·Redis 전체 스택을 단일 `docker-compose.yml`로 구성하며, 이 묶음이 고객사 네트워크 내에 설치되는 배포 단위다.

개발 환경에서는 호스트 머신에서 docker-compose를 올리고, Vagrant VM들이 에이전트를 실행해 RabbitMQ에 메시지를 발행하는 구조로 전체 파이프라인을 검증한다 (`dev-up.sh`가 두 단계를 순서대로 기동).

Python 애플리케이션(web·consumer)만 로컬 빌드(`Dockerfile`)하고, 나머지 인프라 서비스(postgres·rabbitmq·redis)는 공식 이미지를 그대로 사용한다.

---

## 파일 구조

```
Dockerfile            — web·consumer 공용 이미지
docker-compose.yml    — 전체 스택 (5개 서비스 + 네임드 볼륨)
.dockerignore         — COPY . . 시 제외 경로
dev-up.sh             — Docker → web 헬스체크 → Vagrant 순서 기동
dev-down.sh           — Vagrant destroy → docker compose down -v
```

---

## Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install uv
COPY pyproject.toml .
RUN uv pip install --system --no-cache -e .
COPY . .
```

### 단일 이미지 + command 분기

web·consumer 양쪽이 **같은 이미지**를 쓰고, docker-compose의 `command` 필드로 진입점을 분기한다.

| 서비스 | command | 진입점 |
|--------|---------|--------|
| web | `python -m web` | `src/assessment_engine/web/__main__.py` → uvicorn 기동 (reload 모드) |
| consumer | `python -m consumer` | `src/assessment_engine/consumer/__main__.py` → `asyncio.run(consumer.main.main())` |

이미지가 1개라 빌드/푸시·패치 운영 비용이 최소화된다. 의존성 패키지(SQLAlchemy·aio-pika·redis·FastAPI 등)도 양쪽이 모두 사용하므로 분리 이득이 적다.

### 빌드 캐시 전략 — 2단계 COPY

```dockerfile
COPY pyproject.toml .              # ← 의존성 명세만 먼저
RUN uv pip install --system ...    # ← 의존성 레이어 (캐시 가능)
COPY . .                            # ← 소스 코드
```

`pyproject.toml`만 먼저 복사 → `uv pip install` → `COPY . .` 순서로 분리. 소스 코드만 변경 시 의존성 레이어가 캐시되어 빌드가 빨라진다 (보통 1초 미만).

`pyproject.toml` 변경 시에만 의존성 재설치(60s+) 발생.

### `uv pip install --system --no-cache -e .`

- `uv`: pip의 빠른 대체. 의존성 해결과 설치 속도가 pip 대비 10~100배.
- `--system`: 가상환경 없이 시스템 Python에 설치 (컨테이너 내부라 격리 의미 없음).
- `--no-cache`: pip 캐시 비활성화로 이미지 크기 축소.
- `-e .`: editable 설치. 호스트 마운트(`./:/app`)와 결합해 코드 변경 즉시 반영.

### 멀티스테이지/multi-arch 미사용

- 멀티스테이지 빌드 미적용 — Python 슬림 이미지가 이미 작고 (~150MB), 빌드 도구가 추가로 필요 없음.
- multi-arch 빌드도 미적용 — 운영 타겟이 Linux/x86_64로 고정.

---

## docker-compose.yml

### 서비스 구성

| 서비스 | 이미지 | 역할 |
|--------|--------|------|
| `postgres` | `timescale/timescaledb:latest-pg16` | 메인 DB + TimescaleDB 확장 |
| `rabbitmq` | `rabbitmq:3.13-management-alpine` | 메시지 브로커 (AMQP + 관리 UI) |
| `redis` | `redis:7-alpine` | 캐시·온라인 TTL·PUB/SUB |
| `web` | 로컬 빌드 | FastAPI SSR + API + StaticFiles |
| `consumer` | 로컬 빌드 | aio-pika 컨슈머 |

### 포트 노출

| 서비스 | 호스트 포트 | 컨테이너 포트 | 용도 |
|--------|------------|--------------|------|
| postgres | `${POSTGRES_PORT:-5432}` | 5432 | psql 직접 접속 (디버그) |
| rabbitmq | `${RABBITMQ_PORT:-5672}` | 5672 | AMQP — Vagrant VM 에이전트가 `10.0.2.2:5672`로 접근 |
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
| 일반 종료 | `docker compose down` | **보존** |
| 완전 초기화 | `docker compose down -v` | **삭제** |

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

호스트 프로젝트 루트를 컨테이너 `/app`에 마운트. `Dockerfile`의 `COPY . .`는 빌드 시점 복사라 이후 호스트 변경이 반영 안 되지만, 이 마운트가 위에 덮여 **코드 변경이 컨테이너 내부에 즉시 노출**된다.

조합 효과:
- `web`: uvicorn `reload=True` (`src/assessment_engine/web/__main__.py`)가 파일 변경 감지 → 자동 재기동. 새로고침만으로 변경 확인.
- `consumer`: reload 없음. 변경 후 `docker compose restart consumer` 필요.
- `src/assessment_engine/web/static/js/chart-utils.js` 같은 정적 자원도 별도 빌드 없이 즉시 서빙.

**프로덕션 마이그레이션 가이드**: `volumes: ./:/app` 제거 → `Dockerfile`의 `COPY . .` 결과만 사용. uvicorn `reload=True`도 제거 (`src/assessment_engine/web/__main__.py`).

**보안 — `.env` 노출**: 코드 마운트의 부작용으로 호스트 `.env`가 컨테이너 안 `/app/.env`에 그대로 노출된다 (`-rw-r--r--` root 소유). `.dockerignore`에 `.env`가 명시되어 있어 **이미지 빌드(`COPY . .`) 시점에는 제외**되지만, 런타임 코드 마운트는 빌드 산출물이 아니므로 `.dockerignore`가 적용되지 않는다. 프로덕션에서 코드 마운트를 제거하면 `.env`도 자동으로 컨테이너 안에서 사라진다.

### Redis 인라인 설정

```yaml
command: redis-server --maxmemory 256mb --maxmemory-policy volatile-lru
```

별도 설정 파일 없이 커맨드라인으로 redis.conf 옵션을 전달.

- `maxmemory 256mb` — B2B 내부 포털 규모 기준. 키 수·값 크기가 작아 충분.
- `volatile-lru` — TTL이 설정된 키만 evict 대상. **TTL 없는 `cache:resolve:{public_id}` 키를 보호**하면서 만료 임박 캐시·온라인 키를 우선 제거.
- 멱등성 키(`idempotent:{message_id}`)는 24h TTL이 있어 evict 가능 → at-most-once 트레이드오프와 연결 (`docs/tradeoffs.md` T1, T11).

### 환경변수 주입

`.env` 파일 → `env_file:` 으로 전체 주입 후, docker 내부 서비스명을 `environment:`에서 오버라이드.

```yaml
env_file: .env
environment:
  POSTGRES_HOST: postgres   # .env의 localhost 값 오버라이드
  REDIS_HOST: redis
  RABBITMQ_HOST: rabbitmq   # consumer만 — web은 RabbitMQ 직접 사용 안 함
```

호스트에서 직접 실행 시 `.env`의 기본값(`localhost`)을 쓰고, 컨테이너에서는 `environment` 블록이 오버라이드.

| 변수 | 호스트 실행 | 컨테이너 |
|------|-------------|---------|
| POSTGRES_HOST | `localhost` (.env) | `postgres` (compose) |
| REDIS_HOST | `localhost` (.env, 명시 없음) | `redis` (compose) |
| RABBITMQ_HOST | `localhost` (.env) | `rabbitmq` (compose, consumer만) |

### healthcheck

| 서비스 | 체크 명령 | interval | timeout | retries | start_period |
|--------|----------|----------|---------|---------|--------------|
| postgres | `pg_isready -U <user>` | 5s | 5s | 5 | — |
| rabbitmq | `rabbitmq-diagnostics ping` | 10s | 5s | 5 | — |
| redis | `redis-cli ping` | 5s | 5s | 5 | — |
| web | `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"` | 5s | 5s | 5 | **10s** |

`web` healthcheck:
- 명령으로 `python -c ...`를 쓰는 이유: `curl`이 python:3.12-slim 이미지에 없음. python 표준 라이브러리로 해결.
- `start_period: 10s` — web lifespan(`CREATE EXTENSION + create_all + create_hypertable`)이 완료될 시간 확보. 이 구간의 실패는 `retries`에 포함되지 않는다.
- 헬스 엔드포인트(`/health`)는 단순 `{"status": "ok"}` JSON. DB·Redis 연결 검사 안 함 (deep healthcheck 안 함).

### 기동 순서 (`depends_on`)

```
postgres ──┐
           ├──▶ web (service_healthy) ──▶ consumer
redis ─────┤                              ↑
           └──────────────────────────────┤
rabbitmq ─────────────────────────────────┘
```

**consumer가 web 헬스체크 통과 후 기동**하는 이유:
1. web lifespan이 `CREATE EXTENSION timescaledb` + `Base.metadata.create_all` + `create_hypertable(...)` 수행.
2. consumer가 먼저 DB 접근하면 테이블이 없어 INSERT 실패 → DLQ 누적.
3. web 헬스체크 통과 = 스키마 준비 완료.

이 의존 관계는 **DEV 전용**. 프로덕션에서는 Alembic 마이그레이션을 분리해 별도 잡으로 실행하고 `consumer depends_on web` 제거 — `docs/tradeoffs.md` T4.

### restart 정책

모든 서비스 `restart: unless-stopped`:
- 컨테이너 비정상 종료(OOM·프로세스 크래시·healthcheck 누적 실패) 시 Docker가 자동 재시작.
- `docker compose down`으로 명시적으로 내릴 때는 재시작 안 함.
- 호스트 재부팅 시 자동 기동 (Docker daemon이 enable되어 있다면).

---

## dev-up.sh / dev-down.sh

### dev-up.sh
```
[1/3] docker compose up -d --build
[2/3] web 헬스체크 대기 (최대 120초)
[3/3] vagrant up
```

- web 헬스체크 통과 후에만 vagrant up 진입 — 에이전트가 처음 inventory를 발행할 때 RabbitMQ가 healthy 상태여야 정상 처리.
- 헬스체크 타임아웃(120s) 초과 시 web 로그 30라인 dump 후 exit.

### dev-down.sh
```
[1/2] vagrant destroy -f (VM이 존재할 때만)
[2/2] docker compose down -v
```

`-v`로 postgres_data 볼륨 삭제. 다음 `dev-up.sh`는 빈 DB에서 시작하므로 lifespan이 모든 hypertable 신규 생성.

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
| `docker-compose.yml` | 부분 | 부분 | `docker compose up -d` (변경된 서비스만 재생성) |
| ORM 모델 (컬럼·제약 추가) | 새 모델 로드는 reload되나 **DB 스키마는 미반영** | 동일 | `docker compose down -v && docker compose up -d --build` |

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

본 문서는 docker-compose 관점(컨테이너 정의·볼륨·헬스체크)만 다룬다. RabbitMQ broker 자체의 운영 — vhost 개념·권한 모델·토폴로지·dev/prod 분기·Production 전환 체크리스트는 `docs/components/rabbitmq.md` 단일 진실.