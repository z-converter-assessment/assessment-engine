"""DiagnosticRepository 통합 테스트 — ADR 0004 partial UNIQUE·state transition·retention.

각 테스트는 db_session function-scope rollback으로 격리.
시간 의존 테스트(delete_retention)는 finished_at을 raw SQL로 과거 시점 조작.
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
) -> DiagnosticJobCreate:
    return DiagnosticJobCreate(
        scope=scope,
        input_params=params or {"time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00"},
        input_hash=input_hash,
        requested_by=requested_by,
    )


# ─── enqueue ──────────────────────────────────────────────────────────────


async def test_enqueue_new_returns_uuid(diagnostic_repo: DiagnosticRepository, db_session):
    job_id = await diagnostic_repo.enqueue(_make_create(input_hash="a" * 64))
    await db_session.commit()
    assert job_id is not None
    assert len(job_id) == 36  # UUID 문자열 (8-4-4-4-12)


async def test_enqueue_active_conflict_returns_none(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """같은 (scope, input_hash)가 pending/running 상태면 두 번째 INSERT는 do_nothing → None."""
    first = await diagnostic_repo.enqueue(_make_create(input_hash="b" * 64))
    await db_session.commit()
    second = await diagnostic_repo.enqueue(_make_create(input_hash="b" * 64))
    await db_session.commit()
    assert first is not None
    assert second is None


async def test_enqueue_after_first_succeeded_allows_new(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """첫 job을 succeeded로 옮기면 active UNIQUE 해제 — 같은 input_hash로 새 job 가능."""
    first = await diagnostic_repo.enqueue(_make_create(input_hash="c" * 64))
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(first, {"result": "ok"})
    await db_session.commit()

    second = await diagnostic_repo.enqueue(_make_create(input_hash="c" * 64))
    await db_session.commit()
    assert second is not None
    assert second != first


async def test_enqueue_different_scope_same_hash_independent(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """server scope와 environment scope는 input_hash 같아도 충돌 없음."""
    a = await diagnostic_repo.enqueue(_make_create(scope="server", input_hash="d" * 64))
    b = await diagnostic_repo.enqueue(_make_create(scope="environment", input_hash="d" * 64))
    await db_session.commit()
    assert a is not None and b is not None
    assert a != b


# ─── get_active_by_hash ──────────────────────────────────────────────────


async def test_get_active_by_hash_pending(diagnostic_repo: DiagnosticRepository, db_session):
    new_id = await diagnostic_repo.enqueue(_make_create(input_hash="e" * 64))
    await db_session.commit()
    found = await diagnostic_repo.get_active_by_hash("environment", "e" * 64, "ai_diagnostic")
    assert found == new_id


async def test_get_active_by_hash_running(diagnostic_repo: DiagnosticRepository, db_session):
    new_id = await diagnostic_repo.enqueue(_make_create(input_hash="f" * 64))
    await db_session.commit()
    await diagnostic_repo.mark_running(new_id, "extracting_stats")
    await db_session.commit()
    found = await diagnostic_repo.get_active_by_hash("environment", "f" * 64, "ai_diagnostic")
    assert found == new_id


async def test_get_active_by_hash_succeeded_returns_none(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    new_id = await diagnostic_repo.enqueue(_make_create(input_hash="g" * 64))
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(new_id, {})
    await db_session.commit()
    found = await diagnostic_repo.get_active_by_hash("environment", "g" * 64, "ai_diagnostic")
    assert found is None


async def test_get_latest_succeeded_returns_recent(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """SSR latest 진단 — 가장 최근 succeeded 반환 (시간 무관)."""
    jid = await diagnostic_repo.enqueue(_make_create(input_hash="z1" + "0" * 62))
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(jid, {"v": 1})
    await db_session.commit()
    found = await diagnostic_repo.get_latest_succeeded("environment", "z1" + "0" * 62)
    assert found is not None
    assert found.id == jid
    assert found.result == {"v": 1}


async def test_get_latest_succeeded_ignores_time_window(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """finished_at 100일 전이어도 latest로 반환 — 캐시 윈도우 무관."""
    jid = await diagnostic_repo.enqueue(_make_create(input_hash="z2" + "0" * 62))
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(jid, {})
    await db_session.commit()
    await db_session.execute(
        text("UPDATE diagnostic_jobs SET finished_at = now() - interval '100 days' WHERE id = :id"), {"id": jid}
    )
    await db_session.commit()
    found = await diagnostic_repo.get_latest_succeeded("environment", "z2" + "0" * 62)
    assert found is not None
    assert found.id == jid


async def test_get_latest_succeeded_excludes_pending_failed(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """pending·running·failed는 latest 대상 아님."""
    h = "z3" + "0" * 62
    pending_id = await diagnostic_repo.enqueue(_make_create(input_hash=h))
    await db_session.commit()
    # pending 상태만 있는 시점 → None
    assert await diagnostic_repo.get_latest_succeeded("environment", h) is None

    await diagnostic_repo.mark_failed(pending_id, "x")
    await db_session.commit()
    # failed 상태도 None
    assert await diagnostic_repo.get_latest_succeeded("environment", h) is None


async def test_get_latest_by_context_environment(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """environment context — input_hash 무관 (anchor 다른) succeeded 두 건에서 가장 최근."""
    p_old = {"time_range": "14d", "anchor_at": "2026-05-10T00:00:00+00:00"}
    p_new = {"time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00"}
    h_old = "ctx_env_old" + "0" * (64 - 11)
    h_new = "ctx_env_new" + "0" * (64 - 11)

    j_old = await diagnostic_repo.enqueue(_make_create(input_hash=h_old, params=p_old))
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(j_old, {"v": "old"})
    await db_session.commit()
    await db_session.execute(
        text("UPDATE diagnostic_jobs SET finished_at = now() - interval '1 day' WHERE id = :id"), {"id": j_old}
    )
    await db_session.commit()

    j_new = await diagnostic_repo.enqueue(_make_create(input_hash=h_new, params=p_new))
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(j_new, {"v": "new"})
    await db_session.commit()

    found = await diagnostic_repo.get_latest_by_context("environment", "14d", None)
    assert found is not None
    assert found.id == j_new


async def test_get_latest_by_context_server_filters_by_public_id(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """server context — 같은 time_range·다른 server_public_id 결과를 구별."""
    p_a = {"time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00", "server_public_id": "srv-a"}
    p_b = {"time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00", "server_public_id": "srv-b"}
    h_a = "ctx_srv_a" + "0" * (64 - 9)
    h_b = "ctx_srv_b" + "0" * (64 - 9)

    j_a = await diagnostic_repo.enqueue(_make_create(scope="server", input_hash=h_a, params=p_a))
    j_b = await diagnostic_repo.enqueue(_make_create(scope="server", input_hash=h_b, params=p_b))
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(j_a, {})
    await diagnostic_repo.mark_succeeded(j_b, {})
    await db_session.commit()

    found_a = await diagnostic_repo.get_latest_by_context("server", "14d", "srv-a")
    found_b = await diagnostic_repo.get_latest_by_context("server", "14d", "srv-b")
    assert found_a.id == j_a
    assert found_b.id == j_b


async def test_get_latest_by_context_excludes_non_succeeded(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    p = {"time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00"}
    h = "ctx_pending" + "0" * (64 - 11)
    jid = await diagnostic_repo.enqueue(_make_create(input_hash=h, params=p))
    await db_session.commit()
    # pending 상태
    assert await diagnostic_repo.get_latest_by_context("environment", "14d", None) is None
    await diagnostic_repo.mark_failed(jid, "x")
    await db_session.commit()
    # failed 상태
    assert await diagnostic_repo.get_latest_by_context("environment", "14d", None) is None


async def test_get_latest_by_context_time_range_filter(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """다른 time_range는 매칭 안 됨."""
    p_14 = {"time_range": "14d", "anchor_at": "2026-05-12T00:00:00+00:00"}
    p_7 = {"time_range": "7d", "anchor_at": "2026-05-12T00:00:00+00:00"}
    h14 = "ctx_14" + "0" * (64 - 6)
    h7 = "ctx_7d" + "0" * (64 - 6)

    j14 = await diagnostic_repo.enqueue(_make_create(input_hash=h14, params=p_14))
    j7 = await diagnostic_repo.enqueue(_make_create(input_hash=h7, params=p_7))
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(j14, {})
    await diagnostic_repo.mark_succeeded(j7, {})
    await db_session.commit()

    found = await diagnostic_repo.get_latest_by_context("environment", "14d", None)
    assert found.id == j14
    found2 = await diagnostic_repo.get_latest_by_context("environment", "7d", None)
    assert found2.id == j7


async def test_get_latest_succeeded_picks_most_recent(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """동일 input_hash 2건 succeeded → finished_at 큰 쪽 선택."""
    h = "z4" + "0" * 62
    j1 = await diagnostic_repo.enqueue(_make_create(input_hash=h))
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(j1, {"v": "old"})
    await db_session.commit()
    await db_session.execute(
        text("UPDATE diagnostic_jobs SET finished_at = now() - interval '7 days' WHERE id = :id"), {"id": j1}
    )
    await db_session.commit()

    j2 = await diagnostic_repo.enqueue(_make_create(input_hash=h))
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(j2, {"v": "new"})
    await db_session.commit()

    found = await diagnostic_repo.get_latest_succeeded("environment", h)
    assert found.id == j2
    assert found.result == {"v": "new"}


# ─── get_by_id / get_many_by_ids ─────────────────────────────────────────


async def test_get_by_id_returns_full_record(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    jid = await diagnostic_repo.enqueue(
        _make_create(
            input_hash="l" * 64,
            params={"server_public_id": "abc", "period_days": 7},
            requested_by="user-x",
        )
    )
    await db_session.commit()
    rec = await diagnostic_repo.get_by_id(jid)
    assert rec is not None
    assert rec.id == jid
    assert rec.scope == "environment"
    assert rec.input_params == {"server_public_id": "abc", "period_days": 7}
    assert rec.status == "pending"
    assert rec.requested_by == "user-x"


async def test_get_by_id_missing_returns_none(
    diagnostic_repo: DiagnosticRepository,
):
    rec = await diagnostic_repo.get_by_id("00000000-0000-0000-0000-000000000000")
    assert rec is None


async def test_get_many_by_ids_batch(diagnostic_repo: DiagnosticRepository, db_session):
    ids = []
    for i in range(3):
        jid = await diagnostic_repo.enqueue(_make_create(input_hash=chr(ord("m") + i) * 64))
        ids.append(jid)
    await db_session.commit()
    recs = await diagnostic_repo.get_many_by_ids(ids)
    assert {r.id for r in recs} == set(ids)


async def test_get_many_by_ids_empty(diagnostic_repo: DiagnosticRepository):
    assert await diagnostic_repo.get_many_by_ids([]) == []


# ─── 상태 전이 ──────────────────────────────────────────────────────────


async def test_mark_running_sets_started_at(diagnostic_repo: DiagnosticRepository, db_session):
    jid = await diagnostic_repo.enqueue(_make_create(input_hash="p" * 64))
    await db_session.commit()
    await diagnostic_repo.mark_running(jid, "extracting_stats")
    await db_session.commit()
    rec = await diagnostic_repo.get_by_id(jid)
    assert rec.status == "running"
    assert rec.started_at is not None
    assert rec.progress_stage == "extracting_stats"


async def test_mark_succeeded_clears_progress_sets_result(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    jid = await diagnostic_repo.enqueue(_make_create(input_hash="q" * 64))
    await db_session.commit()
    await diagnostic_repo.mark_running(jid, "extracting_stats")
    await db_session.commit()
    await diagnostic_repo.mark_succeeded(jid, {"narrative": "ok", "k": 1})
    await db_session.commit()
    rec = await diagnostic_repo.get_by_id(jid)
    assert rec.status == "succeeded"
    assert rec.progress_stage is None
    assert rec.finished_at is not None
    assert rec.result == {"narrative": "ok", "k": 1}


async def test_mark_failed_stores_error_message(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    jid = await diagnostic_repo.enqueue(_make_create(input_hash="r" * 64))
    await db_session.commit()
    await diagnostic_repo.mark_failed(jid, "LLM timeout after 60s")
    await db_session.commit()
    rec = await diagnostic_repo.get_by_id(jid)
    assert rec.status == "failed"
    assert rec.error_message == "LLM timeout after 60s"
    assert rec.finished_at is not None


async def test_update_progress_stage(diagnostic_repo: DiagnosticRepository, db_session):
    jid = await diagnostic_repo.enqueue(_make_create(input_hash="s" * 64))
    await db_session.commit()
    await diagnostic_repo.mark_running(jid, "extracting_stats")
    await db_session.commit()
    await diagnostic_repo.update_progress_stage(jid, "generating_narrative")
    await db_session.commit()
    rec = await diagnostic_repo.get_by_id(jid)
    assert rec.progress_stage == "generating_narrative"
    assert rec.status == "running"  # 변경 안 됨


# ─── delete_retention ────────────────────────────────────────────────────


async def test_delete_retention_purges_old_finished(
    diagnostic_repo: DiagnosticRepository,
    db_session,
):
    """finished_at이 90일 초과인 job 삭제."""
    jid = await diagnostic_repo.enqueue(_make_create(input_hash="t" * 64))
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
    """finished_at IS NULL (pending/running)은 retention 영향 안 받음."""
    jid = await diagnostic_repo.enqueue(_make_create(input_hash="v" * 64))
    await db_session.commit()
    # finished_at NULL인 상태로 retention 호출
    deleted = await diagnostic_repo.delete_retention(90)
    await db_session.commit()
    assert deleted == 0
    rec = await diagnostic_repo.get_by_id(jid)
    assert rec is not None
    assert rec.status == "pending"
