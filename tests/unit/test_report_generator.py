from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from assessment_engine.web.services.query import QueryService
from assessment_engine.web.services.report import (
    REPORT_KIND_ENV,
    ReportGenerationError,
    build_report_result_for_job,
    generator,
)
from tests.builders import report_row_raw, server_detail
from tests.fakes import FakeRedis, InMemoryQueryRepository

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject

_ANCHOR_ISO = "2026-05-12T00:00:00+00:00"


def _record(scope: str, input_params: JsonObject) -> MagicMock:
    rec = MagicMock()
    rec.scope = scope
    rec.input_params = input_params
    return rec


def _empty_attention():
    return MagicMock(gap_warnings=[], os_eol_warnings=[], agent_unstable=[])


@pytest.fixture(autouse=True)
def _stub_serializer(monkeypatch: pytest.MonkeyPatch):
    def _stub(vm: object) -> JsonObject:
        return {"vm": "snap"}

    monkeypatch.setattr(generator, "env_report_to_dict", _stub)


async def test_environment_scope_generates_result():
    qs = AsyncMock()
    summary = MagicMock()
    summary.overview.total = 5
    qs.get_environment_report = AsyncMock(return_value=summary)
    rec = _record("environment", {"view": "customer", "time_range": "7d", "anchor_at": _ANCHOR_ISO})

    result = await build_report_result_for_job(qs, AsyncMock(), rec)

    assert result["kind"] == REPORT_KIND_ENV
    assert result["view"] == "customer"
    qs.get_environment_report.assert_awaited_once()


async def test_environment_no_servers_raises():
    qs = AsyncMock()
    summary = MagicMock()
    summary.overview.total = 0
    qs.get_environment_report = AsyncMock(return_value=summary)
    rec = _record("environment", {"view": "customer", "time_range": "7d", "anchor_at": _ANCHOR_ISO})

    with pytest.raises(ReportGenerationError):
        await build_report_result_for_job(qs, AsyncMock(), rec)


async def test_server_no_valid_ids_raises():
    qs = AsyncMock()
    qs.resolve_server_ids = AsyncMock(return_value={})
    rec = _record(
        "server",
        {"view": "engineer", "time_range": "7d", "anchor_at": _ANCHOR_ISO, "server_public_ids": ["x"]},
    )
    with pytest.raises(ReportGenerationError):
        await build_report_result_for_job(qs, AsyncMock(), rec)


async def test_server_single_generates_without_children():
    qs = AsyncMock()
    qs.resolve_server_ids = AsyncMock(return_value={"a": 1})
    qs.get_attention_signals = AsyncMock(return_value=_empty_attention())
    single = MagicMock()
    single.base.rows = [MagicMock(hostname="h1")]
    qs.get_single_server_report = AsyncMock(return_value=single)
    ds = AsyncMock()
    rec = _record(
        "server",
        {"view": "engineer", "time_range": "7d", "anchor_at": _ANCHOR_ISO, "server_public_ids": ["a"]},
    )

    result = await build_report_result_for_job(qs, ds, rec)

    assert result["kind"] == REPORT_KIND_ENV
    assert "child_jobs" not in result
    ds.emit_report.assert_not_called()


async def test_server_multi_fans_out_children():
    qs = AsyncMock()
    qs.resolve_server_ids = AsyncMock(return_value={"a": 1, "b": 2})
    qs.get_attention_signals = AsyncMock(return_value=_empty_attention())
    child = MagicMock()
    child.base.rows = [MagicMock(hostname="h")]
    qs.build_child_prefetched_reports = AsyncMock(return_value=[("a", child), ("b", child)])
    selection = MagicMock()
    selection.base.rows = [MagicMock(hostname="h")]
    qs.get_selection_report = AsyncMock(return_value=selection)
    ds = AsyncMock()
    ds.emit_report = AsyncMock(side_effect=["c-a", "c-b"])
    rec = _record(
        "server",
        {"view": "engineer", "time_range": "7d", "anchor_at": _ANCHOR_ISO, "server_public_ids": ["a", "b"]},
    )

    result = await build_report_result_for_job(qs, ds, rec)

    assert result["child_jobs"] == {"a": "c-a", "b": "c-b"}
    assert ds.emit_report.await_count == 2


async def test_server_child_error_propagates():
    qs = AsyncMock()
    qs.resolve_server_ids = AsyncMock(return_value={"a": 1, "b": 2})
    qs.get_attention_signals = AsyncMock(return_value=_empty_attention())
    qs.build_child_prefetched_reports = AsyncMock(side_effect=RuntimeError("db down"))
    rec = _record(
        "server",
        {"view": "engineer", "time_range": "7d", "anchor_at": _ANCHOR_ISO, "server_public_ids": ["a", "b"]},
    )

    with pytest.raises(RuntimeError):
        await build_report_result_for_job(qs, AsyncMock(), rec)


def _seed_detail(server_id: int, hostname: str) -> Any:
    return server_detail(server_id, hostname)


def _service_with(**seed: Any) -> QueryService:
    return QueryService(cast("Any", InMemoryQueryRepository(seed)), cast("Any", FakeRedis()))


async def test_build_child_prefetched_reports_matches_per_server():
    service = _service_with(
        get_report_aggregate=[
            report_row_raw(server_id=1, public_id="pa", hostname="host-a"),
            report_row_raw(server_id=2, public_id="pb", hostname="host-b"),
        ],
        get_servers=[_seed_detail(1, "host-a"), _seed_detail(2, "host-b")],
        resolve_server_ids={"pa": 1, "pb": 2},
    )
    anchor = datetime(2026, 5, 12, tzinfo=UTC)

    out = await service.build_child_prefetched_reports(["pa", "pb"], {"pa": 1, "pb": 2}, "engineer", "7d", anchor)

    by_pid = dict(out)
    assert by_pid["pa"] is not None
    assert by_pid["pb"] is not None
    assert [r.hostname for r in by_pid["pa"].base.rows] == ["host-a"]
    assert [r.hostname for r in by_pid["pb"].base.rows] == ["host-b"]


async def test_build_child_prefetched_reports_missing_server_yields_none():
    service = _service_with(
        get_report_aggregate=[report_row_raw(server_id=1, public_id="pa", hostname="host-a")],
        get_servers=[_seed_detail(1, "host-a")],
        resolve_server_ids={"pa": 1},
    )
    anchor = datetime(2026, 5, 12, tzinfo=UTC)

    out = await service.build_child_prefetched_reports(["pa", "ghost"], {"pa": 1}, "customer", "7d", anchor)

    by_pid = dict(out)
    assert by_pid["ghost"] is None
    assert by_pid["pa"] is not None


async def test_report_trend_uses_valid_metric_types():
    from typing import get_args

    from assessment_engine.db.repositories.query.types import MetricType

    seen: list[str] = []

    async def _mt(metric_type: str, *args: Any, **kwargs: Any) -> list[Any]:
        seen.append(metric_type)
        return []

    repo = MagicMock()
    repo.get_metric_trend = _mt
    svc = QueryService(repo, MagicMock())
    await svc._build_report_trend("24h", datetime(2026, 1, 1, tzinfo=UTC), [1])

    assert seen == ["cpu.usage_percent", "mem.usage_percent", "fs.usage_percent"]
    assert set(seen) <= set(get_args(MetricType.__value__))
