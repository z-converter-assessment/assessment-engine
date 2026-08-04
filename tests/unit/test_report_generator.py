"""report_generator — parent job -> 보고서 생성 디스패치 (비동기 워커·발행 공유 단일 진실).

scope/ids 분기, N대 child fan-out, 생성 불가(ReportGenerationError), 부분 실패 예외 전파를 검증.
env_report_to_dict(실제 ViewModel 직렬화)는 디스패치 테스트 범위 밖이라 stub 으로 우회.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from assessment_engine.diagnostic.report_result import REPORT_KIND_ENV
from assessment_engine.json_types import JsonObject
from assessment_engine.web.services import report_generator
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.services.report_generator import (
    ReportGenerationError,
    build_report_result_for_job,
)

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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_environment_no_servers_raises():
    qs = AsyncMock()
    summary = MagicMock()
    summary.overview.total = 0
    qs.get_environment_report = AsyncMock(return_value=summary)
    rec = _record("environment", {"view": "customer", "time_range": "7d", "anchor_at": _ANCHOR_ISO})

    with pytest.raises(ReportGenerationError):
        await build_report_result_for_job(qs, AsyncMock(), rec)


@pytest.mark.asyncio
async def test_server_no_valid_ids_raises():
    qs = AsyncMock()
    qs.resolve_server_ids = AsyncMock(return_value={})  # 매칭 0
    rec = _record(
        "server",
        {"view": "engineer", "time_range": "7d", "anchor_at": _ANCHOR_ISO, "server_public_ids": ["x"]},
    )
    with pytest.raises(ReportGenerationError):
        await build_report_result_for_job(qs, AsyncMock(), rec)


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_build_child_prefetched_reports_matches_per_server():
    """A5: 배치 조회 결과를 server 별로 정확히 매칭해 prefetch 구성 (서버 간 데이터 섞임 방지 가드)."""
    qs = QueryService.__new__(QueryService)  # 생성자 우회 — repo·메서드만 stub
    qs.repo = AsyncMock()
    raw1 = MagicMock(server_id=1)
    raw2 = MagicMock(server_id=2)
    qs._assemble_report_raws = AsyncMock(return_value=[raw1, raw2])
    qs.repo.get_servers = AsyncMock(return_value=[MagicMock(id=1), MagicMock(id=2)])
    qs.repo.report_memory_breakdown_batch = AsyncMock(return_value={})
    qs.repo.report_cpu_breakdown_batch = AsyncMock(return_value={})

    captured: JsonObject = {}

    async def fake_single(
        pid: str,
        *,
        view: str,
        time_range: str,
        anchor_at: datetime,
        attention: object,
        prefetch: object,
    ) -> MagicMock:
        captured[pid] = prefetch
        return MagicMock()

    qs.get_single_server_report = AsyncMock(side_effect=fake_single)
    sid_map = {"pa": 1, "pb": 2}
    anchor = datetime(2026, 5, 12, tzinfo=UTC)

    out = await qs.build_child_prefetched_reports(["pa", "pb"], sid_map, "engineer", "7d", anchor, None)

    assert len(out) == 2
    assert captured["pa"].raw is raw1 and captured["pa"].detail.id == 1  # server 1 -> pa
    assert captured["pb"].raw is raw2 and captured["pb"].detail.id == 2  # server 2 -> pb


@pytest.mark.asyncio
async def test_build_child_prefetched_reports_missing_server_yields_none():
    """sid_map 에 없는 public_id 는 (pid, None) — 미존재 서버 skip."""
    qs = QueryService.__new__(QueryService)
    qs.repo = AsyncMock()
    qs._assemble_report_raws = AsyncMock(return_value=[MagicMock(server_id=1)])
    qs.repo.get_servers = AsyncMock(return_value=[MagicMock(id=1)])
    qs.get_single_server_report = AsyncMock(return_value=MagicMock())
    anchor = datetime(2026, 5, 12, tzinfo=UTC)

    out = await qs.build_child_prefetched_reports(["pa", "ghost"], {"pa": 1}, "customer", "7d", anchor, None)

    assert dict(out)["ghost"] is None
    assert dict(out)["pa"] is not None


@pytest.mark.asyncio
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
    assert set(seen) <= set(get_args(MetricType))
