"""DiagnosticRepository 통합 테스트 — ADR 0004 partial UNIQUE·즉시 succeeded·retention.

각 테스트는 db_session function-scope rollback으로 격리.
시간 의존 테스트(delete_retention)는 finished_at을 raw SQL로 과거 시점 조작.
보고서 발행은 enqueue → 즉시 mark_succeeded (running/failed 상태 전이 없음, #C1).
"""

import pytest
from sqlalchemy import text

from assessment_engine.db.dtos.inbound import DiagnosticJobCreate
from assessment_engine.db.repositories.diagnostic_repository import DiagnosticRepository

pytestmark = pytest.mark.asyncio


def _make_create(
    scope: str = "environment",
    input_hash: str = "h" * 64,
    params: dict | None = None,
    requested_by: str | None = None,
    job_type: str = "customer_report",
) -> DiagnosticJobCreate:
    return DiagnosticJobCreate(
        scope=scope,
        input_params=params or {"time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00"},
        input_hash=input_hash,
        job_type=job_type,
        requested_by=requested_by,
    )


# ─── enqueue (active partial UNIQUE = scope·input_hash·job_type) ───────────


async def test_enqueue_new_returns_uuid(diagnostic_repo: DiagnosticRepository, db_session):
    job_id = await diagnostic_repo.enqueue(_make_create(input_hash="a" * 64))
    assert job_id is not None
    await db_session.commit()
    assert job_id is not None
    assert len(job_id) == 36  # UUID 문자열 (8-4-4-4-12)


async def test_enqueue_active_conflict_returns_none(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """같은 (scope, input_hash, job_type)가 active 상태면 두 번째 INSERT는 do_nothing → None."""
    first = await diagnostic_repo.enqueue(_make_create(input_hash="b" * 64))
    assert first is not None
    await db_session.commit()
    second = await diagnostic_repo.enqueue(_make_create(input_hash="b" * 64))
    await db_session.commit()
    assert second is None


async def test_enqueue_after_first_succeeded_allows_new(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """첫 job을 succeeded로 옮기면 active UNIQUE 해제 — 같은 input_hash로 새 job 가능."""
    first = await diagnostic_repo.enqueue(_make_create(input_hash="c" * 64))
    assert first is not None
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(first, {"result": "ok"})
    await db_session.commit()

    second = await diagnostic_repo.enqueue(_make_create(input_hash="c" * 64))
    assert second is not None
    await db_session.commit()
    assert second is not None
    assert second != first


async def test_enqueue_different_scope_same_hash_independent(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """server scope와 environment scope는 input_hash 같아도 충돌 없음."""
    a = await diagnostic_repo.enqueue(_make_create(scope="server", input_hash="d" * 64))
    assert a is not None
    b = await diagnostic_repo.enqueue(_make_create(scope="environment", input_hash="d" * 64))
    assert b is not None
    await db_session.commit()
    assert a is not None and b is not None
    assert a != b


async def test_enqueue_different_job_type_same_hash_independent(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """customer_report와 engineer_report는 (scope, input_hash) 같아도 job_type 달라 충돌 없음."""
    c = await diagnostic_repo.enqueue(_make_create(input_hash="j" * 64, job_type="customer_report"))
    assert c is not None
    e = await diagnostic_repo.enqueue(_make_create(input_hash="j" * 64, job_type="engineer_report"))
    assert e is not None
    await db_session.commit()
    assert c is not None and e is not None
    assert c != e


# ─── get_active_by_hash ──────────────────────────────────────────────────


async def test_get_active_by_hash_active(diagnostic_repo: DiagnosticRepository, db_session):
    new_id = await diagnostic_repo.enqueue(_make_create(input_hash="e" * 64))
    assert new_id is not None
    await db_session.commit()
    found = await diagnostic_repo.get_active_by_hash("environment", "e" * 64, "customer_report")
    assert found == new_id


async def test_get_active_by_hash_succeeded_returns_none(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    new_id = await diagnostic_repo.enqueue(_make_create(input_hash="g" * 64))
    assert new_id is not None
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(new_id, {})
    await db_session.commit()
    found = await diagnostic_repo.get_active_by_hash("environment", "g" * 64, "customer_report")
    assert found is None


async def test_get_active_by_hash_job_type_scoped(diagnostic_repo: DiagnosticRepository, db_session):
    """active 회수는 job_type 일치만 — 다른 job_type 으로 조회하면 None."""
    new_id = await diagnostic_repo.enqueue(_make_create(input_hash="k" * 64, job_type="customer_report"))
    assert new_id is not None
    await db_session.commit()
    assert await diagnostic_repo.get_active_by_hash("environment", "k" * 64, "customer_report") == new_id
    assert await diagnostic_repo.get_active_by_hash("environment", "k" * 64, "engineer_report") is None


# ─── get_by_id ───────────────────────────────────────────────────────────


async def test_get_by_id_returns_full_record(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    jid = await diagnostic_repo.enqueue(
        _make_create(
            input_hash="l" * 64,
            params={"server_public_id": "abc", "time_range": "7d"},
            requested_by="user-x",
        )
    )
    assert jid is not None
    await db_session.commit()
    rec = await diagnostic_repo.get_by_id(jid)
    assert rec is not None
    assert rec.id == jid
    assert rec.scope == "environment"
    assert rec.input_params == {"server_public_id": "abc", "time_range": "7d"}
    assert rec.status == "pending"
    assert rec.requested_by == "user-x"


async def test_get_by_id_missing_returns_none(
    diagnostic_repo: DiagnosticRepository,
):
    rec = await diagnostic_repo.get_by_id("00000000-0000-0000-0000-000000000000")
    assert rec is None


# ─── mark_succeeded (발행 즉시 succeeded — running 경유 없음) ───────────────


async def test_mark_succeeded_sets_result_and_finished(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    jid = await diagnostic_repo.enqueue(_make_create(input_hash="q" * 64))
    assert jid is not None
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(jid, {"kind": "env_report", "k": 1})
    await db_session.commit()
    rec = await diagnostic_repo.get_by_id(jid)
    assert rec is not None
    assert rec.status == "succeeded"
    assert rec.finished_at is not None
    assert rec.progress_stage is None
    assert rec.result == {"kind": "env_report", "k": 1}


# ─── delete_retention ────────────────────────────────────────────────────


async def test_delete_retention_purges_old_finished(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """finished_at이 90일 초과인 job 삭제."""
    jid = await diagnostic_repo.enqueue(_make_create(input_hash="t" * 64))
    assert jid is not None
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(jid, {})
    await db_session.commit()
    # finished_at을 100일 전으로 조작
    await db_session.execute(
        text("UPDATE diagnostic_jobs SET finished_at = now() - interval '100 days' WHERE id = :id"), {"id": jid}
    )
    await db_session.commit()

    deleted = await diagnostic_repo.delete_retention(90)
    await db_session.commit()
    assert deleted == 1
    assert await diagnostic_repo.get_by_id(jid) is None


async def test_delete_retention_keeps_recent(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """방금 succeeded한 job은 삭제 대상 아님."""
    jid = await diagnostic_repo.enqueue(_make_create(input_hash="u" * 64))
    assert jid is not None
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(jid, {})
    await db_session.commit()

    deleted = await diagnostic_repo.delete_retention(90)
    await db_session.commit()
    assert deleted == 0
    assert await diagnostic_repo.get_by_id(jid) is not None


async def test_delete_retention_ignores_active_jobs(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """finished_at IS NULL (pending)은 retention 영향 안 받음."""
    jid = await diagnostic_repo.enqueue(_make_create(input_hash="v" * 64))
    assert jid is not None
    await db_session.commit()
    # finished_at NULL인 상태로 retention 호출
    deleted = await diagnostic_repo.delete_retention(90)
    await db_session.commit()
    assert deleted == 0
    rec = await diagnostic_repo.get_by_id(jid)
    assert rec is not None
    assert rec.status == "pending"
