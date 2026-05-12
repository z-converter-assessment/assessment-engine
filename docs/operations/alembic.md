# Alembic 마이그레이션

dev·staging·prod 모든 환경의 DB schema를 Alembic 마이그레이션 1개 진실로 관리. ORM 모델 변경 시 반드시 마이그레이션 파일도 함께 만들어야 한다.

정책 단일 진실: CLAUDE.md #C4 (모델 변경 시 ORM + Alembic revision 동시 갱신 의무, `alembic check` 통과 의무, drift 0건). 본 문서는 그 정책을 따르는 도구 사용법·절차만.

## 본 프로젝트의 Alembic

ORM(`Base.metadata`)과 DB schema의 diff를 `migrations/versions/*.py` revision 파일로 기록하고 `alembic upgrade head` 한 줄로 누적 적용한다. DB의 `alembic_version` 테이블이 현재 적용된 revision id를 보관 — 환경마다 자기 상태를 알아 누락 revision만 자동 실행. 도구 일반론은 공식 문서.

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
├── versions/             ← 마이그레이션 파일 (revision id 순 자동 생성)
└── README
alembic.ini               ← 설정 (sqlalchemy.url은 env.py가 런타임 주입)
```

## 자동 적용 — migrate 컨테이너

docker-compose에 `migrate` 서비스가 정의되어 있다. 동작:

1. postgres 컨테이너 `healthy` 대기
2. `alembic upgrade head` 1회 실행
3. 종료 (restart 안 함)
4. web/consumer/worker/scheduler가 `migrate` 종료 후에만 기동 (`depends_on: service_completed_successfully`)

즉 `docker compose up` 한 번이면 schema가 항상 최신. 환경(dev/staging/prod) 무관 — 같은 컨테이너·같은 절차.

`docker compose run --rm migrate <alembic 명령>` 형태로 일회성 작업도 가능 (current·history·downgrade 등).

## DB URL 주입

`alembic.ini`의 `sqlalchemy.url`은 비어있음. `migrations/env.py`가 `web_settings.database_url`로 런타임 주입 — `.env` / Docker secrets 등 동일 환경변수 정책 활용. migrate 컨테이너는 `POSTGRES_HOST=postgres` 환경변수 자동 주입.

호스트에서 직접 alembic 실행 시 `POSTGRES_HOST` env 명시 필요 (default `postgres`는 docker-compose 서비스명):

```bash
POSTGRES_HOST=localhost .venv/bin/alembic upgrade head
```

## 명령

| 작업 | 명령 |
|------|------|
| 현재 적용 revision 확인 | `docker compose run --rm migrate alembic current` |
| 모든 revision 이력 | `docker compose run --rm migrate alembic history --verbose` |
| 미적용 revision 미리보기 (offline SQL) | `docker compose run --rm migrate alembic upgrade head --sql` |
| head로 업그레이드 | `docker compose run --rm migrate alembic upgrade head` |
| 한 단계 다운그레이드 | `docker compose run --rm migrate alembic downgrade -1` |
| 모델 diff로 신규 revision 생성 | `docker compose run --rm migrate alembic revision --autogenerate -m "<설명>"` |
| 빈 revision 생성 (수동 작성) | `docker compose run --rm migrate alembic revision -m "<설명>"` |
| 모델 vs schema drift 검출 | `docker compose run --rm migrate alembic check` |

## 신규 마이그레이션 작성 워크플로우

전체 흐름: ORM 변경 → autogenerate revision → 수동 보강 → 라운드트립 검증 → commit.

### 1. ORM 모델 변경

`src/assessment_engine/db/models/*.py`에 컬럼 추가/타입 변경 등. ORM이 단일 진실 — 마이그레이션은 ORM을 따라간다.

### 2. autogenerate로 revision 생성

DB가 이미 띄워진 상태에서:

```bash
docker compose run --rm migrate alembic revision --autogenerate -m "add boot_time to server_metrics"
```

`migrations/versions/<id>_add_boot_time_*.py` 파일 자동 생성. ORM과 DB schema 차이만큼 `upgrade()`·`downgrade()` 본문이 채워진다.

### 3. 수동 보강 (autogenerate 미지원 영역)

생성된 파일을 반드시 검토하고 다음 항목은 직접 추가:

- `create_hypertable` / TimescaleDB retention·continuous aggregate
- `CREATE EXTENSION`
- partial index의 `postgresql_where` 일부
- CHECK 제약 / JSONB GIN 옵션 일부

시계열 새 테이블 추가 시 의무:

```python
op.execute("SELECT create_hypertable('table_name', 'collected_at', if_not_exists => true)")
```

`downgrade()`도 검토 — autogenerate가 만든 DROP COLUMN이 데이터 손실을 무릅쓰는 경우라면 운영자가 인지하도록 주석을 더해 둔다.

### 4. 라운드트립 검증

```bash
docker compose run --rm migrate alembic upgrade head        # 정상 적용
docker compose run --rm migrate alembic downgrade -1        # 롤백 가능
docker compose run --rm migrate alembic upgrade head        # 재적용
```

upgrade → downgrade → upgrade가 깔끔하게 통과해야 PR-ready. downgrade에서 에러 나면 `downgrade()` 본문이 잘못된 것 — 보강.

### 5. drift 자동 검출

```bash
docker compose run --rm migrate alembic check
```

ORM 모델과 현재 DB schema에 차이가 있으면 exit 1. 차이가 있다는 건 "마이그레이션을 아직 안 만들었다"는 신호. CI(`.github/workflows/alembic-check.yml`)에서도 같은 명령 자동 실행 — 모델만 바꾸고 마이그레이션 누락하면 PR이 막힌다.

### 6. commit

마이그레이션 파일 + 모델 변경을 한 커밋으로 (CLAUDE.md #C4 의무).

```bash
git add src/assessment_engine/db/models/ migrations/versions/
git commit -m "..."
```

한쪽만 올리면 다른 개발자 환경이 깨진다.

## 환경별 적용 시점

| 환경 | 적용 시점 | 자동 여부 |
|------|----------|----------|
| dev (`docker compose up`) | up 시점에 migrate 컨테이너 자동 실행 | 자동 |
| staging (`docker compose -f ... -f staging.yml up`) | 동일 — migrate 자동 | 자동 |
| prod (`docker compose -f ... -f docker-compose.prod.yml up -d`) | 동일 — migrate 자동. 큰 변경 시 사전 검토 권장 | 자동 |
| 테스트 (`pytest`) | testcontainers fixture가 alembic upgrade subprocess 실행 (`tests/conftest.py`) | 자동 |

prod에 큰 변경(데이터 손실 가능 DROP·대량 행 ALTER) 적용 전:

```bash
# history 확인 — 어디까지 가는지
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm migrate alembic history

# offline SQL 추출 — 실행될 DDL 미리 검토
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm migrate alembic upgrade head --sql > /tmp/migration.sql
cat /tmp/migration.sql

# 검토 OK면 정식 기동 (자동 적용됨)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

## DEV·PROD 동일 책임 매트릭스

| 작업 | 명령 |
|------|------|
| Schema 생성 | migrate 컨테이너 `alembic upgrade head` (자동) |
| 컬럼 추가 | `alembic revision --autogenerate` + migrate가 자동 적용 |
| Hypertable 변환 | 마이그레이션 파일에 `op.execute("SELECT create_hypertable(...)")` 수동 |
| 검증 | pytest (testcontainers + alembic) + staging smoke test |

모든 환경이 같은 마이그레이션 파일을 적용하므로 drift 가능성이 코드 단에서 차단된다. `alembic check`가 PR 단계에서 한 번 더 검증.

## 트러블슈팅

| 증상 | 원인 | 대처 |
|------|------|------|
| `alembic upgrade head` 후 web이 schema 부재 5xx | migrate 컨테이너가 실패하고 종료 코드 0이 아닐 가능성 | `docker compose logs migrate` 확인 후 원인 fix |
| `Target database is not up to date` | migrate가 적용 안 된 상태에서 alembic 명령 호출 | `alembic upgrade head` 먼저 |
| `Can't locate revision identified by '<id>'` | git pull 후 마이그레이션 파일 누락 | `git pull` 다시, 마이그레이션 디렉토리 확인 |
| `alembic check` 실패 | 모델 변경 후 마이그레이션 안 만듦 | autogenerate로 revision 생성 후 commit |
| autogenerate가 hypertable index를 매번 drop 처리 | `{table}_collected_at_idx`는 TimescaleDB 자동 생성 객체 | `migrations/env.py:_include_object`가 이미 필터링 — 본 패턴 외 새로운 자동 생성 객체는 본 함수에 추가 |
