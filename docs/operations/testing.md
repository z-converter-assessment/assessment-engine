# 테스트

단위·통합 테스트 실행 방법.
운영 환경 풀 파이프라인 검증(에이전트 -> MQ -> Consumer -> DB -> Web)은 `docs/operations/pipeline.md`.

## 1. 계층

| 계층 | 위치 | 인프라 | 격리 단위 |
|------|------|--------|---------|
| Unit | `tests/unit/` | `AsyncMock` (Redis 등) | 외부 의존 없음 |
| Integration | `tests/integration/` | `testcontainers[postgres]`로 TimescaleDB 컨테이너 | function-scope `db_session` transaction rollback |

## 2. 실행

```bash
# 1. dev 의존성 설치 (최초 1회)
pip install -e ".[dev]"

# 2. 전체 (unit + integration; ~3초)
python -m pytest

# 3. 단위만 (DB 무관, 빠름; ~1초)
python -m pytest tests/unit/

# 4. 통합만 (Docker 필요 — testcontainers가 TimescaleDB 자동 spawn; ~2초)
python -m pytest tests/integration/

# 5. 단일 파일 / 단일 함수
python -m pytest tests/integration/test_query_repository.py
python -m pytest tests/integration/test_query_repository.py::test_metric_chart_dispatcher_all_types

# 6. 자세한 출력
python -m pytest -v
```

## 3. 설정·Fixture

`pyproject.toml`:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "session"
asyncio_default_test_loop_scope = "session"
```

Fixture 계층:
```
tests/conftest.py             — session: _postgres_container, engine, db_session
tests/integration/conftest.py — function: collect_repo, query_repo
tests/factories.py            — make_inventory(), make_metrics()
```

데이터 빌더 사용:
```python
inv = make_inventory(machine_id="m1", hostname="h1", cpu_cores=4)
m = make_metrics(collected_at=ts, cpu_user=1000)
```
미지정 필드는 안전한 default. 필요한 필드만 명시.

## 4. 테스트 작성

### Unit (DB 무관)

```python
# tests/unit/test_my_module.py
import pytest
from unittest.mock import AsyncMock

pytestmark = pytest.mark.asyncio

async def test_something():
    ...
```

`pytestmark` 한 줄로 모듈 전체에 asyncio 적용. 외부 의존은 `AsyncMock` / `MagicMock`으로.

### Integration (real DB)

```python
# tests/integration/test_my_repo.py
import pytest
from tests.factories import make_inventory

pytestmark = pytest.mark.asyncio

async def test_something(collect_repo):
    inv = make_inventory(machine_id="t-001")
    sid = await collect_repo.upsert_server(inv)
    assert sid > 0
```

`collect_repo` / `query_repo` / `db_session` fixture 자동 주입. 각 테스트 끝에 transaction rollback으로 격리.

### Parametrize 활용 (CollectionRepository / dispatcher 검증)

```python
@pytest.mark.parametrize("metric_type", _ALL_METRIC_TYPES)
async def test_dispatcher(metric_type, query_repo):
    ...
```

17개 metric_type 일괄 검증 — 누락 metric_type 즉시 발견.

## 5. 원칙

- 새 코드 추가 시 테스트도 함께 작성 — 코드 리뷰 시 누락 지적.
- 리팩토링은 테스트 통과 baseline 위에서만 진행 — 회귀 즉시 식별.
- E2E (Vagrant) 검증은 pytest 범위 외 — `docs/operations/pipeline.md` 참조.
- pytest 자동 실행 금지 — 사용자 명시 요청 시에만 (개발 중 회귀 상태일 수 있음).