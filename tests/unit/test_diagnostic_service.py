"""diagnostic_service — input_hash·anchor 순수 함수 + emit_report 핵심 시나리오 (ADR 0004).

보고서 발행(emit_report)은 발행 시점 정적 스냅샷 저장 후 즉시 succeeded — customer/engineer 동일
(별도 비동기·publish 없음, #C1). 본 테스트는 emit_report + 발행 helper(_compute_hash/_normalize_anchor)만 다룬다.
"""

import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from assessment_engine.diagnostic.report_result import REPORT_KIND_ENV, build_report_result
from assessment_engine.web.services.diagnostic_service import (
    DiagnosticService,
    _compute_hash,
    _normalize_anchor,
)

_FIXED_ANCHOR = datetime(2026, 5, 12, 0, 0, 0, tzinfo=UTC)


# ─── _normalize_anchor ──────────────────────────────────────────────────


def test_normalize_anchor_truncates_seconds_microseconds():
    raw = datetime(2026, 5, 12, 10, 30, 45, 123456, tzinfo=UTC)
    out = _normalize_anchor(raw)
    assert out.second == 0 and out.microsecond == 0
    assert out.hour == 10 and out.minute == 30


def test_normalize_anchor_naive_assumed_utc():
    naive = datetime(2026, 5, 12, 10, 30)
    out = _normalize_anchor(naive)
    assert out.tzinfo is UTC


def test_normalize_anchor_none_returns_current_minute():
    out = _normalize_anchor(None)
    assert out.tzinfo is UTC
    assert out.second == 0 and out.microsecond == 0


# ─── _compute_hash (결정성 + 순서 무관) ──────────────────────────────────


def test_compute_hash_deterministic():
    p = {"server_public_id": "x", "time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00"}
    assert _compute_hash("server", p) == _compute_hash("server", p)
    assert len(_compute_hash("server", p)) == 64


def test_compute_hash_key_order_independent():
    a = _compute_hash("server", {"server_public_id": "x", "time_range": "14d", "anchor_at": "T"})
    b = _compute_hash("server", {"anchor_at": "T", "server_public_id": "x", "time_range": "14d"})
    assert a == b


def test_compute_hash_scope_separates_namespace():
    p = {"time_range": "14d", "anchor_at": "T"}
    assert _compute_hash("server", p) != _compute_hash("environment", p)


def test_compute_hash_matches_explicit_sha256():
    scope = "server"
    params = {"server_public_id": "abc", "time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00"}
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    expected = hashlib.sha256(f"{scope}|{canonical}".encode()).hexdigest()
    assert _compute_hash(scope, params) == expected


# ─── report_result 구조 helper (web <-> 스냅샷 저장 공유 계약) ──────────────


def test_build_report_result_defaults():
    r = build_report_result(kind=REPORT_KIND_ENV, snapshot={"x": 1}, view="customer")
    assert r == {"kind": REPORT_KIND_ENV, "snapshot": {"x": 1}, "view": "customer", "aux": {}}


def test_build_report_result_with_aux():
    r = build_report_result(
        kind=REPORT_KIND_ENV,
        snapshot={},
        view="engineer",
        aux={"attention_by_host": {"h1": {"gap": "x"}}},
    )
    assert r["view"] == "engineer"
    assert r["aux"]["attention_by_host"]["h1"]["gap"] == "x"


# ─── DiagnosticService.emit_report ──────────────────────────────────────


@pytest.fixture
def stub_session_factory():
    """async context manager session factory — repo 호출은 외부 mock 으로 캡처."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=session)


@pytest.fixture
def stub_diag_repo():
    return AsyncMock()


def _service(session_factory, diag_repo):
    return DiagnosticService(
        query_repo=AsyncMock(),
        session_factory=session_factory,
        diagnostic_repo_factory=lambda s: diag_repo,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("view", ["customer", "engineer"])
async def test_emit_report_marks_succeeded(view, stub_session_factory, stub_diag_repo):
    """발행 — customer/engineer 동일: enqueue 후 즉시 mark_succeeded(result=build_report_result), commit."""
    stub_diag_repo.enqueue = AsyncMock(return_value="job-1")
    stub_diag_repo.mark_succeeded = AsyncMock()
    session = stub_session_factory.return_value
    service = _service(stub_session_factory, stub_diag_repo)

    job_id = await service.emit_report(
        view=view,
        scope="environment",
        kind=REPORT_KIND_ENV,
        snapshot={"k": 1},
        server_public_ids=["b", "a"],
        time_range="14d",
        anchor_at=_FIXED_ANCHOR,
    )
    assert job_id == "job-1"
    stub_diag_repo.mark_succeeded.assert_awaited_once()
    result_arg = stub_diag_repo.mark_succeeded.call_args[0][1]
    assert result_arg == {"kind": REPORT_KIND_ENV, "snapshot": {"k": 1}, "view": view, "aux": {}}
    session.commit.assert_awaited_once()
    # job_type = f"{view}_report", input_params anchor 보존 + server_public_ids 정렬 저장
    create_arg = stub_diag_repo.enqueue.call_args[0][0]
    assert create_arg.job_type == f"{view}_report"
    assert create_arg.input_params["anchor_at"] == "2026-05-12T00:00:00+00:00"
    assert create_arg.input_params["server_public_ids"] == ["a", "b"]


@pytest.mark.asyncio
async def test_emit_report_server_single_adds_singular_key(stub_session_factory, stub_diag_repo):
    """server scope 1대 — input_params 에 server_public_ids(복수) + server_public_id(단수) 둘 다."""
    stub_diag_repo.enqueue = AsyncMock(return_value="single-job")
    stub_diag_repo.mark_succeeded = AsyncMock()
    service = _service(stub_session_factory, stub_diag_repo)

    await service.emit_report(
        view="engineer",
        scope="server",
        kind=REPORT_KIND_ENV,
        snapshot={},
        server_public_ids=["uuid-a"],
        time_range="14d",
        anchor_at=_FIXED_ANCHOR,
    )
    params = stub_diag_repo.enqueue.call_args[0][0].input_params
    assert params["server_public_ids"] == ["uuid-a"]
    assert params["server_public_id"] == "uuid-a"


@pytest.mark.asyncio
async def test_emit_report_child_jobs_stored_in_result(stub_session_factory, stub_diag_repo):
    """selection N대 — child_jobs 맵은 input_hash 미포함(더블클릭 dedup 보존), result 에만 보관."""
    stub_diag_repo.enqueue = AsyncMock(return_value="sel-job")
    stub_diag_repo.mark_succeeded = AsyncMock()
    service = _service(stub_session_factory, stub_diag_repo)

    await service.emit_report(
        view="engineer",
        scope="server",
        kind=REPORT_KIND_ENV,
        snapshot={},
        server_public_ids=["a", "b"],
        time_range="14d",
        anchor_at=_FIXED_ANCHOR,
        child_jobs={"a": "child-a", "b": "child-b"},
    )
    result_arg = stub_diag_repo.mark_succeeded.call_args[0][1]
    assert result_arg["child_jobs"] == {"a": "child-a", "b": "child-b"}
    assert "child_jobs" not in stub_diag_repo.enqueue.call_args[0][0].input_params


@pytest.mark.asyncio
async def test_emit_report_active_conflict_returns_existing(stub_session_factory, stub_diag_repo):
    """더블클릭 — enqueue 충돌(None) → get_active_by_hash 회수 + rollback. mark_succeeded·commit 없음."""
    stub_diag_repo.enqueue = AsyncMock(return_value=None)
    stub_diag_repo.get_active_by_hash = AsyncMock(return_value="active-job")
    stub_diag_repo.mark_succeeded = AsyncMock()
    session = stub_session_factory.return_value
    service = _service(stub_session_factory, stub_diag_repo)

    job_id = await service.emit_report(
        view="engineer",
        scope="environment",
        kind=REPORT_KIND_ENV,
        snapshot={},
        server_public_ids=["a"],
        time_range="14d",
        anchor_at=_FIXED_ANCHOR,
    )
    assert job_id == "active-job"
    session.rollback.assert_awaited_once()
    stub_diag_repo.mark_succeeded.assert_not_called()
    session.commit.assert_not_called()


# ─── report_result 단일 진실 + recommendation 상수 import sanity ──────────


def test_report_result_constants_single_source():
    """REPORT_KIND_ENV 는 diagnostic.report_result 단일 진실 — report_serializer re-export 동일 객체."""
    from assessment_engine.web.services import report_serializer

    assert report_serializer.REPORT_KIND_ENV is REPORT_KIND_ENV


def test_report_threshold_constants_imported():
    """보고서 표시 임계 상수 mappers sub-package 안 정의 회귀 (색 카탈로그는 폐기 — 표시 미사용)."""
    from assessment_engine import recommendation
    from assessment_engine.web.services.mappers import report as m_report
    from assessment_engine.web.services.mappers import shared as m_shared

    assert m_report._VARIANCE_BURST_RATIO == 1.5
    assert m_shared._CAPACITY_IMMINENT_DAYS == 30
    assert m_report._REBOOT_UNSTABLE_COUNT == 3
    # SATURATION_BURST_RATIO 는 recommendation 상수 인용
    assert m_report._SATURATION_BURST_RATIO == recommendation.CPU_SATURATION_LOAD_RATIO
