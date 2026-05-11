# Alembic 마이그레이션

PROD schema 관리. DEV는 `web` lifespan의 `create_all` + `create_hypertable` 사용 (빠른 반복) — Alembic 미사용.

정책 단일 진실: CLAUDE.md #C4 (모델 변경 시 ORM + Alembic revision 동시 갱신 의무, `alembic check` 통과 의무, drift 0건). 본 문서는 그 정책을 따르는 도구 사용법·절차만.

## 본 프로젝트의 Alembic

ORM(`Base.metadata`)과 DB schema의 diff를 `migrations/versions/*.py` revision 파일로 기록하고 `alembic upgrade head` 한 줄로 누적 적용한다. DB의 `alembic_version` 테이블이 현재 적용된 revision id를 보관 — 환경마다 자기 상태를 알아 누락 revision만 자동 실행. 도구 일반론은 공식 문서.

`create_all`은 신규 CREATE만 가능 (ALTER·DROP 안 됨). DEV는 데이터 손실 허용이라 `docker compose down -v` + `create_all`로 재생성. PROD는 데이터 보존 필요라 Alembic으로 ALTER 발행.

핵심 용어 (본 문서에서 사용)

| 용어 | 의미 |
|------|------|
| revision | 한 마이그레이션 파일. `revision`(자기 id) + `down_revision`(이전 id) 변수로 linked list 구성 |
| head | 최신 revision. `upgrade head` = 끝까지 적용 |
| autogenerate | ORM vs DB schema diff -> revision 파일 자동 생성. `create_hypertable` 등 확장은 수동 보강 의무 |
| `alembic_version` 테이블 | DB 자동 생성. 현재 revision id 저장 |

```
migrations/
├── env.py                ← Base.metadata + asyncpg 비동기 패턴 + web_settings.database_url 주입
├── script.py.mako        ← 신규 revision 템플릿
├── versions/             ← 마이그레이션 파일 (YYYYMMDD 순 자동 생성)
└── README
alembic.ini               ← 설정 (sqlalchemy.url은 env.py가 런타임 주입)
```

## DB URL 주입

`alembic.ini`의 `sqlalchemy.url`은 비어있음. `migrations/env.py`가 `web_settings.database_url`로 런타임 주입 — `.env` / Docker secrets 등 동일 환경변수 정책 활용.

호스트에서 직접 alembic 실행 시 `POSTGRES_HOST` env 명시 필요 (default `postgres`는 docker-compose 서비스명):
```bash
POSTGRES_HOST=localhost .venv/bin/alembic upgrade head
```

PROD 권장 — docker-compose 컨테이너 안에서 실행 (env 자동):
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm web alembic upgrade head
```

## 명령

| 작업 | 명령 |
|------|------|
| 현재 적용 revision 확인 | `alembic current` |
| 모든 revision 이력 | `alembic history --verbose` |
| 미적용 revision 미리보기 (offline SQL) | `alembic upgrade head --sql` |
| head로 업그레이드 | `alembic upgrade head` |
| 한 단계 다운그레이드 | `alembic downgrade -1` |
| 모델 diff로 신규 revision 생성 | `alembic revision --autogenerate -m "<설명>"` |
| 빈 revision 생성 (수동 작성) | `alembic revision -m "<설명>"` |

## 신규 마이그레이션 작성 워크플로우

전체 흐름: ORM 변경 -> DEV 검증 -> autogenerate baseline 준비 -> revision 생성 -> 수동 보강 -> 라운드트립 검증 -> commit.

### 1. ORM 모델 변경
`src/assessment_engine/db/models/*.py`에 컬럼 추가/타입 변경 등. ORM이 단일 진실 — 마이그레이션은 ORM을 따라간다.

### 2. DEV에서 변경 검증
```bash
docker compose down -v && docker compose up
```
볼륨 통째로 날리고 재기동 -> lifespan의 `create_all`이 새 정의대로 처음부터 생성. 정상 동작·쿼리 확인.

### 3. autogenerate baseline 준비
autogenerate는 "현재 DB schema"와 "ORM 모델"의 diff를 추출. 따라서 비교 baseline = 기존 revision까지 적용된 DB.

```bash
docker compose down -v
docker compose up -d postgres
docker compose exec postgres psql -U assessment -d assessment -c \
  "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"
POSTGRES_HOST=localhost .venv/bin/alembic upgrade head      # 기존 revision까지 적용
```
TimescaleDB extension 먼저 만들어야 함 — `create_hypertable` 호출 시 extension 부재면 실패.

### 4. autogenerate로 revision 생성
```bash
POSTGRES_HOST=localhost .venv/bin/alembic revision --autogenerate -m "add boot_time to ..."
```
`migrations/versions/<id>_add_boot_time.py` 파일 자동 생성. ORM과 DB schema 차이만큼 `upgrade()`/`downgrade()` 본문이 채워진다.

### 5. 수동 보강 (autogenerate 미지원 영역)
생성된 파일을 반드시 검토하고 다음 항목은 직접 추가:
- `create_hypertable` / TimescaleDB 정책 / continuous aggregate
- `CREATE EXTENSION`
- partial index의 `postgresql_where` 일부
- CHECK 제약 / JSONB GIN 옵션 일부

시계열 새 테이블 추가 시 의무:
```python
op.execute("SELECT create_hypertable('table', 'collected_at', if_not_exists => true)")
```

`downgrade()`도 검토 — autogenerate가 만든 DROP COLUMN이 데이터 손실을 무릅쓰는 경우라면 운영자가 인지하도록 주석을 더해 둔다.

### 6. 라운드트립 검증
```bash
POSTGRES_HOST=localhost .venv/bin/alembic upgrade head        # 정상 적용
POSTGRES_HOST=localhost .venv/bin/alembic downgrade -1        # 롤백 가능
POSTGRES_HOST=localhost .venv/bin/alembic upgrade head        # 재적용
```
upgrade -> downgrade -> upgrade가 깔끔하게 통과해야 PR-ready. downgrade에서 에러 나면 `downgrade()` 본문이 잘못된 것 — 보강.

### 7. commit
마이그레이션 파일 + 모델 변경을 한 커밋으로 (CLAUDE.md #C4 의무).

## PROD 배포 운영

`web/main.py` lifespan은 `app_env=prod`일 때 schema bootstrap skip. 즉 web 기동 전 Alembic이 스키마를 갖춰야 함.

배포 절차 (운영자):
```bash
# 1. 신규 컨테이너 빌드 (DB는 기존)
docker compose -f docker-compose.yml -f docker-compose.prod.yml build

# 2. Alembic 사전 적용 (one-shot)
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm web alembic upgrade head

# 3. 정상 기동
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

failure 시: web이 schema 부재로 5xx 응답. `docker compose logs web` 확인 후 Alembic 다시 시도.

## 자동화 미도입 — 수동 실행 결정

| 옵션 | 결정 |
|------|------|
| 별도 `migrate` service (compose에 의존) | 미도입 — over-engineering. 운영자 인지 가시성·롤백 통제력 약화 |
| web 컨테이너 entrypoint에 `alembic upgrade head` 자동 | 미도입 — 동시 N web 인스턴스 race + 수동 검토 단계 사라짐 |
| 운영자 수동 실행 | 채택 — 한 줄 명령, 결과 즉시 가시, 롤백 의사결정 명확 |

## DEV vs PROD 책임 매트릭스

| 작업 | DEV | PROD |
|------|-----|------|
| Schema 생성 | web lifespan `create_all` + `create_hypertable` 자동 | Alembic `upgrade head` 사전 수동 |
| 컬럼 추가 | `docker compose down -v` 후 재기동 | `alembic revision --autogenerate` + `upgrade head` |
| Hypertable 변환 | lifespan 자동 | 마이그레이션 파일에 `op.execute("SELECT create_hypertable(...)")` 수동 |
| 검증 | pytest (testcontainers) | staging Alembic 적용 -> smoke test |

DEV `create_all`과 Alembic은 같은 schema를 만든다 (CLAUDE.md #C4). 한 쪽만 갱신 시 환경 간 drift 발생.
