import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from assessment_engine.web.services.diagnostic_service import DiagnosticService
from assessment_engine.web.services.report import (
    REPORT_KIND_ENV,
    build_report_result,
    compute_hash,
    normalize_anchor,
)

if TYPE_CHECKING:
    from assessment_engine.db.dtos.inbound import DiagnosticJobCreate

_FIXED_ANCHOR = datetime(2026, 5, 12, 0, 0, 0, tzinfo=UTC)


def test_normalize_anchor_truncates_seconds_microseconds():
    raw = datetime(2026, 5, 12, 10, 30, 45, 123456, tzinfo=UTC)
    out = normalize_anchor(raw)
    assert out.second == 0
    assert out.microsecond == 0
    assert out.hour == 10
    assert out.minute == 30


def test_normalize_anchor_naive_assumed_utc():
    naive = datetime(2026, 5, 12, 10, 30)  # noqa: DTZ001
    out = normalize_anchor(naive)
    assert out.tzinfo is UTC


def test_normalize_anchor_none_returns_current_minute():
    out = normalize_anchor(None)
    assert out.tzinfo is UTC
    assert out.second == 0
    assert out.microsecond == 0


def test_compute_hash_deterministic():
    p = {"server_public_id": "x", "time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00"}
    assert compute_hash("server", p) == compute_hash("server", p)
    assert len(compute_hash("server", p)) == 64


def test_compute_hash_key_order_independent():
    a = compute_hash("server", {"server_public_id": "x", "time_range": "14d", "anchor_at": "T"})
    b = compute_hash("server", {"anchor_at": "T", "server_public_id": "x", "time_range": "14d"})
    assert a == b


def test_compute_hash_scope_separates_namespace():
    p = {"time_range": "14d", "anchor_at": "T"}
    assert compute_hash("server", p) != compute_hash("environment", p)


def test_compute_hash_matches_explicit_sha256():
    scope = "server"
    params = {"server_public_id": "abc", "time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00"}
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    expected = hashlib.sha256(f"{scope}|{canonical}".encode()).hexdigest()
    assert compute_hash(scope, params) == expected


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


@pytest.fixture
def stub_session_factory() -> MagicMock:
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=session)


@pytest.fixture
def stub_diag_repo() -> AsyncMock:
    return AsyncMock()


def _service(session_factory: MagicMock, diag_repo: AsyncMock) -> DiagnosticService:
    return DiagnosticService(
        session_factory=session_factory,
        diagnostic_repo_factory=lambda s: diag_repo,
    )


@pytest.mark.parametrize("view", ["customer", "engineer"])
async def test_emit_report_marks_succeeded(
    view: str,
    stub_session_factory: MagicMock,
    stub_diag_repo: AsyncMock,
):
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
    create_arg = stub_diag_repo.enqueue.call_args[0][0]
    assert create_arg.job_type == f"{view}_report"
    assert create_arg.input_params["anchor_at"] == "2026-05-12T00:00:00+00:00"
    assert create_arg.input_params["server_public_ids"] == ["a", "b"]


async def test_emit_report_server_single_adds_singular_key(
    stub_session_factory: MagicMock,
    stub_diag_repo: AsyncMock,
):
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


async def test_emit_report_child_jobs_stored_in_result(
    stub_session_factory: MagicMock,
    stub_diag_repo: AsyncMock,
):
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


async def test_emit_report_active_conflict_returns_existing(
    stub_session_factory: MagicMock,
    stub_diag_repo: AsyncMock,
):
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


async def test_enqueue_report_pending_no_mark_succeeded(
    stub_session_factory: MagicMock,
    stub_diag_repo: AsyncMock,
):
    stub_diag_repo.enqueue = AsyncMock(return_value="parent-1")
    stub_diag_repo.mark_succeeded = AsyncMock()
    session = stub_session_factory.return_value
    service = _service(stub_session_factory, stub_diag_repo)

    job_id = await service.enqueue_report(
        view="engineer",
        scope="server",
        server_public_ids=["b", "a"],
        time_range="7d",
        anchor_at=_FIXED_ANCHOR,
    )
    assert job_id == "parent-1"
    stub_diag_repo.mark_succeeded.assert_not_called()
    session.commit.assert_awaited_once()
    create_arg = stub_diag_repo.enqueue.call_args[0][0]
    assert create_arg.input_params["server_public_ids"] == ["a", "b"]


async def test_enqueue_report_active_conflict_returns_existing(
    stub_session_factory: MagicMock,
    stub_diag_repo: AsyncMock,
):
    stub_diag_repo.enqueue = AsyncMock(return_value=None)
    stub_diag_repo.get_active_by_hash = AsyncMock(return_value="active-parent")
    session = stub_session_factory.return_value
    service = _service(stub_session_factory, stub_diag_repo)

    job_id = await service.enqueue_report(
        view="customer",
        scope="environment",
        server_public_ids=[],
        time_range="7d",
        anchor_at=_FIXED_ANCHOR,
    )
    assert job_id == "active-parent"
    session.rollback.assert_awaited_once()
    session.commit.assert_not_called()


async def test_enqueue_and_emit_same_input_hash(
    stub_session_factory: MagicMock,
    stub_diag_repo: AsyncMock,
):
    captured: list[DiagnosticJobCreate] = []

    def _capture(create: DiagnosticJobCreate) -> str:
        captured.append(create)
        return "j"

    stub_diag_repo.enqueue = AsyncMock(side_effect=_capture)
    stub_diag_repo.mark_succeeded = AsyncMock()
    service = _service(stub_session_factory, stub_diag_repo)
    common: dict[str, Any] = {
        "view": "engineer",
        "scope": "server",
        "server_public_ids": ["a"],
        "time_range": "7d",
        "anchor_at": _FIXED_ANCHOR,
    }
    await service.enqueue_report(**common)
    await service.emit_report(kind=REPORT_KIND_ENV, snapshot={}, **common)
    assert captured[0].input_hash == captured[1].input_hash


async def test_claim_pending_commits_and_returns(
    stub_session_factory: MagicMock,
    stub_diag_repo: AsyncMock,
):
    rec = MagicMock()
    stub_diag_repo.claim_next_pending = AsyncMock(return_value=rec)
    session = stub_session_factory.return_value
    service = _service(stub_session_factory, stub_diag_repo)
    out = await service.claim_pending()
    assert out is rec
    session.commit.assert_awaited_once()


async def test_recover_stale_delegates(
    stub_session_factory: MagicMock,
    stub_diag_repo: AsyncMock,
):
    stub_diag_repo.recover_stale_running = AsyncMock(return_value=3)
    session = stub_session_factory.return_value
    service = _service(stub_session_factory, stub_diag_repo)
    n = await service.recover_stale(600)
    assert n == 3
    stub_diag_repo.recover_stale_running.assert_awaited_once_with(600)
    session.commit.assert_awaited_once()


async def test_finish_failed_delegates(
    stub_session_factory: MagicMock,
    stub_diag_repo: AsyncMock,
):
    stub_diag_repo.mark_failed = AsyncMock()
    service = _service(stub_session_factory, stub_diag_repo)
    await service.finish_failed("job-x", "internal error")
    stub_diag_repo.mark_failed.assert_awaited_once_with("job-x", "internal error")


def test_report_threshold_constants_single_definition():
    from assessment_engine.web.services.mappers import constants

    assert constants._VARIANCE_BURST_RATIO == 1.5
    assert constants._REBOOT_UNSTABLE_COUNT == 3
