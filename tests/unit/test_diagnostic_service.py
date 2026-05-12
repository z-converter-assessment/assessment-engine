"""diagnostic_service — input_hash·input_params 순수 함수 + submit 핵심 시나리오 (ADR 0004)."""
import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from assessment_engine.db.repositories.outbound import DiagnosticJobRecord
from assessment_engine.web.services.diagnostic_service import (
    DiagnosticService,
    _BadRequest,
    _NotFound,
    _build_input_params,
    _compute_hash,
    _normalize_anchor,
    to_panel_payload,
)


_FIXED_ANCHOR = datetime(2026, 5, 12, 0, 0, 0, tzinfo=timezone.utc)


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
    raw = datetime(2026, 5, 12, 10, 30, 45, 123456, tzinfo=timezone.utc)
    out = _normalize_anchor(raw)
    assert out.second == 0 and out.microsecond == 0
    assert out.hour == 10 and out.minute == 30


def test_normalize_anchor_naive_assumed_utc():
    naive = datetime(2026, 5, 12, 10, 30)
    out = _normalize_anchor(naive)
    assert out.tzinfo is timezone.utc


def test_normalize_anchor_none_returns_current_minute():
    out = _normalize_anchor(None)
    assert out.tzinfo is timezone.utc
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
    with pytest.raises(_NotFound):
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
    with pytest.raises(_BadRequest):
        await service.submit("server", None, "14d", _FIXED_ANCHOR)
    with pytest.raises(_BadRequest):
        await service.submit("server", [], "14d", _FIXED_ANCHOR)


def test_to_panel_payload_none_returns_none():
    assert to_panel_payload(None) is None


def test_to_panel_payload_shape_matches_render_contract():
    rec = DiagnosticJobRecord(
        id="job-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", scope="environment",
        input_params={"time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00"},
        input_hash="h",
        status="succeeded", progress_stage=None,
        result={"narrative": "x"}, error_message=None,
        created_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
        started_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
        finished_at=datetime(2026, 5, 12, 1, 0, 0, tzinfo=timezone.utc),
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
