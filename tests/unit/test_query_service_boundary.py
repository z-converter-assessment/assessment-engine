"""QueryService 공개 표면 characterization — 6 도메인이 무엇을 돌려주는지 값으로 고정한다.

HTTP 경계 스냅샷(`tests/http/`)이 화면을 고정한다면 여기는 그 아래 계층을 고정한다. 화면을 거치지
않는 경로(JSON 전용 메서드, 캐시 왕복)와 화면이 삼켜 버리는 값(빈 리스트 vs None)이 여기서 갈린다.

호출 순서·횟수는 단언하지 않는다. 그건 "지금 어느 메서드가 어느 메서드를 부르는가" 를 고정하는 것이라
동작을 보존하는 재배치까지 막는다. 이 계층이 받쳐야 하는 것이 바로 그 재배치다.
"""

from datetime import UTC, datetime
from typing import Any, cast

from assessment_engine.web.services.query_service import QueryService
from tests.builders import report_row_raw, server_detail
from tests.fakes import FakeRedis, InMemoryQueryRepository

ANCHOR = datetime(2026, 5, 12, tzinfo=UTC)


def _service(**seed: Any) -> QueryService:
    return QueryService(cast("Any", InMemoryQueryRepository(seed)), cast("Any", FakeRedis()))


def _three_hosts() -> dict[str, Any]:
    """분류가 갈리는 3대 — 자원 부족·유휴·미측정."""
    return {
        "list_server_ids": [1, 2, 3],
        "resolve_server_ids": {"p1": 1, "p2": 2, "p3": 3},
        "get_servers": [server_detail(n, f"host-{n}") for n in (1, 2, 3)],
        "report_aggregate": [
            report_row_raw(server_id=1, public_id="p1", hostname="host-1", cpu_p95_pct=88.0, mem_p95_pct=93.0),
            report_row_raw(server_id=2, public_id="p2", hostname="host-2", cpu_p95_pct=1.0, mem_p95_pct=4.0),
            report_row_raw(server_id=3, public_id="p3", hostname="host-3"),
        ],
    }


async def test_get_report_rows_preserve_requested_servers():
    """요청한 서버 수만큼 행이 나온다 — repo 가 순서를 보장하지 않으므로 개수와 집합으로 본다."""
    service = _service(**_three_hosts())

    result = await service.get_report([1, 2, 3], period_days=14, end=ANCHOR)

    assert {row.hostname for row in result.rows} == {"host-1", "host-2", "host-3"}
    assert result.total == 3


async def test_get_report_classifies_each_host():
    """분류는 행마다 붙는다 — 자원 부족·과다·표본 부족이 같은 표에서 갈린다.

    저사용 호스트가 `idle` 이 아니라 `over_provisioned` 인 것은 baseline 이 네트워크·디스크 활동을
    측정하지 않기 때문이다. 유휴는 활동 3축이 "측정됐고 조용할 때" 만 성립한다.
    """
    service = _service(**_three_hosts())

    rows = {r.hostname: r for r in (await service.get_report([1, 2, 3], period_days=14, end=ANCHOR)).rows}

    assert rows["host-1"].recommendation == "under_provisioned"
    assert rows["host-2"].recommendation == "over_provisioned"
    assert rows["host-3"].recommendation == "insufficient_data"


async def test_get_report_empty_when_no_servers():
    """서버가 없으면 빈 표 — 예외가 아니라 값으로 돌려준다(화면이 empty_state 를 그린다)."""
    service = _service(list_server_ids=[], get_servers=[], report_aggregate=[])

    result = await service.get_report([], period_days=14, end=ANCHOR)

    assert result.rows == []
    assert result.total == 0


async def test_resolve_server_id_returns_none_for_unknown():
    """미존재 식별자는 None — 라우터가 404 로 바꾸는 유일한 신호다."""
    service = _service(resolve_server_ids={"p1": 1})

    assert await service.resolve_server_id("p1") == 1
    assert await service.resolve_server_id("ghost") is None


async def test_get_server_returns_none_when_repo_empty():
    """상세 조회가 비면 None — 캐시에 빈 값을 심지 않는다."""
    service = _service(get_server=None)

    assert await service.get_server(1) is None


async def test_get_environment_report_carries_anchor_and_window():
    """환경 보고서는 앵커와 창을 그대로 싣는다 — 발행 스냅샷 재현의 근거."""
    service = _service(**_three_hosts())

    summary = await service.get_environment_report(time_range="14d", anchor_at=ANCHOR)

    assert summary.anchor_at == ANCHOR
    assert summary.time_range == "14d"
    assert summary.base.period_days == 14
