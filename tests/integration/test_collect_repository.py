from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text

from assessment_engine.db.dtos.inbound import (
    DiskIoEntry,
    FilesystemEntry,
    NetIoEntry,
    PressureEntry,
)
from tests.factories import make_inventory, make_metrics

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from assessment_engine.db.repositories.collect_sql import SqlCollectRepository


async def test_upsert_server_inserts_new(collect_repo: SqlCollectRepository):
    inv = make_inventory(agent_id="00000000-0000-4000-8000-000000000001", hostname="h1")
    server_id = await collect_repo.upsert_server(inv)
    assert server_id > 0


async def test_upsert_server_stores_machine_id(
    collect_repo: SqlCollectRepository,
    db_session: AsyncSession,
):
    inv = make_inventory(
        agent_id="00000000-0000-4000-8000-000000000002",
        machine_id="raw-machine-xyz",
        hostname="h",
    )
    sid = await collect_repo.upsert_server(inv)
    machine_id = (
        await db_session.execute(
            text("SELECT machine_id FROM server_inventory WHERE id = :i"),
            {"i": sid},
        )
    ).scalar_one()
    assert machine_id == "raw-machine-xyz"


async def test_upsert_server_overwrites_fields_on_conflict(
    collect_repo: SqlCollectRepository,
    db_session: AsyncSession,
):
    aid = "00000000-0000-4000-8000-000000000003"
    inv1 = make_inventory(agent_id=aid, hostname="srv-a", cpu_cores=4)
    inv2 = make_inventory(agent_id=aid, hostname="srv-a", cpu_cores=16)
    await collect_repo.upsert_server(inv1)
    await collect_repo.upsert_server(inv2)

    row = (
        await db_session.execute(
            text("SELECT cpu_cores FROM server_inventory WHERE agent_id = :a"),
            {"a": aid},
        )
    ).one()
    assert row.cpu_cores == 16


async def test_upsert_server_same_agent_id_converges_across_reboot(
    collect_repo: SqlCollectRepository,
    db_session: AsyncSession,
):
    aid = "00000000-0000-4000-8000-000000000004"
    inv1 = make_inventory(agent_id=aid, composite_id="reboot-A", hostname="host-a", cpu_cores=4)
    inv2 = make_inventory(agent_id=aid, composite_id="reboot-B", hostname="host-b", cpu_cores=8)
    sid1 = await collect_repo.upsert_server(inv1)
    sid2 = await collect_repo.upsert_server(inv2)
    assert sid1 == sid2

    row = (
        await db_session.execute(
            text("SELECT hostname, composite_id FROM server_inventory WHERE agent_id = :a"),
            {"a": aid},
        )
    ).one()
    assert row.hostname == "host-b"
    assert row.composite_id == "reboot-B"

    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM server_inventory WHERE agent_id = :a"),
            {"a": aid},
        )
    ).scalar_one()
    assert count == 1


async def test_find_server_id_existing(collect_repo: SqlCollectRepository):
    aid = "00000000-0000-4000-8000-000000000011"
    sid = await collect_repo.upsert_server(make_inventory(agent_id=aid, hostname="h"))
    assert await collect_repo.find_server_id(aid) == sid


async def test_find_server_id_missing(collect_repo: SqlCollectRepository):
    assert await collect_repo.find_server_id("00000000-0000-4000-8000-0000000000ff") is None


async def test_find_server_id_distinct_agent_ids_isolated(
    collect_repo: SqlCollectRepository,
):
    aid_a = "00000000-0000-4000-8000-000000000012"
    aid_b = "00000000-0000-4000-8000-000000000013"
    sid_a = await collect_repo.upsert_server(make_inventory(agent_id=aid_a, hostname="a"))
    sid_b = await collect_repo.upsert_server(make_inventory(agent_id=aid_b, hostname="b"))
    assert await collect_repo.find_server_id(aid_a) == sid_a
    assert await collect_repo.find_server_id(aid_b) == sid_b
    assert sid_a != sid_b


async def test_different_agent_id_same_machine_id_hostname_isolated(
    collect_repo: SqlCollectRepository,
):
    sid_a = await collect_repo.upsert_server(
        make_inventory(
            agent_id="00000000-0000-4000-8000-000000000021",
            machine_id="mid-clone",
            hostname="same-host",
        )
    )
    sid_b = await collect_repo.upsert_server(
        make_inventory(
            agent_id="00000000-0000-4000-8000-000000000022",
            machine_id="mid-clone",
            hostname="same-host",
        )
    )
    assert sid_b != sid_a


async def test_ensure_server_id_auto_registers_when_missing(
    collect_repo: SqlCollectRepository,
):
    aid = "00000000-0000-4000-8000-000000000031"
    fallback = make_inventory(agent_id=aid, hostname="placeholder")
    server_id, auto = await collect_repo.ensure_server_id(aid, fallback)
    assert server_id > 0
    assert auto is True


async def test_ensure_server_id_uses_existing_without_fallback(
    collect_repo: SqlCollectRepository,
    db_session: AsyncSession,
):
    aid = "00000000-0000-4000-8000-000000000032"
    real = make_inventory(agent_id=aid, hostname="real-host", cpu_cores=8)
    sid_real = await collect_repo.upsert_server(real)

    fallback = make_inventory(agent_id=aid, hostname="real-host", cpu_cores=1)
    sid_ensured, auto = await collect_repo.ensure_server_id(aid, fallback)

    assert sid_ensured == sid_real
    assert auto is False
    row = (
        await db_session.execute(
            text("SELECT cpu_cores FROM server_inventory WHERE id = :id"),
            {"id": sid_real},
        )
    ).one()
    assert row.cpu_cores == 8


async def test_record_metrics_inserts_all_four_tables(
    collect_repo: SqlCollectRepository,
):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-rec-1"))
    metrics = make_metrics(
        collected_at=datetime.now(UTC),
        disk_io=[
            DiskIoEntry(
                device_id="by-path:pci-0000:00:05.0",
                device_name="sda",
                ops_read=100,
                ops_write=50,
                io_read_bytes=1000 * 512,
                io_write_bytes=500 * 512,
            ),
            DiskIoEntry(
                device_id="by-path:pci-0000:00:06.0",
                device_name="sdb",
                ops_read=200,
                ops_write=100,
                io_read_bytes=2000 * 512,
                io_write_bytes=1000 * 512,
            ),
        ],
        filesystems=[
            FilesystemEntry(
                mountpoint="/",
                fstype="ext4",
                used_bytes=50_000_000_000 - 18_000_000_000,
                free_bytes=18_000_000_000,
            ),
            FilesystemEntry(
                mountpoint="/data",
                fstype="ext4",
                used_bytes=100_000_000_000 - 78_000_000_000,
                free_bytes=78_000_000_000,
            ),
            FilesystemEntry(
                mountpoint="/var",
                fstype="ext4",
                used_bytes=10_000_000_000 - 4_000_000_000,
                free_bytes=4_000_000_000,
            ),
        ],
        net_io=[
            NetIoEntry(
                iface_id="mac:52:54:00:12:34:56",
                iface_name="eth0",
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
    assert result.filesystem == 3


async def test_record_metrics_stores_pressure_rows(
    collect_repo: SqlCollectRepository,
):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-store-all"))
    ts = datetime.now(UTC)
    m = make_metrics(collected_at=ts)
    m.pressure = [
        PressureEntry(
            resource="cpu", scope="some", stall_time_s=111.0, ratio_avg10=0.1, ratio_avg60=0.05, ratio_avg300=0.01
        ),
        PressureEntry(resource="memory", scope="some", stall_time_s=222.0),
        PressureEntry(resource="io", scope="full", stall_time_s=333.0),
    ]
    result = await collect_repo.record_metrics(sid, m)
    assert result.pressure == 3

    rows = (
        await collect_repo.session.execute(
            text(
                "SELECT resource, scope, stall_time_s FROM server_pressure WHERE server_id = :s AND collected_at = :t"
            ),
            {"s": sid, "t": ts},
        )
    ).all()
    assert {(r.resource, r.scope, r.stall_time_s) for r in rows} == {
        ("cpu", "some", 111.0),
        ("memory", "some", 222.0),
        ("io", "full", 333.0),
    }


async def test_record_metrics_idempotent_on_conflict(
    collect_repo: SqlCollectRepository,
):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-rec-2"))
    ts = datetime.now(UTC)
    m = make_metrics(collected_at=ts)

    first = await collect_repo.record_metrics(sid, m)
    second = await collect_repo.record_metrics(sid, m)

    assert first.metrics == 1
    assert first.disk_io == 1
    assert first.net_io == 1
    assert first.filesystem == 1
    assert second.metrics == 0
    assert second.disk_io == 0
    assert second.net_io == 0
    assert second.filesystem == 0


async def test_record_metrics_skips_empty_collections(
    collect_repo: SqlCollectRepository,
):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-rec-3"))
    m = make_metrics(
        collected_at=datetime.now(UTC),
        disk_io=[],
        filesystems=[],
        net_io=[],
    )
    result = await collect_repo.record_metrics(sid, m)
    assert result.metrics == 1
    assert result.disk_io == 0
    assert result.net_io == 0
    assert result.filesystem == 0


async def test_record_metrics_independent_collected_at_succeeds(
    collect_repo: SqlCollectRepository,
):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-rec-4"))
    ts1 = datetime.now(UTC)
    ts2 = ts1 + timedelta(minutes=1)

    r1 = await collect_repo.record_metrics(sid, make_metrics(collected_at=ts1))
    r2 = await collect_repo.record_metrics(sid, make_metrics(collected_at=ts2))

    assert r1.metrics == 1
    assert r2.metrics == 1


async def test_record_metrics_per_device_unique(
    collect_repo: SqlCollectRepository,
):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-rec-5"))
    ts = datetime.now(UTC)
    m = make_metrics(
        collected_at=ts,
        disk_io=[
            DiskIoEntry(device_id="by-path:pci-0000:00:05.0", device_name="sda", ops_read=1),
            DiskIoEntry(device_id="by-path:pci-0000:00:06.0", device_name="sdb", ops_read=2),
            DiskIoEntry(device_id="nvme-eui.0001", device_name="nvme0n1", ops_read=3),
        ],
        filesystems=[],
        net_io=[],
    )
    result = await collect_repo.record_metrics(sid, m)
    assert result.disk_io == 3


async def test_record_metrics_persists_boot_time_envelope(
    collect_repo: SqlCollectRepository,
    db_session: AsyncSession,
):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-bt-1"))
    ts = datetime.now(UTC)
    boot = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    started = datetime(2026, 5, 9, 12, 5, tzinfo=UTC)
    m = make_metrics(collected_at=ts, boot_time=boot, agent_started_at=started)
    await collect_repo.record_metrics(sid, m)

    row = (
        await db_session.execute(
            text(
                "SELECT boot_time, agent_started_at FROM server_metrics "
                "WHERE server_id = :sid AND collected_at = :ts LIMIT 1"
            ),
            {"sid": sid, "ts": ts},
        )
    ).one()
    assert row.boot_time == boot
    assert row.agent_started_at == started

    for table in ("server_disk_io", "server_net_io", "server_filesystem"):
        count = (
            await db_session.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE server_id = :sid AND collected_at = :ts"),
                {"sid": sid, "ts": ts},
            )
        ).scalar_one()
        assert count == 1, f"{table} should reference the envelope at same (server_id, collected_at)"


async def test_upsert_server_history_appended_on_change(
    collect_repo: SqlCollectRepository,
    db_session: AsyncSession,
):
    aid = "00000000-0000-4000-8000-000000000041"
    inv1 = make_inventory(agent_id=aid, hostname="h1", cpu_cores=4, mem_total_bytes=4 * 1024**3)
    inv2 = make_inventory(agent_id=aid, hostname="h1", cpu_cores=8, mem_total_bytes=8 * 1024**3)
    await collect_repo.upsert_server(inv1)
    await collect_repo.upsert_server(inv2)
    sid = await collect_repo.find_server_id(aid)
    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM server_inventory_history WHERE server_id = :sid"),
            {"sid": sid},
        )
    ).scalar_one()
    assert count == 2, "첫 등록 + 변경 = history 2건이어야 함"


async def test_upsert_server_history_not_appended_when_unchanged(
    collect_repo: SqlCollectRepository,
    db_session: AsyncSession,
):
    aid = "00000000-0000-4000-8000-000000000042"
    inv1 = make_inventory(agent_id=aid, hostname="h1", cpu_cores=4, collected_at=datetime.now(UTC) - timedelta(hours=1))
    inv2 = make_inventory(agent_id=aid, hostname="h1", cpu_cores=4, collected_at=datetime.now(UTC))
    await collect_repo.upsert_server(inv1)
    await collect_repo.upsert_server(inv2)
    sid = await collect_repo.find_server_id(aid)
    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM server_inventory_history WHERE server_id = :sid"),
            {"sid": sid},
        )
    ).scalar_one()
    assert count == 1, "변경 없는 재발행은 history 그대로 — noise 차단"


async def test_upsert_server_history_appended_on_boot_change(
    collect_repo: SqlCollectRepository,
    db_session: AsyncSession,
):
    aid = "00000000-0000-4000-8000-000000000043"
    inv1 = make_inventory(
        agent_id=aid,
        hostname="h1",
        boot={"kernel_cmdline": "ro quiet", "root_ref_type": "label", "grub_install_target": "i386-pc"},
    )
    inv2 = make_inventory(
        agent_id=aid,
        hostname="h1",
        boot={"kernel_cmdline": "ro quiet", "root_ref_type": "uuid", "grub_install_target": "i386-pc"},
    )
    await collect_repo.upsert_server(inv1)
    await collect_repo.upsert_server(inv2)
    sid = await collect_repo.find_server_id(aid)
    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM server_inventory_history WHERE server_id = :sid"),
            {"sid": sid},
        )
    ).scalar_one()
    assert count == 2, "첫 등록 + boot.root_ref_type 변경 = history 2건"


async def test_upsert_server_persists_reproduction_columns(
    collect_repo: SqlCollectRepository,
    db_session: AsyncSession,
):
    aid = "00000000-0000-4000-8000-000000000044"
    inv = make_inventory(
        agent_id=aid,
        hostname="h1",
        arch="x86_64",
        rtc_utc=True,
        nonblock_mounts=[
            {
                "source": "tmpfs",
                "target": "/run",
                "fstype": "tmpfs",
                "options": ["rw", "nosuid"],
                "fs_freq": 0,
                "fs_passno": 0,
            }
        ],
    )
    sid = await collect_repo.upsert_server(inv)
    for table, where in (("server_inventory", "id"), ("server_inventory_history", "server_id")):
        row = (
            await db_session.execute(
                text(f"SELECT arch, rtc_utc, boot, nonblock_mounts FROM {table} WHERE {where} = :sid"),
                {"sid": sid},
            )
        ).one()
        assert row.arch == "x86_64", table
        assert row.rtc_utc is True, table
        assert row.boot is None, table
        assert row.nonblock_mounts == inv.nonblock_mounts, table
