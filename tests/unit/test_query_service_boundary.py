from datetime import UTC, datetime
from typing import Any, cast

from assessment_engine.web.services.query import QueryService
from tests.builders import report_row_raw, server_detail
from tests.fakes import FakeRedis, InMemoryQueryRepository

ANCHOR = datetime(2026, 5, 12, tzinfo=UTC)


def _service(**seed: Any) -> QueryService:
    return QueryService(cast("Any", InMemoryQueryRepository(seed)), cast("Any", FakeRedis()))


def _three_hosts() -> dict[str, Any]:
    return {
        "list_server_ids": [1, 2, 3],
        "resolve_server_ids": {"p1": 1, "p2": 2, "p3": 3},
        "get_servers": [server_detail(n, f"host-{n}") for n in (1, 2, 3)],
        "get_report_aggregate": [
            report_row_raw(server_id=1, public_id="p1", hostname="host-1", cpu_p95_pct=88.0, mem_p95_pct=93.0),
            report_row_raw(server_id=2, public_id="p2", hostname="host-2", cpu_p95_pct=1.0, mem_p95_pct=4.0),
            report_row_raw(server_id=3, public_id="p3", hostname="host-3"),
        ],
    }


async def test_get_report_rows_preserve_requested_servers():
    service = _service(**_three_hosts())

    result = await service.get_report([1, 2, 3], period_days=14, end=ANCHOR)

    assert {row.hostname for row in result.rows} == {"host-1", "host-2", "host-3"}
    assert result.total == 3


async def test_get_report_classifies_each_host():
    service = _service(**_three_hosts())

    rows = {r.hostname: r for r in (await service.get_report([1, 2, 3], period_days=14, end=ANCHOR)).rows}

    assert rows["host-1"].recommendation == "under_provisioned"
    assert rows["host-2"].recommendation == "over_provisioned"
    assert rows["host-3"].recommendation == "insufficient_data"


async def test_get_report_empty_when_no_servers():
    service = _service(list_server_ids=[], get_servers=[], get_report_aggregate=[])

    result = await service.get_report([], period_days=14, end=ANCHOR)

    assert result.rows == []
    assert result.total == 0


async def test_resolve_server_id_returns_none_for_unknown():
    service = _service(resolve_server_ids={"p1": 1})

    assert await service.resolve_server_id("p1") == 1
    assert await service.resolve_server_id("ghost") is None


async def test_get_server_returns_none_when_repo_empty():
    service = _service(get_server=None)

    assert await service.get_server(1) is None


async def test_get_environment_report_carries_anchor_and_window():
    service = _service(**_three_hosts())

    summary = await service.get_environment_report(time_range="14d", anchor_at=ANCHOR)

    assert summary.anchor_at == ANCHOR
    assert summary.time_range == "14d"
    assert summary.base.period_days == 14
