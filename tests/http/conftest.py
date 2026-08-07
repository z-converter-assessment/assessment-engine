"""HTTP 경계 characterization 픽스처.

이 계층의 목적은 하나다 — 리팩토링이 화면과 API 응답을 바꾸지 않았다는 것을 저장소 안에서 재현 가능하게
보이는 것. 그래서 단언을 손으로 쓰지 않고 응답을 통째로 스냅샷과 대조한다.

최초 캡처는 `SNAPSHOT_UPDATE=1 uv run pytest tests/http/` 로 만들고, 만든 뒤 사람이 전 파일을 눈으로 읽고
"이게 지금 화면이 맞다" 를 확인한 다음 커밋한다. 이 검수를 건너뛰면 안전망이 아니라 버그 고정 장치가 된다.

`starlette.testclient` 는 쓰지 않는다 — httpx 를 쓰면 `StarletteDeprecationWarning` 을 내는데 이 저장소는
`filterwarnings = ["error"]` 라 그대로 죽는다. `httpx.ASGITransport` 를 직접 쓰면 신규 의존이 0 이다.

lifespan 은 돌리지 않는다. 돌리면 broker 와 httpx 클라이언트에 실제로 접속하려 든다. 그래서 lifespan 이
`app.state` 에 넣는 것에 의존하는 endpoint(`/api/tasks/install` 과 발행 2건)는 캡처 대상에서 뺀다.

지금 시드가 닿지 않아 404 로 고정된 경로가 넷 있다 — task 상세 2건, 보고서 job 상태, 최신 메트릭 스냅샷.
404 도 계약이라 그 상태로 캡처하지만, 그 템플릿·직렬화는 이 안전망이 검증하지 않는다. 해당 화면을 건드리는
단계는 시드를 먼저 채워 200 으로 만든 뒤 진행한다.
"""

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
    """DI 를 대역으로 갈아끼운 ASGI 클라이언트.

    `app.state.dev_assets` 를 명시로 끈다 — 켜지면 미들웨어가 매 요청 `asset_v` 를 갈아치우고
    그 값이 모듈 전역이라 프로세스 전체로 새서 뒤 테스트 스냅샷을 오염시킨다.
    """
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
    """스냅샷 비교기. `SNAPSHOT_UPDATE=1` 이면 기록하고, 아니면 대조한다."""

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
