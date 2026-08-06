"""report_generator — parent job -> 보고서 생성 디스패치 (비동기 워커·발행 공유 단일 진실).

scope/ids 분기, N대 child fan-out, 생성 불가(ReportGenerationError), 부분 실패 예외 전파를 검증.
env_report_to_dict(실제 ViewModel 직렬화)는 디스패치 테스트 범위 밖이라 stub 으로 우회.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from assessment_engine.web.services import report_generator
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.services.report_generator import (
    ReportGenerationError,
    build_report_result_for_job,
)
from assessment_engine.web.services.report_result import REPORT_KIND_ENV
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
    # env_report_to_dict 는 실제 ViewModel 직렬화 — 디스패치 분기 테스트는 dict 변환을 stub.
    def _stub(vm: object) -> JsonObject:
        return {"vm": "snap"}

    monkeypatch.setattr(report_generator, "env_report_to_dict", _stub)


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
    qs.resolve_server_ids = AsyncMock(return_value={})  # 매칭 0
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
    assert "child_jobs" not in result  # 1대는 child fan-out 없음
    ds.emit_report.assert_not_called()


async def test_server_multi_fans_out_children():
    qs = AsyncMock()
    qs.resolve_server_ids = AsyncMock(return_value={"a": 1, "b": 2})
    qs.get_attention_signals = AsyncMock(return_value=_empty_attention())
    child = MagicMock()
    child.base.rows = [MagicMock(hostname="h")]
    # A5: child fan-out 은 build_child_prefetched_reports 배치 경로 (get_single_server_report N회 아님).
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
    """child 생성 중 예외는 함수 밖으로 전파 -> 워커가 parent 를 failed 로 전이(부분 succeeded parent 차단)."""
    qs = AsyncMock()
    qs.resolve_server_ids = AsyncMock(return_value={"a": 1, "b": 2})
    qs.get_attention_signals = AsyncMock(return_value=_empty_attention())
    # child 생성(배치) 중 예외는 함수 밖으로 전파 -> 워커가 parent failed (emit 0, orphan 없음).
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
    """실제 `QueryService` 를 대역 repo·redis 로 조립한다 — 형제 메서드 stub 0."""
    return QueryService(cast("Any", InMemoryQueryRepository(seed)), cast("Any", FakeRedis()))


async def test_build_child_prefetched_reports_matches_per_server():
    """A5: 배치 조회 결과를 server 별로 정확히 매칭 — 서버 간 데이터 섞임 방지 가드.

    형제 메서드를 stub 하지 않고 실제 서비스를 조립해 돌린다. stub 하면 "지금 어느 메서드가 어느
    메서드를 부르는가" 를 고정해 버려서, 그 배치를 바꾸는 리팩토링이 동작을 보존해도 깨진다.
    """
    service = _service_with(
        report_aggregate=[
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
    """sid_map 에 없는 public_id 는 (pid, None) — 미존재 서버 skip."""
    service = _service_with(
        report_aggregate=[report_row_raw(server_id=1, public_id="pa", hostname="host-a")],
        get_servers=[_seed_detail(1, "host-a")],
        resolve_server_ids={"pa": 1},
    )
    anchor = datetime(2026, 5, 12, tzinfo=UTC)

    out = await service.build_child_prefetched_reports(["pa", "ghost"], {"pa": 1}, "customer", "7d", anchor)

    by_pid = dict(out)
    assert by_pid["ghost"] is None
    assert by_pid["pa"] is not None


async def test_report_trend_uses_valid_metric_types():
    """_build_report_trend 3 콜사이트가 유효 MetricType 만 사용 — 이름 drift 회귀 가드.

    'disk.usage_percent' -> 'fs.usage_percent' 개명 후 report 콜사이트가 미추종해 보고서 3경로
    전부 500(unsupported metric_type AssertionError) 났던 회귀를 막는다.
    """
    from typing import get_args

    from assessment_engine.db.repositories.query.types import MetricType

    seen: list[str] = []

    async def _mt(metric_type: str, *args: Any, **kwargs: Any) -> list[Any]:
        seen.append(metric_type)
        return []

    repo = MagicMock()
    repo.metric_trend = _mt
    svc = QueryService(repo, MagicMock())
    await svc._build_report_trend("24h", datetime(2026, 1, 1, tzinfo=UTC), [1])

    assert seen == ["cpu.usage_percent", "mem.usage_percent", "fs.usage_percent"]
    assert set(seen) <= set(get_args(MetricType.__value__))
