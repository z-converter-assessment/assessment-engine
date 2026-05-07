"""CollectRepository 통합 테스트 — Phase 1 정확화 6가지(A~F) 검증.

각 테스트는 db_session이 function-scope이라 transaction rollback으로 격리.
테스트 시나리오:
- upsert_server (C — DRY): 멱등 INSERT, machine_id UNIQUE
- find_server_id: 존재/미존재
- ensure_server_id (D — facade): auto_registered 플래그
- record_metrics (A — 분리, F — MetricInsertResult 반환):
  - 4개 시계열 테이블 행 수 정확
  - ON CONFLICT DO NOTHING 멱등성 (재호출 시 0행)
  - 빈 entries 시 해당 카운트 0
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from assessment_engine.db.repositories.collect_repository import CollectRepository
from assessment_engine.db.repositories.inbound import (
    DiskIoEntry,
    MountUsageEntry,
    NetIoEntry,
)
from tests.factories import make_inventory, make_metrics


pytestmark = pytest.mark.asyncio


# ─── upsert_server (C) ────────────────────────────────────────────────────

async def test_upsert_server_inserts_new(collect_repo: CollectRepository):
    inv = make_inventory(machine_id="mid-001", hostname="h1")
    server_id = await collect_repo.upsert_server(inv)
    assert server_id > 0


async def test_upsert_server_idempotent_on_same_machine_id(
    collect_repo: CollectRepository,
):
    """같은 machine_id 재호출 시 같은 server_id (ON CONFLICT DO UPDATE + RETURNING)."""
    inv1 = make_inventory(machine_id="mid-002", hostname="h1")
    inv2 = make_inventory(machine_id="mid-002", hostname="h1-renamed", cpu_cores=8)
    sid1 = await collect_repo.upsert_server(inv1)
    sid2 = await collect_repo.upsert_server(inv2)
    assert sid1 == sid2


async def test_upsert_server_overwrites_fields_on_conflict(
    collect_repo: CollectRepository, db_session,
):
    """ON CONFLICT DO UPDATE — 두 번째 호출의 필드가 덮어씀."""
    inv1 = make_inventory(machine_id="mid-003", hostname="old-host", cpu_cores=4)
    inv2 = make_inventory(machine_id="mid-003", hostname="new-host", cpu_cores=16)
    await collect_repo.upsert_server(inv1)
    await collect_repo.upsert_server(inv2)

    row = (await db_session.execute(
        text("SELECT hostname, cpu_cores FROM server_inventory WHERE machine_id = :m"),
        {"m": "mid-003"},
    )).one()
    assert row.hostname == "new-host"
    assert row.cpu_cores == 16


# ─── find_server_id ───────────────────────────────────────────────────────

async def test_find_server_id_existing(collect_repo: CollectRepository):
    sid = await collect_repo.upsert_server(make_inventory(machine_id="mid-find-1"))
    assert await collect_repo.find_server_id("mid-find-1") == sid


async def test_find_server_id_missing(collect_repo: CollectRepository):
    assert await collect_repo.find_server_id("mid-does-not-exist") is None


# ─── ensure_server_id (D — facade with auto_registered flag) ──────────────

async def test_ensure_server_id_auto_registers_when_missing(
    collect_repo: CollectRepository,
):
    """machine_id 미등록 시 fallback inventory로 INSERT, auto_registered=True."""
    fallback = make_inventory(machine_id="mid-ensure-1", hostname="placeholder")
    server_id, auto = await collect_repo.ensure_server_id("mid-ensure-1", fallback)
    assert server_id > 0
    assert auto is True


async def test_ensure_server_id_uses_existing_without_fallback(
    collect_repo: CollectRepository, db_session,
):
    """기존 server_id 사용. fallback은 미사용 — 데이터가 fallback 값으로 덮이지 않음."""
    real = make_inventory(machine_id="mid-ensure-2", hostname="real-host", cpu_cores=8)
    sid_real = await collect_repo.upsert_server(real)

    fallback = make_inventory(machine_id="mid-ensure-2", hostname="placeholder", cpu_cores=1)
    sid_ensured, auto = await collect_repo.ensure_server_id("mid-ensure-2", fallback)

    assert sid_ensured == sid_real
    assert auto is False
    # fallback이 실제로 미사용됐는지 — 데이터가 real 그대로
    row = (await db_session.execute(
        text("SELECT hostname, cpu_cores FROM server_inventory WHERE id = :id"),
        {"id": sid_real},
    )).one()
    assert row.hostname == "real-host"
    assert row.cpu_cores == 8


# ─── record_metrics (A — 분리, F — MetricInsertResult) ────────────────────

async def test_record_metrics_inserts_all_four_tables(
    collect_repo: CollectRepository,
):
    sid = await collect_repo.upsert_server(make_inventory(machine_id="mid-rec-1"))
    metrics = make_metrics(
        collected_at=datetime.now(timezone.utc),
        disk_io=[
            DiskIoEntry(device="sda", reads_completed=100, writes_completed=50, sectors_read=1000, sectors_written=500),
            DiskIoEntry(device="sdb", reads_completed=200, writes_completed=100, sectors_read=2000, sectors_written=1000),
        ],
        mounts=[
            MountUsageEntry(mount="/", total_bytes=50_000_000_000, free_bytes=20_000_000_000, avail_bytes=18_000_000_000),
            MountUsageEntry(mount="/data", total_bytes=100_000_000_000, free_bytes=80_000_000_000, avail_bytes=78_000_000_000),
            MountUsageEntry(mount="/var", total_bytes=10_000_000_000, free_bytes=5_000_000_000, avail_bytes=4_000_000_000),
        ],
        net_io=[
            NetIoEntry(interface="eth0", rx_bytes=1_000_000, tx_bytes=500_000, rx_packets=1000, tx_packets=500, rx_errors=0, tx_errors=0),
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
    sid = await collect_repo.upsert_server(make_inventory(machine_id="mid-rec-2"))
    ts = datetime.now(timezone.utc)
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
    sid = await collect_repo.upsert_server(make_inventory(machine_id="mid-rec-3"))
    m = make_metrics(
        collected_at=datetime.now(timezone.utc),
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
    sid = await collect_repo.upsert_server(make_inventory(machine_id="mid-rec-4"))
    ts1 = datetime.now(timezone.utc)
    ts2 = ts1 + timedelta(minutes=1)

    r1 = await collect_repo.record_metrics(sid, make_metrics(collected_at=ts1))
    r2 = await collect_repo.record_metrics(sid, make_metrics(collected_at=ts2))

    assert r1.metrics == 1 and r2.metrics == 1


async def test_record_metrics_per_device_unique(
    collect_repo: CollectRepository,
):
    """server_disk_io UNIQUE = (server_id, device, collected_at) — 같은 ts 다른 device는 OK."""
    sid = await collect_repo.upsert_server(make_inventory(machine_id="mid-rec-5"))
    ts = datetime.now(timezone.utc)
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