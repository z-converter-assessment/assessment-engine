# ADR 0005 — DB Schema 관리 표준화 (Alembic 단일 진실)

상태: 채택 (2026-05-12)

## Context

기존 패턴 — 환경마다 schema를 만드는 경로가 둘:

- DEV: `web` lifespan의 `Base.metadata.create_all` + `create_hypertable` 자동
- PROD: Alembic (`alembic upgrade head`) 운영자 수동

문제:
- 개발자가 ORM 모델 변경 후 `alembic revision` 잊어도 dev는 동작 — `create_all`이 새 모델 그대로 생성
- PR 검토자도 마이그레이션 없는 모델 변경 통과 가능
- prod 배포 시 schema drift 발견 → 운영 사고 위험
- 테스트 환경(`tests/conftest.py`)도 `create_all` 사용 — 운영 경로와 다름, 마이그레이션 자체의 정확성 검증 불가

요구사항:
1. dev·staging·prod·테스트 모두 같은 schema 생성 경로 — drift 위험 0
2. 개발자가 마이그레이션 누락하면 자동 검출
3. dev iteration 빠름 — `docker compose up` 한 번이면 모든 게 됨

## Options

### A. 현상 유지 (dev=create_all / prod=Alembic)
- 장점: dev iteration 빠름 (마이그레이션 작성 부담 없음)
- 단점: drift 위험. 모델 변경 + 마이그레이션 누락이 prod 배포 직전까지 안 발견

### B. 모든 환경 Alembic 단일 진실 (init-container 패턴)
- `migrate` docker-compose 서비스 추가 — `alembic upgrade head` 1회 실행 후 종료
- 모든 앱 서비스가 `depends_on: migrate (service_completed_successfully)` 후 기동
- lifespan의 `create_all` 자동 분기 제거
- `tests/conftest.py`도 alembic 적용 (subprocess)
- CI에서 `alembic check` 자동 실행 → drift PR 차단
- 장점: 모든 환경 동일 schema 생성 경로. drift 자체가 코드 단에서 차단
- 단점: dev 모델 변경 시 마이그레이션 작성 의무 (자동화 없음). docker compose run --rm migrate alembic 명령 학습

### C. web 컨테이너 entrypoint에서 alembic upgrade head 자동
- 별도 init-container 없이 web 시작 전 alembic 실행
- 장점: 컨테이너 1개 줄임
- 단점: 동시 N web 인스턴스 race condition. schema 책임이 web에 섞임 — 다른 서비스(consumer/worker)는 web 의존이 어색

## Decision

옵션 B 채택.

근거:
1. drift 위험을 코드 단에서 차단 — 운영 사고 회피 가치가 dev iteration 비용보다 큼
2. k8s init-container와 동일 패턴 — 미래 k8s 배포 시 자연스럽게 전환
3. CI `alembic check`로 마이그레이션 누락 PR 차단 — 개발자 인지 부담 줄임
4. 옵션 C(entrypoint 자동)는 race·책임 혼합 문제 — 별도 컨테이너가 깨끗

## Architecture

### docker-compose 토폴로지

```
postgres (healthy)
    ↓
migrate (alembic upgrade head → exit 0)
    ↓
web + consumer + diagnostic-worker + diagnostic-scheduler
```

`migrate` 서비스 정의:

```yaml
migrate:
  build: .
  command: alembic upgrade head
  environment:
    APP_ENV: ${APP_ENV:-prod}
    POSTGRES_HOST: postgres
  depends_on:
    postgres:
      condition: service_healthy
  restart: "no"
```

앱 서비스 의존성:

```yaml
web:
  depends_on:
    postgres: { condition: service_healthy }
    redis: { condition: service_healthy }
    rabbitmq: { condition: service_healthy }
    migrate: { condition: service_completed_successfully }
```

### 테스트 환경 (testcontainers + alembic)

`tests/conftest.py`의 `engine` fixture가 alembic upgrade를 subprocess로 호출:

```python
subprocess.run(
    ["alembic", "-c", str(_REPO_ROOT / "alembic.ini"), "upgrade", "head"],
    env=env, check=True, capture_output=True,
)
```

async fixture 내 nested asyncio 회피용 subprocess. testcontainers PostgreSQL host·port를 env var로 주입.

### CI 강제 채널

`.github/workflows/alembic-check.yml` — PR/push 시 자동 실행:
1. TimescaleDB service container 띄움
2. `alembic upgrade head` 적용
3. `alembic check` 실행 — ORM 모델 vs 마이그레이션 drift 검출

모델만 변경하고 마이그레이션 누락하면 exit 1 → PR 차단.

### 개발자 워크플로

ORM 모델 변경 후:
```bash
docker compose run --rm migrate alembic revision --autogenerate -m "..."
# 생성된 파일 review (autogenerate 한계 — hypertable·partial index 등 수동 보강)
docker compose run --rm migrate alembic upgrade head           # 적용
docker compose run --rm migrate alembic downgrade -1           # 라운드트립 검증
docker compose run --rm migrate alembic upgrade head
git add src/assessment_engine/db/models/ migrations/versions/
git commit -m "..."
```

다른 개발자 변경 pull 받으면:
```bash
git pull
docker compose up -d   # migrate 자동 적용
```

## Consequences

### 긍정
- dev·staging·prod·테스트 모두 동일 schema 생성 경로 — drift 0
- CI에서 자동 검출 → 운영 사고 회피
- k8s init-container 패턴과 일관 → 미래 배포 전환 자연
- 마이그레이션 파일이 운영 schema의 진실 → 운영자가 마이그레이션 이력으로 schema 변경 추적

### 부정·한계
- dev 모델 변경마다 `alembic revision` 명령 필요 (학습 비용)
- autogenerate 한계로 hypertable·partial index 등 수동 보강 의무 — 누락 시 마이그레이션 깨짐
- 마이그레이션 누적 시 `up` 시간 증가 (현재 2개라 무관, 100+ 시 squash 검토)
- migrate 컨테이너 실패 시 모든 앱 서비스 미기동 — 운영자가 `docker compose logs migrate` 확인 의무

### 미해결 (다음 단계)
- 마이그레이션 squash 정책 (100개 누적 시점)
- dev seed data 자동 주입 (현재 Lima 에이전트가 자연 메트릭 발행이라 무관)
- 큰 데이터 마이그레이션(수백만 행 ALTER) 운영 정책 — 향후 도입 시 별도 ADR

## 관련 문서
- CLAUDE.md #C4 (스키마 변경 의무·검증 절차)
- `docs/operations/alembic.md` (운영 절차·트러블슈팅)
- `README.md` "데이터베이스 스키마 관리" 섹션 (개발자 사용법)

## 정정 이력

- (없음)
