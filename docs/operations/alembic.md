# Alembic 마이그레이션

PROD schema 관리 단일 진실. DEV는 `web` lifespan의 `create_all` + `create_hypertable` 사용 (빠른 반복) — Alembic 미사용. 단 모델 변경 시 Alembic 마이그레이션도 동시 갱신 의무 (PROD 동기화).

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

1. ORM 모델 변경 (`src/assessment_engine/db/models/*.py`)
2. DEV에서 변경 검증 (`docker compose down -v` 후 재기동 → `create_all`이 자동)
3. **빈 DB + TimescaleDB extension** 띄우기 (autogenerate 비교 대상):
   ```bash
   docker compose down -v
   docker compose up -d postgres
   docker compose exec postgres psql -U assessment -d assessment -c \
     "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"
   POSTGRES_HOST=localhost .venv/bin/alembic upgrade head  # 기존 revision 적용
   ```
4. autogenerate:
   ```bash
   POSTGRES_HOST=localhost .venv/bin/alembic revision --autogenerate -m "add boot_time to ..."
   ```
5. 생성된 마이그레이션 파일 검토 + 수동 보강:
   - **autogenerate 미지원**: `create_hypertable` / TimescaleDB 정책 / continuous aggregate / partial index의 `postgresql_where` 일부 / `CREATE EXTENSION`
   - 시계열 새 테이블 추가 시 `op.execute("SELECT create_hypertable('table', 'collected_at', if_not_exists => true)")` 추가 필수
6. 검증:
   ```bash
   POSTGRES_HOST=localhost .venv/bin/alembic upgrade head        # 정상 적용
   POSTGRES_HOST=localhost .venv/bin/alembic downgrade -1        # 롤백 가능
   POSTGRES_HOST=localhost .venv/bin/alembic upgrade head        # 재적용
   ```
7. commit (마이그레이션 파일 + 모델)

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
| 별도 `migrate` service (compose에 의존) | **미도입** — over-engineering. 운영자 인지 가시성·롤백 통제력 약화 |
| web 컨테이너 entrypoint에 `alembic upgrade head` 자동 | **미도입** — 동시 N web 인스턴스 race + 수동 검토 단계 사라짐 |
| **운영자 수동 실행** | **채택** — 한 줄 명령, 결과 즉시 가시, 롤백 의사결정 명확 |

## DEV vs PROD 책임 매트릭스

| 작업 | DEV | PROD |
|------|-----|------|
| Schema 생성 | web lifespan `create_all` + `create_hypertable` 자동 | Alembic `upgrade head` 사전 수동 |
| 컬럼 추가 | `docker compose down -v` 후 재기동 | `alembic revision --autogenerate` + `upgrade head` |
| Hypertable 변환 | lifespan 자동 | 마이그레이션 파일에 `op.execute("SELECT create_hypertable(...)")` 수동 |
| 검증 | pytest (testcontainers) | staging Alembic 적용 → smoke test |

DEV의 `create_all`과 Alembic 마이그레이션은 **같은 schema 보장 의무** — 모델 변경 시 둘 다 검증.
