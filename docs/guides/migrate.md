# Alembic 마이그레이션

정책: CLAUDE.md #C4. 본 문서는 Alembic 사용법·절차·트러블슈팅 단일 진실.

## 본 프로젝트의 Alembic

ORM(`Base.metadata`)과 DB schema의 diff를 `src/assessment_engine/migrations/versions/*.py` revision 파일로 기록하고 `upgrade head` 한 줄로 누적 적용한다. DB의 `alembic_version` 테이블이 적용 revision id를 보관 — 환경마다 자기 상태를 알아 누락 revision만 자동 실행. revision·head·autogenerate 같은 도구 일반론은 Alembic 공식 문서.

`_alembic.ini` 와 `migrations/` 는 패키지 안에 있어 별도 포장 설정 없이 이미지에 동봉된다. 설정 경로는 진입점 `assessment_engine.migrate` 가 자기 패키지에서 찾으므로 호출 측이 경로를 주지 않는다 — 호스트든 컨테이너든 같은 명령이고, compose 없이 `docker run` 해도 성립한다. 맨 `alembic` 명령은 cwd 에서 설정을 찾지 못해 실패하니 쓰지 않는다.

```bash
python -m assessment_engine.migrate <명령>
```

## 자동 적용 — migrate 컨테이너

`migrate` 서비스가 postgres 컨테이너 `healthy` 를 기다려 `upgrade head` 를 1회 실행하고 종료한다 (restart 안 함). web·consumer·worker 는 `depends_on: service_completed_successfully` 라 그 종료 뒤에만 기동한다.

즉 `docker compose up` 한 번이면 schema가 항상 최신. 환경 무관 — 같은 컨테이너·같은 절차. 같은 컨테이너로 일회성 작업도 돈다 ("명령" 절).

## DB URL 주입

`_alembic.ini`의 `sqlalchemy.url`은 비어있음. `src/assessment_engine/migrations/env.py`가 `WebSettings().database_url`로 런타임 주입 — `.env` / Docker secrets 등 동일 환경변수 정책 활용. migrate 컨테이너는 `POSTGRES_HOST=postgres` 환경변수 자동 주입.

호스트에서 직접 실행할 때는 `POSTGRES_HOST` 를 명시한다 (default `postgres`는 docker-compose 서비스명):

```bash
POSTGRES_HOST=localhost .venv/bin/python -m assessment_engine.migrate upgrade head
```

## 명령

head 업그레이드는 `make migrate`, 모델 diff 초안 생성은 `make migration M="<설명>"`. 나머지는 `docker compose run --rm migrate python -m assessment_engine.migrate` 뒤에 아래 인자를 붙인다.

| 작업 | 인자 |
|------|------|
| 현재 적용 revision 확인 | `current` |
| 모든 revision 이력 | `history --verbose` |
| 미적용 revision 미리보기 (offline SQL) | `upgrade head --sql` |
| 한 단계 다운그레이드 | `downgrade -1` |
| 빈 revision 생성 (수동 작성) | `revision -m "<설명>"` |
| 모델 vs schema drift 검출 | `check` |

## 신규 마이그레이션 작성 워크플로우

전체 흐름: ORM 변경 → autogenerate revision → 수동 보강 → 라운드트립 검증 → commit.

### 1. ORM 모델 변경

`src/assessment_engine/db/models/*.py`에 컬럼 추가/타입 변경 등. ORM이 단일 진실 — 마이그레이션은 ORM을 따라간다.

### 2. autogenerate로 revision 생성

DB가 이미 띄워진 상태에서:

```bash
make migration M="add boot_time to server_metrics"
```

`src/assessment_engine/migrations/versions/<id>_add_boot_time_*.py` 파일 자동 생성. ORM과 DB schema 차이만큼 `upgrade()`·`downgrade()` 본문이 채워진다.

### 3. 수동 보강 (autogenerate 미지원 영역)

생성된 파일을 반드시 검토하고 다음 항목은 직접 추가:

- `create_hypertable` / TimescaleDB retention·continuous aggregate
- `CREATE EXTENSION` (예: TimescaleDB)
- partial index의 `postgresql_where` 일부
- CHECK 제약 / JSONB GIN 옵션 일부

시계열 새 테이블 추가 시 의무:

```python
op.execute("SELECT create_hypertable('table_name', 'collected_at', if_not_exists => true)")
```

`downgrade()`도 검토 — autogenerate가 만든 DROP COLUMN이 데이터 손실을 무릅쓰는 경우라면 운영자가 인지하도록 주석을 더해 둔다.

### 4. 라운드트립 검증

```bash
docker compose run --rm migrate python -m assessment_engine.migrate upgrade head        # 정상 적용
docker compose run --rm migrate python -m assessment_engine.migrate downgrade -1        # 롤백 가능
docker compose run --rm migrate python -m assessment_engine.migrate upgrade head        # 재적용
```

upgrade → downgrade → upgrade가 깔끔하게 통과해야 PR-ready. downgrade에서 에러 나면 `downgrade()` 본문이 잘못된 것 — 보강.

### 5. drift 자동 검출

```bash
docker compose run --rm migrate python -m assessment_engine.migrate check
```

ORM 모델과 현재 DB schema에 차이가 있으면 exit 1. 차이가 있다는 건 "마이그레이션을 아직 안 만들었다"는 신호. CI(`.github/workflows/alembic-check.yml`)에서도 같은 명령 자동 실행 — 모델만 바꾸고 마이그레이션 누락하면 PR이 막힌다.

### 6. commit

마이그레이션 파일 + 모델 변경을 한 커밋으로 (CLAUDE.md #C4 의무).

```bash
git add src/assessment_engine/db/models/ src/assessment_engine/migrations/versions/
git commit -m "..."
```

한쪽만 올리면 다른 개발자 환경이 깨진다.

## 환경별 적용 시점

셋 다 사람 손을 타지 않는다.

| 환경 | 적용 시점 |
|------|----------|
| dev (`docker compose up`) | up 시점에 `migrate` 컨테이너 실행 |
| prod (compose) | 같은 `migrate` init-container 를 `deploy.sh` rollout 이 그대로 탄다 (이미지 안 `_alembic.ini`+`migrations/`) |
| 테스트 (`pytest`) | testcontainers fixture 가 upgrade subprocess 실행 (`tests/conftest.py`) |

prod에 큰 변경(데이터 손실 가능 DROP·대량 행 ALTER) 적용 전 — 배포 환경 이미지 컨테이너에서:

```bash
# history 확인 — 어디까지 가는지
docker compose run --rm migrate python -m assessment_engine.migrate history

# offline SQL 추출 — 실행될 DDL 미리 검토
docker compose run --rm migrate python -m assessment_engine.migrate upgrade head --sql > /tmp/migration.sql
cat /tmp/migration.sql

# 검토 OK면 적용
make migrate
```

## Backward compatibility — 무중단 deploy 시 schema 변경 단계 (#C4)

prod rollout 은 init container 패턴이라 스키마가 한 번에 적용되지만, backward compatibility 는 지금도 의무다 — `deploy.sh` 의 rollback 이 이미 마이그레이션된 스키마 위에 직전 이미지를 올리므로 구버전 코드가 새 스키마에서 동작해야 복구가 성립한다. rolling restart·blue-green 을 도입하면 그 위에 동시 실행 구간이 더해진다.

### Column 추가

NULL 허용 또는 `server_default` 의무. 한 release에서 끝남.

```python
op.add_column("server_metrics", sa.Column("new_field", sa.Integer(), nullable=True))
# 또는
op.add_column("server_metrics", sa.Column("new_field", sa.Integer(), nullable=False, server_default="0"))
```

옛 컨테이너의 INSERT는 새 컬럼을 모르지만 NULL/default로 채워져 통과.

### Column 제거 — 2 release 분리 의무

| Release | 마이그레이션 | 코드 변경 |
|---------|--------------|-----------|
| R1 | (마이그레이션 없음) | 새 코드가 해당 컬럼 read·write 안 함을 확인. ORM 모델에선 column 유지 |
| R2 | `op.drop_column("table", "old_col")` | ORM 모델에서 column 정의 제거 |

단일 release에서 ORM column 제거 + `op.drop_column`은 금지 — R1 deploy 중 옛 컨테이너가 잠시 column 가정으로 INSERT 시도하면 실패.

### Column 이름 변경 (rename) — 3 release 분리 의무

| Release | 마이그레이션 | 코드 변경 |
|---------|--------------|-----------|
| R1 | `op.add_column("new_col", nullable=True)` | 새 코드가 옛 + 새 둘 다 write (dual-write). read는 옛 column |
| R1.5 | `op.execute("UPDATE table SET new_col = old_col WHERE new_col IS NULL")` (배치 또는 별도 스크립트) | (없음 — 데이터 동기) |
| R2 | (마이그레이션 없음) | read를 새 column으로 전환. write는 dual 유지 |
| R3 | `op.drop_column("old_col")` | 옛 column 코드 제거 |

단일 release rename(`op.alter_column(..., new_name="...")` 또는 `op.rename_column(...)`)은 금지.

### 비-trivial 데이터 변형 — 마이그레이션 분리

`op.execute("UPDATE big_table SET ...")` 큰 트랜잭션은 테이블 lock으로 prod traffic block 가능. 다음 패턴 의무:
- 마이그레이션 안에는 schema 변경(컬럼 추가·인덱스)만 + NULL 허용
- 별도 backfill 스크립트(`scripts/backfill_*.py`) — 운영자가 배치(LIMIT + OFFSET 또는 keyset pagination) 처리
- backfill 완료 후 다음 release에서 NOT NULL 강제 또는 옛 컬럼 drop

## 트러블슈팅

| 증상 | 원인 | 대처 |
|------|------|------|
| `docker compose up` 이 web·consumer·worker 를 띄우지 않고 dependency failed | migrate 컨테이너가 0 이 아닌 코드로 종료 | `docker compose logs migrate` 확인 후 원인 fix |
| `Target database is not up to date` | migrate가 적용 안 된 상태에서 alembic 명령 호출 | `make migrate` 먼저 |
| `Can't locate revision identified by '<id>'` | git pull 후 마이그레이션 파일 누락 | `git pull` 다시, 마이그레이션 디렉토리 확인 |
| `alembic check` 실패 | 모델 변경 후 마이그레이션 안 만듦 | autogenerate로 revision 생성 후 commit |
| autogenerate가 hypertable index를 매번 drop 처리 | `{table}_collected_at_idx`는 TimescaleDB 자동 생성 객체 | `src/assessment_engine/migrations/env.py:_include_object`가 이미 필터링 — 본 패턴 외 새로운 자동 생성 객체는 본 함수에 추가 |
