"""Task 조회·UPDATE 통합 테스트 (ADR 0007).

검증:
- collect_repo.complete_task — result 컬럼 UPDATE
  (status·completed_at·failure_reason·exit_code·signal_no·duration_ms·stdout_tail·stderr_tail)
- collect_repo.expire_all_overdue_tasks — deadline 경과 pending 전역 failure(timeout) 전이 (reaper)
- query_repo.get_task_by_public_id — 단일 + JOIN server_inventory (target_public_id·target_hostname)
- query_repo.list_recent_tasks — created_at 역순 + cursor pagination (E2)
- query_repo.latest_tasks_by_servers — DISTINCT ON (target_server_id) 서버별 최신 1건

부분 UNIQUE `uq_tasks_pending_per_server_type` 동작은 별도 — 본 테스트는 조회 메서드만.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import Row, text

from assessment_engine.db.dtos.inbound import TaskCreate
from tests.factories import make_inventory, make_task_result_update

if TYPE_CHECKING:
    from assessment_engine.db.repositories.collect_sql import SqlCollectRepository
    from assessment_engine.db.repositories.query.repository_sql import SqlQueryRepository

pytestmark = pytest.mark.asyncio


# 발행 대상 식별자 = agent_id (UUID). tasks.target_agent_id 는 UUID 컬럼이라 유효 UUID 형식 필수.
_AGENT_A = "00000000-0000-4000-8000-0000000000b1"
_AGENT_B = "00000000-0000-4000-8000-0000000000b2"


async def _setup_server(
    collect_repo: SqlCollectRepository,
    agent_id: str = _AGENT_A,
    hostname: str = "test-task-host-01",
) -> int:
    inv = make_inventory(agent_id=agent_id, hostname=hostname)
    return await collect_repo.upsert_server(inv)


async def _insert_task(
    collect_repo: SqlCollectRepository,
    server_id: int,
    agent_id: str,
    task_type: str = "zconverter_install",
) -> str:
    return await collect_repo.create_task(
        TaskCreate(
            target_server_id=server_id,
            target_agent_id=agent_id,
            task_type=task_type,
            params=None,
        )
    )


# --- complete_task --------------------------------------------------------


async def test_complete_task_success(collect_repo: SqlCollectRepository) -> None:
    sid = await _setup_server(collect_repo)
    pid = await _insert_task(collect_repo, sid, _AGENT_A)

    update = make_task_result_update(
        public_id=pid,
        status="success",
        exit_code=0,
        duration_ms=42,
        stdout_tail="ok",
    )
    updated = await collect_repo.complete_task(update)
    assert updated is True

    # 6 컬럼 모두 UPDATE 확인.
    row = (
        await collect_repo.session.execute(
            text(
                "SELECT status, exit_code, duration_ms, stdout_tail, stderr_tail, "
                "completed_at, failure_reason FROM tasks WHERE public_id=:pid"
            ),
            {"pid": pid},
        )
    ).first()
    assert row is not None
    assert row.status == "success"
    assert row.exit_code == 0
    assert row.duration_ms == 42
    assert row.stdout_tail == "ok"
    assert row.failure_reason is None
    assert row.completed_at is not None


async def test_complete_task_failure_with_reason(collect_repo: SqlCollectRepository) -> None:
    sid = await _setup_server(collect_repo)
    pid = await _insert_task(collect_repo, sid, _AGENT_A)

    update = make_task_result_update(
        public_id=pid,
        status="failure",
        failure_reason="sha256_mismatch",
        exit_code=None,
        duration_ms=8000,
        stdout_tail="",
        stderr_tail="hash mismatch",
    )
    await collect_repo.complete_task(update)

    row = (
        await collect_repo.session.execute(
            text("SELECT status, failure_reason, exit_code, stderr_tail FROM tasks WHERE public_id=:pid"),
            {"pid": pid},
        )
    ).first()
    assert row is not None
    assert row.status == "failure"
    assert row.failure_reason == "sha256_mismatch"
    assert row.exit_code is None
    assert row.stderr_tail == "hash mismatch"


async def test_complete_task_stores_signal_no(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
) -> None:
    """시그널 사망 결과 — exit_code null + signal_no 저장. query 경로도 signal_no 노출."""
    sid = await _setup_server(collect_repo)
    pid = await _insert_task(collect_repo, sid, _AGENT_A)

    update = make_task_result_update(
        public_id=pid, status="failure", failure_reason="script_failed", exit_code=None, signal_no=9
    )
    assert await collect_repo.complete_task(update) is True

    row = await query_repo.get_task_by_public_id(pid)
    assert row is not None
    assert row.exit_code is None
    assert row.signal_no == 9


async def test_complete_task_stores_task_policy(collect_repo: SqlCollectRepository) -> None:
    """task_policy 실값(True/False) 영속 — 판정 1순위 신호 raw 보존(감사)."""
    sid = await _setup_server(collect_repo)
    for policy in (True, False):
        pid = await _insert_task(collect_repo, sid, _AGENT_A)
        update = make_task_result_update(public_id=pid, task_policy=policy)
        assert await collect_repo.complete_task(update) is True
        row = (
            await collect_repo.session.execute(text("SELECT task_policy FROM tasks WHERE public_id = :p"), {"p": pid})
        ).scalar_one()
        assert row is policy


async def test_complete_task_unknown_public_id_returns_false(collect_repo: SqlCollectRepository) -> None:
    update = make_task_result_update(public_id="00000000-0000-4000-8000-000000000000")
    updated = await collect_repo.complete_task(update)
    assert updated is False


# --- expire_all_overdue_tasks (reaper) ------------------------------------


async def test_expire_all_overdue_tasks_transitions_overdue_only(collect_repo: SqlCollectRepository) -> None:
    """deadline 경과 pending 만 failure(timeout) 전이. 미경과·미래 deadline 은 pending 유지."""
    sid = await _setup_server(collect_repo)
    past = datetime.now(UTC) - timedelta(hours=1)
    future = datetime.now(UTC) + timedelta(hours=1)
    overdue = await collect_repo.create_task(
        TaskCreate(target_server_id=sid, target_agent_id=_AGENT_A, task_type="t-overdue", params=None, deadline_at=past)
    )
    fresh = await collect_repo.create_task(
        TaskCreate(target_server_id=sid, target_agent_id=_AGENT_A, task_type="t-fresh", params=None, deadline_at=future)
    )

    n = await collect_repo.expire_all_overdue_tasks()
    assert n == 1

    async def _row(pid: str) -> Row[Any]:
        row = (
            await collect_repo.session.execute(
                text("SELECT status, failure_reason FROM tasks WHERE public_id=:pid"),
                {"pid": pid},
            )
        ).first()
        assert row is not None
        return row

    overdue_row = await _row(overdue)
    assert overdue_row.status == "failure"
    assert overdue_row.failure_reason == "timeout"
    assert (await _row(fresh)).status == "pending"


# --- get_task_by_public_id ------------------------------------------------


async def test_get_task_by_public_id_joins_server(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
) -> None:
    sid = await _setup_server(collect_repo)
    pid = await _insert_task(collect_repo, sid, _AGENT_A)
    await collect_repo.complete_task(
        make_task_result_update(
            public_id=pid,
            status="success",
            exit_code=0,
            duration_ms=29,
        )
    )

    row = await query_repo.get_task_by_public_id(pid)
    assert row is not None
    assert row.public_id == pid
    assert row.target_server_id == sid
    assert row.target_hostname == "test-task-host-01"
    assert row.status == "success"
    assert row.exit_code == 0
    assert row.duration_ms == 29


async def test_get_task_by_public_id_not_found(query_repo: SqlQueryRepository) -> None:
    row = await query_repo.get_task_by_public_id("00000000-0000-4000-8000-000000000099")
    assert row is None


# --- list_recent_tasks ----------------------------------------------------


async def test_list_recent_tasks_orders_by_created_desc(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
) -> None:
    sid = await _setup_server(collect_repo)
    pids = [await _insert_task(collect_repo, sid, _AGENT_A, task_type=f"t-{i}") for i in range(3)]
    await collect_repo.session.flush()

    rows = await query_repo.list_recent_tasks(sid, limit=10, cursor=None)
    assert len(rows) == 3
    # 가장 마지막 INSERT 가 가장 최신.
    assert [r.public_id for r in rows] == list(reversed(pids))


async def test_list_recent_tasks_cursor_pagination(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
) -> None:
    sid = await _setup_server(collect_repo)
    base = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    # raw SQL — created_at 명시 INSERT (ORM server_default(now()) 우회).
    # 같은 transaction 내 함수 호출 간 microsecond 동일 가능성 회피.
    for i in range(5):
        await collect_repo.session.execute(
            text("""
            INSERT INTO tasks (target_server_id, target_agent_id, task_type, status, created_at)
            VALUES (:sid, :aid, :tt, 'success', :ts)
        """),
            {"sid": sid, "aid": _AGENT_A, "tt": f"cursor-t-{i}", "ts": base + timedelta(seconds=i)},
        )
    await collect_repo.session.flush()

    first = await query_repo.list_recent_tasks(sid, limit=2, cursor=None)
    assert len(first) == 2
    cursor = first[-1].created_at
    second = await query_repo.list_recent_tasks(sid, limit=10, cursor=cursor)
    # cursor 시점 row 는 제외, 이후 3건 (5 - 2 = 3).
    assert len(second) == 3
    assert all(r.created_at < cursor for r in second)


# --- latest_tasks_by_servers ----------------------------------------------


async def test_latest_tasks_by_servers_distinct_on(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
) -> None:
    s1 = await _setup_server(collect_repo, agent_id=_AGENT_A, hostname="test-task-host-A")
    s2 = await _setup_server(collect_repo, agent_id=_AGENT_B, hostname="test-task-host-B")

    await _insert_task(collect_repo, s1, _AGENT_A, task_type="t-old")
    p1_new = await _insert_task(collect_repo, s1, _AGENT_A, task_type="t-new")
    p2_only = await _insert_task(collect_repo, s2, _AGENT_B, task_type="t-only")
    await collect_repo.session.flush()

    latest = await query_repo.latest_tasks_by_servers([s1, s2])
    assert latest[s1].public_id == p1_new  # s1 의 가장 최근 1건만
    assert latest[s2].public_id == p2_only


async def test_latest_tasks_by_servers_empty_input(query_repo: SqlQueryRepository) -> None:
    assert await query_repo.latest_tasks_by_servers([]) == {}


# --- expire_overdue_tasks (scoped) ----------------------------------------

_AGENT_C = "00000000-0000-4000-8000-0000000000c3"


async def test_expire_overdue_tasks_scopes_to_server_ids(collect_repo: SqlCollectRepository) -> None:
    """server_ids 스코프 만료 — 목록에 든 서버만 failure(timeout) 전이, 목록 밖 서버는 pending 유지(격리)."""
    s1 = await _setup_server(collect_repo, agent_id=_AGENT_A, hostname="test-task-host-A")
    s2 = await _setup_server(collect_repo, agent_id=_AGENT_B, hostname="test-task-host-B")
    past = datetime.now(UTC) - timedelta(hours=1)
    in_scope = await collect_repo.create_task(
        TaskCreate(target_server_id=s1, target_agent_id=_AGENT_A, task_type="t-in", params=None, deadline_at=past)
    )
    out_scope = await collect_repo.create_task(
        TaskCreate(target_server_id=s2, target_agent_id=_AGENT_B, task_type="t-out", params=None, deadline_at=past)
    )

    n = await collect_repo.expire_overdue_tasks([s1])
    assert n == 1

    async def _row(pid: str) -> Row[Any]:
        row = (
            await collect_repo.session.execute(
                text("SELECT status, failure_reason FROM tasks WHERE public_id=:pid"),
                {"pid": pid},
            )
        ).first()
        assert row is not None
        return row

    in_row = await _row(in_scope)
    assert in_row.status == "failure"
    assert in_row.failure_reason == "timeout"
    # 목록 밖 서버의 경과 pending 은 격리되어 pending 유지.
    assert (await _row(out_scope)).status == "pending"


async def test_expire_overdue_tasks_empty_list_returns_zero(collect_repo: SqlCollectRepository) -> None:
    """빈 리스트 -> 0 즉시 반환(early return). 경과 pending 이 있어도 건드리지 않음."""
    sid = await _setup_server(collect_repo)
    past = datetime.now(UTC) - timedelta(hours=1)
    pid = await collect_repo.create_task(
        TaskCreate(
            target_server_id=sid, target_agent_id=_AGENT_A, task_type="t-untouched", params=None, deadline_at=past
        )
    )

    assert await collect_repo.expire_overdue_tasks([]) == 0

    status = (
        await collect_repo.session.execute(text("SELECT status FROM tasks WHERE public_id=:pid"), {"pid": pid})
    ).scalar_one()
    assert status == "pending"


async def test_expire_overdue_tasks_ignores_fresh_deadline(collect_repo: SqlCollectRepository) -> None:
    """스코프 안이어도 미래 deadline 은 만료 안 함."""
    sid = await _setup_server(collect_repo)
    future = datetime.now(UTC) + timedelta(hours=1)
    pid = await collect_repo.create_task(
        TaskCreate(target_server_id=sid, target_agent_id=_AGENT_A, task_type="t-fresh", params=None, deadline_at=future)
    )

    assert await collect_repo.expire_overdue_tasks([sid]) == 0
    status = (
        await collect_repo.session.execute(text("SELECT status FROM tasks WHERE public_id=:pid"), {"pid": pid})
    ).scalar_one()
    assert status == "pending"


# --- find_pending_deadline_servers ----------------------------------------


async def test_find_pending_deadline_servers_only_pending_with_deadline(
    collect_repo: SqlCollectRepository,
) -> None:
    """pending + deadline_at NOT NULL 서버만 반환. 비-pending·deadline NULL 은 제외."""
    s1 = await _setup_server(collect_repo, agent_id=_AGENT_A, hostname="test-task-host-A")
    s2 = await _setup_server(collect_repo, agent_id=_AGENT_B, hostname="test-task-host-B")
    s3 = await _setup_server(collect_repo, agent_id=_AGENT_C, hostname="test-task-host-C")
    future = datetime.now(UTC) + timedelta(hours=1)

    # s1: pending + deadline -> 포함.
    await collect_repo.create_task(
        TaskCreate(target_server_id=s1, target_agent_id=_AGENT_A, task_type="t1", params=None, deadline_at=future)
    )
    # s2: pending 이지만 deadline NULL -> 제외.
    await collect_repo.create_task(
        TaskCreate(target_server_id=s2, target_agent_id=_AGENT_B, task_type="t2", params=None)
    )
    # s3: deadline 있으나 complete_task 로 success 전이 -> 비-pending 제외.
    p3 = await collect_repo.create_task(
        TaskCreate(target_server_id=s3, target_agent_id=_AGENT_C, task_type="t3", params=None, deadline_at=future)
    )
    await collect_repo.complete_task(make_task_result_update(public_id=p3, status="success", exit_code=0))

    result = await collect_repo.find_pending_deadline_servers([s1, s2, s3])
    assert result == [s1]


async def test_find_pending_deadline_servers_distinct(collect_repo: SqlCollectRepository) -> None:
    """같은 서버 다건 pending(task_type 상이) 이어도 DISTINCT — 1회만 반환."""
    sid = await _setup_server(collect_repo)
    future = datetime.now(UTC) + timedelta(hours=1)
    # 부분 UNIQUE (target_server_id, task_type) WHERE status=pending — task_type 상이로 공존.
    await collect_repo.create_task(
        TaskCreate(target_server_id=sid, target_agent_id=_AGENT_A, task_type="t-a", params=None, deadline_at=future)
    )
    await collect_repo.create_task(
        TaskCreate(target_server_id=sid, target_agent_id=_AGENT_A, task_type="t-b", params=None, deadline_at=future)
    )

    result = await collect_repo.find_pending_deadline_servers([sid])
    assert result == [sid]


async def test_find_pending_deadline_servers_empty_input(collect_repo: SqlCollectRepository) -> None:
    assert await collect_repo.find_pending_deadline_servers([]) == []
