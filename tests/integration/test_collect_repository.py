"""CollectRepository 통합 테스트 — Phase 1 정확화 6가지(A~F) 검증.

각 테스트는 db_session이 function-scope이라 transaction rollback으로 격리.
테스트 시나리오:
- upsert_server (C — DRY): 멱등 INSERT, composite_id UNIQUE
- find_server_id: 존재/미존재
- ensure_server_id (D — facade): auto_registered 플래그
- record_metrics (A — 분리, F — MetricInsertResult 반환):
  - 4개 시계열 테이블 행 수 정확
  - ON CONFLICT DO NOTHING 멱등성 (재호출 시 0행)
  - 빈 entries 시 해당 카운트 0
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from assessment_engine.db.dtos.inbound import (
    DiskIoEntry,
    MountUsageEntry,
    NetIoEntry,
)
from assessment_engine.db.repositories.collect_repository import CollectRepository
from tests.factories import make_inventory, make_metrics

pytestmark = pytest.mark.asyncio


# ─── upsert_server (C) ────────────────────────────────────────────────────


async def test_upsert_server_inserts_new(collect_repo: CollectRepository):
    inv = make_inventory(composite_id="mid-001", hostname="h1")
    server_id = await collect_repo.upsert_server(inv)
    assert server_id > 0


async def test_upsert_server_stores_machine_id(
    collect_repo: CollectRepository,
    db_session,
):
    """machine_id 표시 컬럼 저장 (ADR 0027) — composite_id 식별과 별개, 표시 전용 nullable."""
    inv = make_inventory(composite_id="mid-mach", machine_id="raw-machine-xyz", hostname="h")
    sid = await collect_repo.upsert_server(inv)
    machine_id = (
        await db_session.execute(
            text("SELECT machine_id FROM server_inventory WHERE id = :i"),
            {"i": sid},
        )
    ).scalar_one()
    assert machine_id == "raw-machine-xyz"


async def test_upsert_server_idempotent_on_same_machine_hostname(
    collect_repo: CollectRepository,
):
    """같은 (composite_id, hostname) 재호출 시 같은 server_id (ON CONFLICT DO UPDATE + RETURNING)."""
    inv1 = make_inventory(composite_id="mid-002", hostname="h1")
    inv2 = make_inventory(composite_id="mid-002", hostname="h1", cpu_cores=8)
    sid1 = await collect_repo.upsert_server(inv1)
    sid2 = await collect_repo.upsert_server(inv2)
    assert sid1 == sid2


async def test_upsert_server_overwrites_fields_on_conflict(
    collect_repo: CollectRepository,
    db_session,
):
    """ON CONFLICT DO UPDATE — 같은 (composite_id, hostname) 의 다른 필드가 덮어씀."""
    inv1 = make_inventory(composite_id="mid-003", hostname="srv-a", cpu_cores=4)
    inv2 = make_inventory(composite_id="mid-003", hostname="srv-a", cpu_cores=16)
    await collect_repo.upsert_server(inv1)
    await collect_repo.upsert_server(inv2)

    row = (
        await db_session.execute(
            text("SELECT cpu_cores FROM server_inventory WHERE composite_id = :m AND hostname = :h"),
            {"m": "mid-003", "h": "srv-a"},
        )
    ).one()
    assert row.cpu_cores == 16


async def test_upsert_server_same_composite_id_converges_to_single_row(
    collect_repo: CollectRepository,
    db_session,
):
    """composite_id 단일 UNIQUE 키 — 같은 composite_id 면 hostname 이 달라도 한 row 로 수렴 (ADR 0022).

    composite_id 는 primary MAC + /etc/machine-id 합성 SHA-256 이라 호스트마다 고유.
    hostname 은 display(non-UNIQUE)라 같은 composite_id 로 다른 hostname 이 와도 동일 row 의
    hostname 만 갱신 — 별도 row 생성 안 함.
    """
    inv1 = make_inventory(composite_id="mid-dup", hostname="host-a", cpu_cores=4)
    inv2 = make_inventory(composite_id="mid-dup", hostname="host-b", cpu_cores=8)
    sid1 = await collect_repo.upsert_server(inv1)
    sid2 = await collect_repo.upsert_server(inv2)
    assert sid1 == sid2

    hostname = (
        await db_session.execute(
            text("SELECT hostname FROM server_inventory WHERE composite_id = :m"),
            {"m": "mid-dup"},
        )
    ).scalar_one()
    assert hostname == "host-b"  # 마지막 upsert 의 hostname 으로 갱신

    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM server_inventory WHERE composite_id = :m"),
            {"m": "mid-dup"},
        )
    ).scalar_one()
    assert count == 1


# ─── find_server_id ───────────────────────────────────────────────────────


async def test_find_server_id_existing(collect_repo: CollectRepository):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-find-1", hostname="h"))
    assert await collect_repo.find_server_id("mid-find-1") == sid


async def test_find_server_id_missing(collect_repo: CollectRepository):
    assert await collect_repo.find_server_id("mid-does-not-exist") is None


async def test_find_server_id_distinct_composite_ids_isolated(
    collect_repo: CollectRepository,
):
    """서로 다른 composite_id 는 각각 다른 server_id 로 격리 (composite_id 단일 UNIQUE 매칭)."""
    sid_a = await collect_repo.upsert_server(make_inventory(composite_id="mid-iso-a", hostname="a"))
    sid_b = await collect_repo.upsert_server(make_inventory(composite_id="mid-iso-b", hostname="b"))
    assert await collect_repo.find_server_id("mid-iso-a") == sid_a
    assert await collect_repo.find_server_id("mid-iso-b") == sid_b
    assert sid_a != sid_b


# ─── ensure_server_id (D — facade with auto_registered flag) ──────────────


async def test_ensure_server_id_auto_registers_when_missing(
    collect_repo: CollectRepository,
):
    """(composite_id, hostname) 미등록 시 fallback inventory로 INSERT, auto_registered=True."""
    fallback = make_inventory(composite_id="mid-ensure-1", hostname="placeholder")
    server_id, auto = await collect_repo.ensure_server_id("mid-ensure-1", fallback)
    assert server_id > 0
    assert auto is True


async def test_ensure_server_id_uses_existing_without_fallback(
    collect_repo: CollectRepository,
    db_session,
):
    """기존 server_id 사용. fallback은 미사용 — 데이터가 fallback 값으로 덮이지 않음."""
    real = make_inventory(composite_id="mid-ensure-2", hostname="real-host", cpu_cores=8)
    sid_real = await collect_repo.upsert_server(real)

    fallback = make_inventory(composite_id="mid-ensure-2", hostname="real-host", cpu_cores=1)
    sid_ensured, auto = await collect_repo.ensure_server_id(
        "mid-ensure-2",
        fallback,
    )

    assert sid_ensured == sid_real
    assert auto is False
    # fallback이 실제로 미사용됐는지 — 데이터가 real 그대로
    row = (
        await db_session.execute(
            text("SELECT cpu_cores FROM server_inventory WHERE id = :id"),
            {"id": sid_real},
        )
    ).one()
    assert row.cpu_cores == 8


# ─── record_metrics (A — 분리, F — MetricInsertResult) ────────────────────


async def test_record_metrics_inserts_all_four_tables(
    collect_repo: CollectRepository,
):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-rec-1"))
    metrics = make_metrics(
        collected_at=datetime.now(UTC),
        disk_io=[
            DiskIoEntry(
                device="sda",
                reads_completed=100,
                writes_completed=50,
                sectors_read=1000,
                sectors_written=500,
            ),
            DiskIoEntry(
                device="sdb",
                reads_completed=200,
                writes_completed=100,
                sectors_read=2000,
                sectors_written=1000,
            ),
        ],
        mounts=[
            MountUsageEntry(
                mount="/",
                total_bytes=50_000_000_000,
                free_bytes=20_000_000_000,
                avail_bytes=18_000_000_000,
            ),
            MountUsageEntry(
                mount="/data",
                total_bytes=100_000_000_000,
                free_bytes=80_000_000_000,
                avail_bytes=78_000_000_000,
            ),
            MountUsageEntry(
                mount="/var",
                total_bytes=10_000_000_000,
                free_bytes=5_000_000_000,
                avail_bytes=4_000_000_000,
            ),
        ],
        net_io=[
            NetIoEntry(
                interface="eth0",
                rx_bytes=1_000_000,
                tx_bytes=500_000,
                rx_packets=1000,
                tx_packets=500,
                rx_errors=0,
                tx_errors=0,
            ),
        ],
    )
    result = await collect_repo.record_metrics(sid, metrics)
    assert result.metrics == 1
    assert result.disk_io == 2
    assert result.net_io == 1
    assert result.mount_usage == 3


async def test_record_metrics_idempotent_on_conflict(
    collect_repo: CollectRepository,
):
    """같은 (server_id, collected_at) 재호출 시 ON CONFLICT DO NOTHING — 모든 카운트 0."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-rec-2"))
    ts = datetime.now(UTC)
    m = make_metrics(collected_at=ts)

    first = await collect_repo.record_metrics(sid, m)
    second = await collect_repo.record_metrics(sid, m)

    assert first.metrics == 1 and first.disk_io == 1 and first.net_io == 1 and first.mount_usage == 1
    assert second.metrics == 0
    assert second.disk_io == 0
    assert second.net_io == 0
    assert second.mount_usage == 0


async def test_record_metrics_skips_empty_collections(
    collect_repo: CollectRepository,
):
    """disk_io/mounts/net_io 빈 리스트면 해당 INSERT skip — count 0."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-rec-3"))
    m = make_metrics(
        collected_at=datetime.now(UTC),
        disk_io=[],
        mounts=[],
        net_io=[],
    )
    result = await collect_repo.record_metrics(sid, m)
    assert result.metrics == 1
    assert result.disk_io == 0
    assert result.net_io == 0
    assert result.mount_usage == 0


async def test_record_metrics_independent_collected_at_succeeds(
    collect_repo: CollectRepository,
):
    """collected_at이 다르면 두 번 다 INSERT (UNIQUE 자연키는 (server_id, collected_at))."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-rec-4"))
    ts1 = datetime.now(UTC)
    ts2 = ts1 + timedelta(minutes=1)

    r1 = await collect_repo.record_metrics(sid, make_metrics(collected_at=ts1))
    r2 = await collect_repo.record_metrics(sid, make_metrics(collected_at=ts2))

    assert r1.metrics == 1 and r2.metrics == 1


async def test_record_metrics_per_device_unique(
    collect_repo: CollectRepository,
):
    """server_disk_io UNIQUE = (server_id, device, collected_at) — 같은 ts 다른 device는 OK."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-rec-5"))
    ts = datetime.now(UTC)
    m = make_metrics(
        collected_at=ts,
        disk_io=[
            DiskIoEntry(device="sda", reads_completed=1, writes_completed=0, sectors_read=0, sectors_written=0),
            DiskIoEntry(device="sdb", reads_completed=2, writes_completed=0, sectors_read=0, sectors_written=0),
            DiskIoEntry(device="nvme0n1", reads_completed=3, writes_completed=0, sectors_read=0, sectors_written=0),
        ],
        mounts=[],
        net_io=[],
    )
    result = await collect_repo.record_metrics(sid, m)
    assert result.disk_io == 3


# ─── boot_time / agent_started_at 보존 (counter reset 정밀 식별 의존) ───────


async def test_record_metrics_persists_boot_time_to_all_four_tables(
    collect_repo: CollectRepository,
    db_session,
):
    """boot_time/agent_started_at은 시계열 4개 테이블 모두에 동일 시점값으로 저장.
    server_metrics·disk_io·net_io는 calculator의 reset 판정에 활용. mount_usage는 시점값이라
    calculator 활용 없으나 메타데이터 일관성 + 운영 디버깅 단일 SELECT 위해 보존 (CLAUDE.md C1).
    """
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-bt-1"))
    ts = datetime.now(UTC)
    boot = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    started = datetime(2026, 5, 9, 12, 5, tzinfo=UTC)
    m = make_metrics(collected_at=ts, boot_time=boot, agent_started_at=started)
    await collect_repo.record_metrics(sid, m)

    for table in ("server_metrics", "server_disk_io", "server_net_io", "server_mount_usage"):
        row = (
            await db_session.execute(
                text(
                    f"SELECT boot_time, agent_started_at FROM {table} "
                    f"WHERE server_id = :sid AND collected_at = :ts LIMIT 1"
                ),
                {"sid": sid, "ts": ts},
            )
        ).one()
        assert row.boot_time == boot, f"{table} boot_time mismatch"
        assert row.agent_started_at == started, f"{table} agent_started_at mismatch"


# ─── 명시 select 후 _inventory_changed 회귀 (C5) ──────────────────────────


async def test_upsert_server_history_appended_on_change(
    collect_repo: CollectRepository,
    db_session,
):
    """C5 명시 select(_INVENTORY_COMPARE_COLS) 후에도 변경 감지 동일 — history 한 행 append.

    hostname 은 복합 conflict 키라 변경 시 새 row 가 되어 history append 의미가 사라짐.
    여기서는 hostname 동일 + cpu_cores·mem_total_kb 변경으로 진짜 history 트리거 검증.
    """
    inv1 = make_inventory(composite_id="mid-hist-1", hostname="h1", cpu_cores=4, mem_total_kb=4_000_000)
    inv2 = make_inventory(composite_id="mid-hist-1", hostname="h1", cpu_cores=8, mem_total_kb=8_000_000)
    await collect_repo.upsert_server(inv1)
    await collect_repo.upsert_server(inv2)
    sid = await collect_repo.find_server_id("mid-hist-1")
    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM server_inventory_history WHERE server_id = :sid"),
            {"sid": sid},
        )
    ).scalar_one()
    assert count == 2, "첫 등록 + 변경 = history 2건이어야 함"


async def test_upsert_server_history_not_appended_when_unchanged(
    collect_repo: CollectRepository,
    db_session,
):
    """비교 컬럼 동일 + collected_at만 다름 (1h 주기 재발행 시뮬) → history 추가 없음."""
    inv1 = make_inventory(
        composite_id="mid-hist-2", hostname="h1", cpu_cores=4, collected_at=datetime.now(UTC) - timedelta(hours=1)
    )
    inv2 = make_inventory(
        composite_id="mid-hist-2", hostname="h1", cpu_cores=4, collected_at=datetime.now(UTC)
    )  # 모든 비교 컬럼 동일
    await collect_repo.upsert_server(inv1)
    await collect_repo.upsert_server(inv2)
    sid = await collect_repo.find_server_id("mid-hist-2")
    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM server_inventory_history WHERE server_id = :sid"),
            {"sid": sid},
        )
    ).scalar_one()
    assert count == 1, "변경 없는 재발행은 history 그대로 — noise 차단"
