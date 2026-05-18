"""diagnostic_service — input_hash·input_params 순수 함수 + submit 핵심 시나리오 (ADR 0004)."""
import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from assessment_engine.db.repositories.outbound import DiagnosticJobRecord
from assessment_engine.web.services.diagnostic_service import (
    DiagnosticBadRequest,
    DiagnosticNotFound,
    DiagnosticService,
    _build_input_params,
    _compute_hash,
    _normalize_anchor,
    to_panel_payload,
)

_FIXED_ANCHOR = datetime(2026, 5, 12, 0, 0, 0, tzinfo=UTC)


# ─── _build_input_params (scope별 키 카탈로그) ───────────────────────────

def test_build_input_params_server_scope():
    p = _build_input_params("server", "uuid-abc", "14d", _FIXED_ANCHOR)
    assert p == {
        "server_public_id": "uuid-abc",
        "time_range":       "14d",
        "anchor_at":        "2026-05-12T00:00:00+00:00",
    }


def test_build_input_params_environment_scope_drops_server_public_id():
    """environment scope는 server_public_id 키 없음 — input_hash 안정성."""
    p = _build_input_params("environment", None, "14d", _FIXED_ANCHOR)
    assert p == {"time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00"}
    assert "server_public_id" not in p


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
    """같은 입력 → 같은 hash."""
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


def test_compute_hash_anchor_change_changes_hash():
    a = _compute_hash("environment", {"time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00"})
    b = _compute_hash("environment", {"time_range": "14d", "anchor_at": "2026-05-12T01:00:00+00:00"})
    assert a != b


def test_compute_hash_matches_explicit_sha256():
    scope = "server"
    params = {"server_public_id": "abc", "time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00"}
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    expected = hashlib.sha256(f"{scope}|{canonical}".encode()).hexdigest()
    assert _compute_hash(scope, params) == expected


# ─── DiagnosticService.submit 시나리오 ──────────────────────────────────


@pytest.fixture
def stub_session_factory():
    """async context manager session factory — repo 호출은 외부 mock으로 캡처."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session)
    return factory


@pytest.fixture
def stub_broker_channel():
    """publish가 호출되면 mock exchange.publish가 await 가능하도록."""
    exchange = AsyncMock()
    exchange.publish = AsyncMock()
    channel = AsyncMock()
    channel.get_exchange = AsyncMock(return_value=exchange)
    return channel, exchange


@pytest.fixture
def stub_query_repo():
    repo = AsyncMock()
    # 기본: 모든 public_id resolve 성공
    repo.resolve_server_ids = AsyncMock(side_effect=lambda pids: {p: i for i, p in enumerate(pids, start=1)})
    return repo


@pytest.fixture
def stub_diag_repo():
    return AsyncMock()


@pytest.mark.asyncio
async def test_submit_environment_new_enqueue_publishes(
    stub_session_factory, stub_broker_channel, stub_query_repo, stub_diag_repo,
):
    """enqueue 성공 시 publish 1회."""
    stub_diag_repo.enqueue = AsyncMock(return_value="new-job-id")

    channel, exchange = stub_broker_channel
    service = DiagnosticService(
        query_repo=stub_query_repo,
        session_factory=stub_session_factory,
        diagnostic_repo_factory=lambda s: stub_diag_repo,
        broker_channel=channel,
        redis=AsyncMock(),
    )
    job_ids = await service.submit("environment", None, "14d", _FIXED_ANCHOR)
    assert job_ids == ["new-job-id"]
    assert exchange.publish.await_count == 1


@pytest.mark.asyncio
async def test_submit_environment_active_conflict_returns_existing(
    stub_session_factory, stub_broker_channel, stub_query_repo, stub_diag_repo,
):
    """enqueue 충돌(None) → active 회수 → publish 없음."""
    stub_diag_repo.enqueue = AsyncMock(return_value=None)
    stub_diag_repo.get_active_by_hash = AsyncMock(return_value="active-job-id")

    channel, exchange = stub_broker_channel
    service = DiagnosticService(
        query_repo=stub_query_repo,
        session_factory=stub_session_factory,
        diagnostic_repo_factory=lambda s: stub_diag_repo,
        broker_channel=channel,
        redis=AsyncMock(),
    )
    job_ids = await service.submit("environment", None, "14d", _FIXED_ANCHOR)
    assert job_ids == ["active-job-id"]
    exchange.publish.assert_not_called()


@pytest.mark.asyncio
async def test_submit_server_scope_missing_public_id_raises_not_found(
    stub_session_factory, stub_broker_channel, stub_diag_repo,
):
    query_repo = AsyncMock()
    query_repo.resolve_server_ids = AsyncMock(return_value={})  # 모두 미존재

    channel, _ = stub_broker_channel
    service = DiagnosticService(
        query_repo=query_repo,
        session_factory=stub_session_factory,
        diagnostic_repo_factory=lambda s: stub_diag_repo,
        broker_channel=channel,
        redis=AsyncMock(),
    )
    with pytest.raises(DiagnosticNotFound):
        await service.submit("server", ["missing-uuid"], "14d", _FIXED_ANCHOR)


@pytest.mark.asyncio
async def test_submit_server_scope_without_ids_raises_bad_request(
    stub_session_factory, stub_broker_channel, stub_query_repo, stub_diag_repo,
):
    channel, _ = stub_broker_channel
    service = DiagnosticService(
        query_repo=stub_query_repo,
        session_factory=stub_session_factory,
        diagnostic_repo_factory=lambda s: stub_diag_repo,
        broker_channel=channel,
        redis=AsyncMock(),
    )
    with pytest.raises(DiagnosticBadRequest):
        await service.submit("server", None, "14d", _FIXED_ANCHOR)
    with pytest.raises(DiagnosticBadRequest):
        await service.submit("server", [], "14d", _FIXED_ANCHOR)


def test_to_panel_payload_none_returns_none():
    assert to_panel_payload(None) is None


def test_to_panel_payload_shape_matches_render_contract():
    rec = DiagnosticJobRecord(
        id="job-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", job_type="ai_diagnostic", scope="environment",
        input_params={"time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00"},
        input_hash="h",
        status="succeeded", progress_stage=None,
        result={"narrative": "x"}, error_message=None,
        created_at=datetime(2026, 5, 12, tzinfo=UTC),
        started_at=datetime(2026, 5, 12, tzinfo=UTC),
        finished_at=datetime(2026, 5, 12, 1, 0, 0, tzinfo=UTC),
        requested_by=None,
    )
    p = to_panel_payload(rec)
    # mapper view의 핵심 필드 — JS·SSR이 직접 표시
    expected_keys = {
        "job_id", "short_id", "scope", "server_public_id",
        "status", "status_badge_class", "progress_stage", "progress_label_kr",
        "time_range", "window_label_kr", "anchor_at",
        "classification", "classification_label_kr", "classification_badge_class",
        "recommendation_action",
        "result", "error_message",
        "created_at", "started_at", "finished_at", "requested_by",
    }
    assert set(p) == expected_keys
    assert p["finished_at"] == "2026-05-12T01:00:00+00:00"  # ISO UTC (F2)
    assert p["short_id"] == "job-aaaa"
    assert p["window_label_kr"] == "14일"
    assert p["status_badge_class"] == "badge-ok"


@pytest.mark.asyncio
async def test_get_latest_uses_context_keys(
    stub_session_factory, stub_broker_channel, stub_query_repo, stub_diag_repo,
):
    """get_latest는 input_hash가 아닌 (scope, time_range, server_public_id) context 기반.

    anchor_at 무관 — 매 호출마다 다른 anchor를 사용해도 같은 latest 매칭.
    """
    stub_diag_repo.get_latest_by_context = AsyncMock(return_value=None)

    channel, _ = stub_broker_channel
    service = DiagnosticService(
        query_repo=stub_query_repo,
        session_factory=stub_session_factory,
        diagnostic_repo_factory=lambda s: stub_diag_repo,
        broker_channel=channel,
        redis=AsyncMock(),
    )
    await service.get_latest("environment", None, "14d")
    stub_diag_repo.get_latest_by_context.assert_awaited_once_with("environment", "14d", None)

    await service.get_latest("server", "uuid-abc", "7d")
    stub_diag_repo.get_latest_by_context.assert_awaited_with("server", "7d", "uuid-abc")


@pytest.mark.asyncio
async def test_submit_server_scope_batch_n_servers_n_enqueues(
    stub_session_factory, stub_broker_channel, stub_query_repo, stub_diag_repo,
):
    """server_ids 3개 → 3번 enqueue + 3번 publish (옵션 A — N개 batch enqueue)."""
    stub_diag_repo.enqueue = AsyncMock(side_effect=["j1", "j2", "j3"])

    channel, exchange = stub_broker_channel
    service = DiagnosticService(
        query_repo=stub_query_repo,
        session_factory=stub_session_factory,
        diagnostic_repo_factory=lambda s: stub_diag_repo,
        broker_channel=channel,
        redis=AsyncMock(),
    )
    job_ids = await service.submit("server", ["a", "b", "c"], "14d", _FIXED_ANCHOR)
    assert job_ids == ["j1", "j2", "j3"]
    assert exchange.publish.await_count == 3


# ─── to_history_item (이력 페이지 mapper, P2 단일 변환) ──────────────────

def test_to_history_item_shape_and_fields():
    """이력 행은 template attribute access만 — JSONB 키·status badge·datetime은 그대로 전달."""
    from assessment_engine.web.services.diagnostic_mapper import to_history_item
    rec = DiagnosticJobRecord(
        id="job-abc", job_type="ai_diagnostic", scope="server",
        input_params={
            "server_public_id": "uuid-xyz",
            "time_range": "7d",
            "anchor_at": "2026-05-12T00:00:00+00:00",
        },
        input_hash="h", status="succeeded", progress_stage=None,
        result={}, error_message=None,
        created_at=datetime(2026, 5, 12, tzinfo=UTC),
        started_at=datetime(2026, 5, 12, tzinfo=UTC),
        finished_at=datetime(2026, 5, 12, 1, 0, 0, tzinfo=UTC),
        requested_by="scheduler",
    )
    item = to_history_item(rec)
    assert item["job_id"] == "job-abc"
    assert item["scope"] == "server"
    assert item["server_public_id"] == "uuid-xyz"
    assert item["time_range"] == "7d"
    assert item["anchor_at"] == "2026-05-12T00:00:00+00:00"
    assert item["status"] == "succeeded"
    assert item["status_badge_class"] == "badge-ok"
    assert item["requested_by"] == "scheduler"
    # datetime은 raw 그대로 전달 (KST는 template kst 필터, F2)
    assert item["created_at"] == datetime(2026, 5, 12, tzinfo=UTC)


def test_to_history_item_environment_scope_no_server_id():
    """environment scope는 server_public_id 키 자체가 input_params에 없음 → None."""
    from assessment_engine.web.services.diagnostic_mapper import to_history_item
    rec = DiagnosticJobRecord(
        id="job-env", job_type="ai_diagnostic", scope="environment",
        input_params={"time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00"},
        input_hash="h", status="running", progress_stage="extracting_stats",
        result=None, error_message=None,
        created_at=datetime(2026, 5, 12, tzinfo=UTC),
        started_at=None, finished_at=None, requested_by=None,
    )
    item = to_history_item(rec)
    assert item["server_public_id"] is None
    assert item["status_badge_class"] == "badge-warn"  # running
    assert item["finished_at"] is None


def test_to_history_item_missing_time_range_fallback():
    """input_params에 time_range 누락 시 표시 fallback '—' (옛 job 호환)."""
    from assessment_engine.web.services.diagnostic_mapper import to_history_item
    rec = DiagnosticJobRecord(
        id="job-old", job_type="ai_diagnostic", scope="environment",
        input_params={"anchor_at": "2026-05-12T00:00:00+00:00"},
        input_hash="h", status="failed", progress_stage=None,
        result=None, error_message="boom",
        created_at=datetime(2026, 5, 12, tzinfo=UTC),
        started_at=None, finished_at=datetime(2026, 5, 12, 1, 0, 0, tzinfo=UTC),
        requested_by=None,
    )
    item = to_history_item(rec)
    assert item["time_range"] == "—"
    assert item["status_badge_class"] == "badge-danger"  # failed


# ─── DiagnosticSubmitter 직접 import (scheduler 의존 경계 회귀) ──────────

def test_submitter_re_export_identity():
    """web.services.diagnostic_service 의 exceptions/helpers 가 diagnostic.submitter 와 동일 객체.

    scheduler 노드가 web 의존 없이 diagnostic.submitter 만 import 가능 (#F4 멀티노드).
    """
    from assessment_engine.diagnostic.submitter import (
        DiagnosticBadRequest as SubBR,
        DiagnosticNotFound as SubNF,
        DiagnosticRaceMiss as SubRM,
        _build_input_params as sub_bip,
        _compute_hash as sub_ch,
        _normalize_anchor as sub_na,
    )
    from assessment_engine.web.services.diagnostic_service import (
        DiagnosticBadRequest as WebBR,
        DiagnosticNotFound as WebNF,
        DiagnosticRaceMiss as WebRM,
    )
    assert WebBR is SubBR
    assert WebNF is SubNF
    assert WebRM is SubRM
    assert sub_bip is _build_input_params
    assert sub_ch is _compute_hash
    assert sub_na is _normalize_anchor


# ─── record_report_emission — time_range / period_days float / 단수 키 ───

@pytest.mark.asyncio
async def test_record_report_emission_server_scope_single_includes_singular_key(
    stub_session_factory, stub_broker_channel, stub_query_repo, stub_diag_repo,
):
    """server scope 1대 보고서 — input_params 에 server_public_ids 복수 + server_public_id 단수 둘 다."""
    stub_diag_repo.enqueue = AsyncMock(return_value="rep-id-1")
    stub_diag_repo.mark_succeeded = AsyncMock()
    channel, _ = stub_broker_channel
    service = DiagnosticService(
        query_repo=stub_query_repo,
        session_factory=stub_session_factory,
        diagnostic_repo_factory=lambda s: stub_diag_repo,
        broker_channel=channel,
        redis=AsyncMock(),
    )
    await service.record_report_emission(
        view="customer", scope="server", server_public_ids=["uuid-a"],
        period_days=14.0, time_range="14d",
    )
    enqueue_arg = stub_diag_repo.enqueue.call_args[0][0]
    assert enqueue_arg.scope == "server"
    assert enqueue_arg.job_type == "customer_report"
    assert enqueue_arg.input_params["server_public_ids"] == ["uuid-a"]
    assert enqueue_arg.input_params["server_public_id"] == "uuid-a"
    assert enqueue_arg.input_params["time_range"] == "14d"
    assert enqueue_arg.input_params["period_days"] == 14.0


@pytest.mark.asyncio
async def test_record_report_emission_preserves_fractional_period_days(
    stub_session_factory, stub_broker_channel, stub_query_repo, stub_diag_repo,
):
    """15m 윈도우 (period_days=0.0104) 도 input_params 에 float 그대로 보존 — 옛 int truncation 회귀 가드."""
    stub_diag_repo.enqueue = AsyncMock(return_value="rep-id-15m")
    stub_diag_repo.mark_succeeded = AsyncMock()
    channel, _ = stub_broker_channel
    service = DiagnosticService(
        query_repo=stub_query_repo,
        session_factory=stub_session_factory,
        diagnostic_repo_factory=lambda s: stub_diag_repo,
        broker_channel=channel,
        redis=AsyncMock(),
    )
    await service.record_report_emission(
        view="engineer", scope="server", server_public_ids=["uuid-b"],
        period_days=0.0104, time_range="15m",
    )
    enqueue_arg = stub_diag_repo.enqueue.call_args[0][0]
    assert enqueue_arg.input_params["period_days"] == 0.0104
    assert enqueue_arg.input_params["time_range"] == "15m"


@pytest.mark.asyncio
async def test_record_report_emission_active_conflict_returns_none(
    stub_session_factory, stub_broker_channel, stub_query_repo, stub_diag_repo,
):
    """enqueue 충돌 (None) → rollback + return None — 동시 두 발행 best-effort."""
    stub_diag_repo.enqueue = AsyncMock(return_value=None)
    channel, _ = stub_broker_channel
    session = stub_session_factory.return_value
    session.rollback = AsyncMock()
    service = DiagnosticService(
        query_repo=stub_query_repo,
        session_factory=stub_session_factory,
        diagnostic_repo_factory=lambda s: stub_diag_repo,
        broker_channel=channel,
        redis=AsyncMock(),
    )
    result = await service.record_report_emission(
        view="customer", scope="environment", server_public_ids=["a", "b"],
        period_days=14.0, time_range="14d",
    )
    assert result is None
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_report_emission_sorts_server_public_ids(
    stub_session_factory, stub_broker_channel, stub_query_repo, stub_diag_repo,
):
    """input_hash 결정성 — server_public_ids 정렬 후 저장 (같은 N대 발행은 같은 hash)."""
    stub_diag_repo.enqueue = AsyncMock(return_value="rep-id-sorted")
    stub_diag_repo.mark_succeeded = AsyncMock()
    channel, _ = stub_broker_channel
    service = DiagnosticService(
        query_repo=stub_query_repo,
        session_factory=stub_session_factory,
        diagnostic_repo_factory=lambda s: stub_diag_repo,
        broker_channel=channel,
        redis=AsyncMock(),
    )
    await service.record_report_emission(
        view="customer", scope="environment",
        server_public_ids=["zeta", "alpha", "beta"],
        period_days=14.0, time_range="14d",
    )
    enqueue_arg = stub_diag_repo.enqueue.call_args[0][0]
    assert enqueue_arg.input_params["server_public_ids"] == ["alpha", "beta", "zeta"]
    # 2대+ 라 단수 키는 추가 안 됨 (server scope 1대 한정 — 본 케이스 environment).
    assert "server_public_id" not in enqueue_arg.input_params


# ─── recommendation 상수 import sanity ───────────────────────────────────

def test_report_signal_color_catalog_imported():
    """_REPORT_SIG_* 색 단일 진실 + 임계 상수 mappers 모듈 안 정의 회귀."""
    from assessment_engine.web.services import mappers as m
    assert m._REPORT_SIG_DANGER == "#b91c1c"
    assert m._REPORT_SIG_WARN == "#92400e"
    assert m._REPORT_SIG_NEUTRAL == "#1e293b"
    assert m._REPORT_SIG_MUTED == "#94a3b8"
    assert m._REPORT_SIG_SUBTLE == "#64748b"
    assert m._VARIANCE_BURST_RATIO == 1.5
    assert m._CAPACITY_IMMINENT_DAYS == 30
    assert m._REBOOT_UNSTABLE_COUNT == 3
    # SATURATION_BURST_RATIO 는 recommendation 상수 인용
    from assessment_engine import recommendation
    assert m._SATURATION_BURST_RATIO == recommendation.CPU_SATURATION_LOAD_RATIO
