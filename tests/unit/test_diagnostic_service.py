"""diagnostic_service — input_hash·anchor 순수 함수 + emit_report 핵심 시나리오 (ADR 0004).

AI 진단 독립 발행(submit)은 engineer 보고서 발행(emit_report) 통합으로 폐기 — 본 테스트는
emit_report (정적 스냅샷 저장 + engineer narrative publish) 와 발행 helper(_compute_hash/_normalize_anchor)만 다룬다.
"""

import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from assessment_engine.diagnostic.report_result import (
    ENV_NARRATIVE_KEY,
    REPORT_KIND_ENV,
    REPORT_KIND_SUMMARY,
    build_narrative_entry,
    build_report_result,
)
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


# ─── report_result 구조 helper (web <-> worker 공유 계약) ──────────────────


def test_build_report_result_defaults():
    r = build_report_result(kind=REPORT_KIND_ENV, snapshot={"x": 1}, view="customer")
    assert r["kind"] == REPORT_KIND_ENV
    assert r["snapshot"] == {"x": 1}
    assert r["view"] == "customer"
    assert r["narrative_status"] == "none"
    assert r["narratives"] == {}
    assert r["aux"] == {}


def test_build_report_result_engineer_pending_with_aux():
    r = build_report_result(
        kind=REPORT_KIND_SUMMARY,
        snapshot={},
        view="engineer",
        narrative_status="pending",
        aux={"attention_by_host": {"h1": {"gap": "x"}}},
    )
    assert r["narrative_status"] == "pending"
    assert r["aux"]["attention_by_host"]["h1"]["gap"] == "x"


def test_build_narrative_entry_succeeded_and_failed():
    ok = build_narrative_entry(
        status="succeeded",
        narrative="요약",
        classification="optimal",
        classification_label_kr="optimal",
        recommendation_action="no_action",
    )
    assert ok == {
        "narrative": "요약",
        "classification": "optimal",
        "classification_label_kr": "optimal",
        "recommendation_action": "no_action",
        "status": "succeeded",
        "error": None,
    }
    bad = build_narrative_entry(status="failed", error="TimeoutError")
    assert bad["status"] == "failed"
    assert bad["narrative"] is None
    assert bad["error"] == "TimeoutError"


# ─── DiagnosticService.emit_report ──────────────────────────────────────


@pytest.fixture
def stub_session_factory():
    """async context manager session factory — repo 호출은 외부 mock 으로 캡처."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session)
    return factory


@pytest.fixture
def stub_broker_channel():
    exchange = AsyncMock()
    exchange.publish = AsyncMock()
    channel = AsyncMock()
    channel.get_exchange = AsyncMock(return_value=exchange)
    return channel, exchange


@pytest.fixture
def stub_diag_repo():
    return AsyncMock()


def _service(session_factory, channel, diag_repo):
    return DiagnosticService(
        query_repo=AsyncMock(),
        session_factory=session_factory,
        diagnostic_repo_factory=lambda s: diag_repo,
        broker_channel=channel,
        redis=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_emit_report_customer_marks_succeeded_no_publish(
    stub_session_factory, stub_broker_channel, stub_diag_repo
):
    """customer 보고서 — narrative 없이 즉시 mark_succeeded(narrative_status=none), publish 없음."""
    stub_diag_repo.enqueue = AsyncMock(return_value="cust-job")
    stub_diag_repo.mark_succeeded = AsyncMock()
    channel, exchange = stub_broker_channel
    service = _service(stub_session_factory, channel, stub_diag_repo)

    job_id = await service.emit_report(
        view="customer",
        scope="environment",
        kind=REPORT_KIND_ENV,
        snapshot={"k": 1},
        server_public_ids=["b", "a"],
        time_range="14d",
        anchor_at=_FIXED_ANCHOR,
    )
    assert job_id == "cust-job"
    result_arg = stub_diag_repo.mark_succeeded.call_args[0][1]
    assert result_arg["narrative_status"] == "none"
    assert result_arg["kind"] == REPORT_KIND_ENV
    stub_diag_repo.save_report_snapshot.assert_not_called()
    exchange.publish.assert_not_called()
    # anchor_at 항상 input_params 저장 (worker 윈도우 재현)
    enqueue_arg = stub_diag_repo.enqueue.call_args[0][0]
    assert enqueue_arg.input_params["anchor_at"] == "2026-05-12T00:00:00+00:00"
    assert enqueue_arg.input_params["server_public_ids"] == ["a", "b"]


@pytest.mark.asyncio
async def test_emit_report_engineer_saves_snapshot_pending_and_publishes(
    stub_session_factory, stub_broker_channel, stub_diag_repo
):
    """engineer 보고서 — save_report_snapshot(pending) + worker publish 1회. mark_succeeded 없음."""
    stub_diag_repo.enqueue = AsyncMock(return_value="eng-job")
    stub_diag_repo.save_report_snapshot = AsyncMock()
    channel, exchange = stub_broker_channel
    service = _service(stub_session_factory, channel, stub_diag_repo)

    job_id = await service.emit_report(
        view="engineer",
        scope="server",
        kind=REPORT_KIND_SUMMARY,
        snapshot={"k": 2},
        server_public_ids=["a", "b"],
        time_range="7d",
        anchor_at=_FIXED_ANCHOR,
        aux={"attention_by_host": {}},
    )
    assert job_id == "eng-job"
    result_arg = stub_diag_repo.save_report_snapshot.call_args[0][1]
    assert result_arg["narrative_status"] == "pending"
    assert result_arg["narratives"] == {}
    stub_diag_repo.mark_succeeded.assert_not_called()
    assert exchange.publish.await_count == 1


@pytest.mark.asyncio
async def test_emit_report_server_single_adds_singular_key(stub_session_factory, stub_broker_channel, stub_diag_repo):
    """server scope 1대 — input_params 에 server_public_ids(복수) + server_public_id(단수) 둘 다."""
    stub_diag_repo.enqueue = AsyncMock(return_value="single-job")
    stub_diag_repo.save_report_snapshot = AsyncMock()
    channel, _ = stub_broker_channel
    service = _service(stub_session_factory, channel, stub_diag_repo)

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
async def test_emit_report_active_conflict_returns_existing_no_publish(
    stub_session_factory, stub_broker_channel, stub_diag_repo
):
    """더블클릭 — enqueue 충돌(None) → get_active_by_hash 회수 + rollback. publish 없음."""
    stub_diag_repo.enqueue = AsyncMock(return_value=None)
    stub_diag_repo.get_active_by_hash = AsyncMock(return_value="active-job")
    channel, exchange = stub_broker_channel
    session = stub_session_factory.return_value
    service = _service(stub_session_factory, channel, stub_diag_repo)

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
    exchange.publish.assert_not_called()


# ─── DiagnosticSubmitter 의존 경계 회귀 (#F4) ────────────────────────────


def test_submitter_helper_re_export_identity():
    """web.services.diagnostic_service 의 hash/anchor helper 가 diagnostic.submitter 와 동일 객체.

    submitter 가 web 의존 없이 단독 import 가능 (#F4 멀티노드). ADR 0023 후도 본질 유지.
    """
    from assessment_engine.diagnostic.submitter import _compute_hash as sub_ch
    from assessment_engine.diagnostic.submitter import _normalize_anchor as sub_na

    assert sub_ch is _compute_hash
    assert sub_na is _normalize_anchor


def test_report_result_constants_single_source():
    """REPORT_KIND_*/ENV_NARRATIVE_KEY 는 diagnostic.report_result 단일 진실 — report_serializer re-export 동일 객체."""
    from assessment_engine.web.services import report_serializer

    assert report_serializer.REPORT_KIND_ENV is REPORT_KIND_ENV
    assert report_serializer.REPORT_KIND_SUMMARY is REPORT_KIND_SUMMARY
    assert report_serializer.ENV_NARRATIVE_KEY is ENV_NARRATIVE_KEY


# ─── recommendation 상수 import sanity ───────────────────────────────────


def test_report_signal_color_catalog_imported():
    """_REPORT_SIG_* 색 단일 진실 + 임계 상수 mappers sub-package 안 정의 회귀."""
    from assessment_engine.web.services.mappers import report as m_report
    from assessment_engine.web.services.mappers import shared as m_shared

    assert m_report._REPORT_SIG_DANGER == "#b91c1c"
    assert m_report._REPORT_SIG_WARN == "#92400e"
    assert m_report._REPORT_SIG_NEUTRAL == "#1e293b"
    assert m_report._REPORT_SIG_MUTED == "#94a3b8"
    assert m_report._REPORT_SIG_SUBTLE == "#64748b"
    assert m_report._VARIANCE_BURST_RATIO == 1.5
    assert m_shared._CAPACITY_IMMINENT_DAYS == 30
    assert m_report._REBOOT_UNSTABLE_COUNT == 3
    # SATURATION_BURST_RATIO 는 recommendation 상수 인용
    from assessment_engine import recommendation

    assert m_report._SATURATION_BURST_RATIO == recommendation.CPU_SATURATION_LOAD_RATIO
