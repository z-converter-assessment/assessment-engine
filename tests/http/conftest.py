import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from assessment_engine.web.deps import get_diagnostic_service, get_service
from assessment_engine.web.main import app
from assessment_engine.web.services.query import QueryService
from tests.fakes import FakeRedis, InMemoryQueryRepository
from tests.http.seed import QUERY_SEED, normalize

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
UPDATE = os.environ.get("SNAPSHOT_UPDATE") == "1"


@pytest.fixture
def fake_repo() -> InMemoryQueryRepository:
    return InMemoryQueryRepository(QUERY_SEED())


@pytest_asyncio.fixture
async def client(fake_repo: InMemoryQueryRepository) -> AsyncIterator[AsyncClient]:
    app.state.dev_assets = False
    service = QueryService(cast("Any", fake_repo), cast("Any", FakeRedis()))
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[get_diagnostic_service] = _fake_diagnostic_service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


def _fake_diagnostic_service() -> Any:
    from tests.fakes import InMemoryDiagnosticService

    return InMemoryDiagnosticService()


@pytest.fixture
def snapshot() -> Any:

    def _compare(name: str, value: object) -> None:
        path = SNAPSHOT_DIR / f"{name}.json"
        payload = json.dumps(normalize(value), ensure_ascii=False, indent=2, sort_keys=False)
        if UPDATE:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload + "\n", encoding="utf-8")
            return
        if not path.exists():
            pytest.fail(f"스냅샷이 없다: {path} — SNAPSHOT_UPDATE=1 로 먼저 캡처한다")
        assert payload + "\n" == path.read_text(encoding="utf-8"), f"{name} 응답이 스냅샷과 다르다"

    return _compare
