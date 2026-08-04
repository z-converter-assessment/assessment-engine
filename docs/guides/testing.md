# 테스트

본 문서는 pytest 단위·통합 테스트 실행·작성 단일 진실. agent 가 붙는 실 VM 을 동반한 E2E 는 본 repo 범위 밖(OpenStack 공급 환경에서 수행).

## 1. 계층

| 계층 | 위치 | 인프라 | 격리 단위 |
|------|------|--------|---------|
| Unit | `tests/unit/` | `AsyncMock` (Redis 등) | 외부 의존 없음 |
| Integration | `tests/integration/` | `testcontainers[postgres]`로 TimescaleDB 컨테이너 | function-scope `db_session` transaction rollback |

hypothesis property 기반 테스트는 unit 안에 있다 — 생성 횟수 배율은 2절.

## 2. 실행

일상 실행은 `make test`·`make test-unit`·`make test-integration` 이다 (`make help`). 통합은 Docker 가 필요하다 — testcontainers 가 TimescaleDB 를 자동으로 띄운다.

아래는 make 타깃이 없는 변형이다.

```bash
# property 생성 횟수 1/10 — develop 워크플로와 같은 배율
HYPOTHESIS_SCALE=0.1 uv run pytest tests/unit/

# 단일 파일 / 단일 함수
uv run pytest tests/integration/test_query_repository.py
uv run pytest tests/integration/test_query_repository.py::test_metric_chart_dispatcher_all_types

# 자세한 출력
uv run pytest -v
```

unit 소요는 property 테스트가 지배한다. `HYPOTHESIS_SCALE` 은 각 테스트가 선언한 `max_examples` 에 곱하는 배율이고(기본 1, 하한 50 예제 — `tests/hypothesis_scale.py`), 검증 워크플로는 이 값을 base 브랜치로 가른다 — main 승격은 선언값 전량, develop 통합은 1/10.

CI 자동 실행: PR 을 올리면 `.github/workflows/ci.yml` 이 테스트를 돌린다. base 별 발화 범위는 `docs/guides/ci-setup.md` 3.4.

## 3. 설정·Fixture

pytest 설정은 `pyproject.toml` `[tool.pytest.ini_options]`.

Fixture 계층:
```
tests/conftest.py             — session: _postgres_container, engine / function: db_session (테스트마다 rollback)
tests/unit/conftest.py        — autouse: 필수 비밀번호 env 주입, Composition Root lru_cache 초기화
tests/integration/conftest.py — function: collect_repo, query_repo, diagnostic_repo (TRUNCATE 격리)
tests/factories.py            — wire 계약 형태 데이터 빌더 (함수 목록은 해당 파일)
```

데이터 빌더 사용:
```python
inv = make_inventory(composite_id="m1", hostname="h1", cpu_cores=4)
m = make_metrics(collected_at=ts, cpu_user_s=1000.0)
```
전 인자 keyword-only. 미지정 필드는 안전한 default 라 필요한 필드만 명시한다. 단위는 wire 규약(시간 s, 크기 By).

## 4. 테스트 작성

### Unit (DB 무관)

```python
# tests/unit/test_my_module.py
from unittest.mock import AsyncMock

async def test_something():
    ...
```

asyncio 마커는 붙이지 않는다 — `asyncio_mode=auto` 라 async 테스트가 자동 수집된다. 외부 의존은 `AsyncMock` / `MagicMock`으로.

### Integration (real DB)

```python
# tests/integration/test_my_repo.py
from tests.factories import make_inventory

async def test_something(collect_repo):
    inv = make_inventory(composite_id="t-001")
    sid = await collect_repo.upsert_server(inv)
    assert sid > 0
```

`collect_repo` / `query_repo` / `diagnostic_repo` / `db_session` fixture 자동 주입. 각 테스트 끝에 transaction rollback으로 격리 — 명시 commit 을 쓰는 `diagnostic_repo` 만 TRUNCATE 로 격리한다.

### Parametrize 활용 (CollectionRepository / dispatcher 검증)

```python
@pytest.mark.parametrize("metric_type", _ALL_METRIC_TYPES)
async def test_dispatcher(metric_type, query_repo):
    ...
```

전 metric_type(카탈로그는 `types.py` `MetricType`) 일괄 검증 — 누락 metric_type 즉시 발견.

## 5. 원칙

- 새 코드 추가 시 테스트도 함께 작성 — 코드 리뷰 시 누락 지적.
- 리팩토링은 테스트 통과 baseline 위에서만 진행 — 회귀 즉시 식별.
- 실 VM 동반 E2E 는 pytest 범위 외 — 본 repo 범위 밖(OpenStack 공급 환경).
- 에이전트의 pytest 실행 정책은 CLAUDE.md #F5.