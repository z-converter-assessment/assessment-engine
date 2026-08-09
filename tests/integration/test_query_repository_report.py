from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from assessment_engine.db.dtos.inbound import (
    DiskIoEntry,
    FilesystemEntry,
    NetIoEntry,
)
from tests.approx import approx
from tests.factories import _DISK_DEVICE_ID, _IFACE_ID, make_inventory, make_metrics

if TYPE_CHECKING:
    from assessment_engine.db.repositories.collect_sql import SqlCollectRepository
    from assessment_engine.db.repositories.query.repository_sql import SqlQueryRepository


async def _seed_server_with_period_metrics(
    collect_repo: SqlCollectRepository,
    composite_id: str,
    n_points: int = 10,
    interval_min: int = 1,
) -> tuple[int, datetime, datetime]:
    sid = await collect_repo.upsert_server(make_inventory(composite_id=composite_id))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=interval_min * (n_points - 1))

    for i in range(n_points):
        ts = base_ts + timedelta(minutes=interval_min * i)
        m = make_metrics(
            collected_at=ts,
            cpu_user_s=1000 + i * 100,
            cpu_system_s=200 + i * 30,
            cpu_iowait_s=50 + i * 40,
            cpu_idle_s=8000 + i * 600,
            disk_io=[
                DiskIoEntry(
                    device_id=_DISK_DEVICE_ID,
                    device_name="sda",
                    ops_read=100 + i * 50,
                    ops_write=50 + i * 30,
                    io_read_bytes=(2000 + i * 1000) * 512,
                    io_write_bytes=(1000 + i * 500) * 512,
                ),
            ],
            filesystems=[
                FilesystemEntry(
                    mountpoint="/data",
                    fstype="ext4",
                    used_bytes=(50 + i * 2) * 10**9,
                    free_bytes=(50 - i * 2) * 10**9,
                ),
            ],
            net_io=[
                NetIoEntry(
                    iface_id=_IFACE_ID,
                    iface_name="eth0",
                    rx_bytes=1_000_000 + i * 60_000,
                    tx_bytes=500_000 + i * 30_000,
                    rx_packets=1000 + i * 100,
                    tx_packets=500 + i * 50,
                    rx_errors=0,
                    tx_errors=0,
                ),
            ],
        )
        await collect_repo.record_metrics(sid, m)

    return sid, base_ts, base_ts + timedelta(minutes=interval_min * (n_points - 1))


async def test_report_aggregate_returns_iowait_and_inventory(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
):
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-iowait")
    rows = await query_repo.get_report_aggregate(
        [sid],
        period_days=1,
        end=end + timedelta(minutes=1),
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.iowait_p95_pct is not None
    assert r.iowait_p95_pct > 0
    assert r.iowait_peak_pct is not None
    assert r.iowait_peak_pct >= r.iowait_p95_pct
    assert r.cpu_cores == 4
    assert r.mem_total_bytes == 8 * 1024**3
    assert r.block_devices
    assert r.block_devices[0]["size_bytes"] == 100 * 10**9
    assert r.boot_time is not None


async def test_report_aggregate_returns_reproduction_columns(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
):
    sid = await collect_repo.upsert_server(
        make_inventory(
            composite_id="r-repro",
            arch="x86_64",
            bits=64,
            boot_firmware="uefi",
            secure_boot=True,
            timezone="Asia/Seoul",
            rtc_utc=True,
            boot={"kernel_cmdline": "ro quiet", "root_ref_type": "label", "grub_install_target": None},
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
    )
    rows = await query_repo.get_report_aggregate([sid], period_days=1, end=datetime.now(UTC))
    assert len(rows) == 1
    r = rows[0]
    assert r.arch == "x86_64"
    assert r.bits == 64
    assert r.boot_firmware == "uefi"
    assert r.secure_boot is True
    assert r.timezone == "Asia/Seoul"
    assert r.rtc_utc is True
    assert r.boot is not None
    assert r.boot["root_ref_type"] == "label"
    assert r.boot["grub_install_target"] is None
    assert r.nonblock_mounts is not None
    assert r.nonblock_mounts[0]["fstype"] == "tmpfs"


async def test_report_uptime_stats_no_reboot(collect_repo: SqlCollectRepository, query_repo: SqlQueryRepository):
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-up-stable")
    counts = await query_repo.get_report_uptime_stats(
        [sid],
        period_days=1,
        end=end + timedelta(minutes=1),
    )
    assert counts.get(sid, 0) == 0


async def test_report_uptime_stats_counts_reboot_transitions(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
):
    end = datetime(2026, 5, 10, 12, tzinfo=UTC)
    start = end - timedelta(days=1)
    samples = [
        (start - timedelta(minutes=5), start - timedelta(days=2)),
        (start + timedelta(minutes=10), start + timedelta(minutes=1)),
        (start + timedelta(minutes=20), start + timedelta(minutes=11)),
        (start + timedelta(minutes=30), start + timedelta(minutes=21)),
    ]

    sid = 0
    for collected_at, boot_time in samples:
        sid = await collect_repo.upsert_server(
            make_inventory(
                composite_id="r-up-reboot-boundary",
                boot_time=boot_time,
                collected_at=collected_at,
            )
        )

    counts = await query_repo.get_report_uptime_stats([sid], period_days=1, end=end)
    assert counts[sid] == 3


async def test_agent_restart_counts_include_pre_window_state(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
):
    end = datetime(2026, 5, 10, 12, tzinfo=UTC)
    start = end - timedelta(days=1)
    boot_time = datetime(2026, 5, 1, tzinfo=UTC)
    samples = [
        (start - timedelta(minutes=5), datetime(2026, 5, 9, 11, tzinfo=UTC)),
        (start + timedelta(minutes=10), datetime(2026, 5, 9, 12, 5, tzinfo=UTC)),
        (start + timedelta(minutes=20), datetime(2026, 5, 9, 12, 15, tzinfo=UTC)),
        (start + timedelta(minutes=30), datetime(2026, 5, 9, 12, 25, tzinfo=UTC)),
    ]

    sid = 0
    for collected_at, agent_started_at in samples:
        sid = await collect_repo.upsert_server(
            make_inventory(
                composite_id="r-agent-restart-boundary",
                collected_at=collected_at,
                boot_time=boot_time,
                agent_started_at=agent_started_at,
            )
        )

    report_counts = await query_repo.get_report_agent_restart_stats([sid], period_days=1, end=end)
    recent_counts = await query_repo.get_agent_restart_counts_recent([sid], start)

    assert report_counts[sid] == 3
    assert recent_counts[sid] == 3


async def test_report_disk_io_baseline_iops_and_throughput(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
):
    sid, _start, end = await _seed_server_with_period_metrics(
        collect_repo,
        "r-disk-io",
        n_points=10,
        interval_min=1,
    )
    io_map = await query_repo.get_report_disk_io_baseline(
        [sid],
        period_days=1,
        end=end + timedelta(minutes=1),
    )
    assert sid in io_map
    bl = io_map[sid]
    iops, throughput_kbps = bl.iops_baseline, bl.throughput_kbps_baseline
    iops_p95, iops_peak, kbps_p95, kbps_peak = bl.iops_p95, bl.iops_peak, bl.kbps_p95, bl.kbps_peak
    assert iops is not None
    assert iops >= 1
    assert throughput_kbps is not None
    assert throughput_kbps > 0
    assert iops_p95 is not None
    assert iops_p95 > 0
    assert iops_peak is not None
    assert iops_peak >= iops_p95
    assert kbps_p95 is not None
    assert kbps_p95 > 0
    assert kbps_peak is not None
    assert kbps_peak >= kbps_p95


async def test_report_disk_io_baseline_missing_data_returns_empty(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-disk-empty"))
    io_map = await query_repo.get_report_disk_io_baseline(
        [sid],
        period_days=1,
        end=datetime.now(UTC),
    )
    assert sid not in io_map


async def test_report_net_io_baseline_rx_tx(collect_repo: SqlCollectRepository, query_repo: SqlQueryRepository):
    sid, _start, end = await _seed_server_with_period_metrics(
        collect_repo,
        "r-net",
        n_points=10,
        interval_min=1,
    )
    net_map = await query_repo.get_report_net_io_baseline(
        [sid],
        period_days=1,
        end=end + timedelta(minutes=1),
    )
    assert sid in net_map
    bl = net_map[sid]
    rx_kbps, tx_kbps = bl.rx_kbps_baseline, bl.tx_kbps_baseline
    rx_p95, rx_peak, tx_p95, tx_peak = bl.rx_p95, bl.rx_peak, bl.tx_p95, bl.tx_peak
    assert rx_kbps is not None
    assert rx_kbps > 0
    assert tx_kbps is not None
    assert tx_kbps > 0
    assert rx_kbps > tx_kbps
    assert rx_p95 is not None
    assert rx_peak is not None
    assert rx_peak >= rx_p95
    assert tx_p95 is not None
    assert tx_peak is not None
    assert tx_peak >= tx_p95


async def test_all_report_queries_share_server_ids_and_period(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
):
    sid, _start, end = await _seed_server_with_period_metrics(
        collect_repo,
        "r-combo",
        n_points=10,
        interval_min=1,
    )
    end_q = end + timedelta(minutes=1)
    raws = await query_repo.get_report_aggregate([sid], period_days=1, end=end_q)
    uptime = await query_repo.get_report_uptime_stats([sid], period_days=1, end=end_q)
    disk_io = await query_repo.get_report_disk_io_baseline([sid], period_days=1, end=end_q)
    net_io = await query_repo.get_report_net_io_baseline([sid], period_days=1, end=end_q)

    assert len(raws) == 1
    assert raws[0].worst_mount_used_pct is not None
    assert sid in disk_io
    assert sid in net_io
    assert uptime.get(sid, 0) == 0


async def test_report_memory_breakdown_pct_split(collect_repo: SqlCollectRepository, query_repo: SqlQueryRepository):
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-mem-bd")
    mb = await query_repo.get_report_memory_breakdown(sid, period_days=1, end=end + timedelta(minutes=1))
    assert mb.used_pct == approx(37.5, abs=0.1)
    assert mb.available_pct == approx(62.5, abs=0.1)
    assert mb.cached_pct == approx(12.5, abs=0.1)
    assert mb.buffers_pct is not None
    assert 0 < mb.buffers_pct < 5


async def test_report_cpu_breakdown_delta_split(collect_repo: SqlCollectRepository, query_repo: SqlQueryRepository):
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-cpu-bd")
    cb = await query_repo.get_report_cpu_breakdown(sid, period_days=1, end=end + timedelta(minutes=1))
    assert cb.user_pct == approx(100 / 770 * 100, abs=0.5)
    assert cb.system_pct == approx(30 / 770 * 100, abs=0.5)
    assert cb.iowait_pct == approx(40 / 770 * 100, abs=0.5)


async def test_report_breakdowns_single_equals_batch(
    collect_repo: SqlCollectRepository, query_repo: SqlQueryRepository
):
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-eq-batch")
    end_q = end + timedelta(minutes=1)

    mem_single = await query_repo.get_report_memory_breakdown(sid, period_days=1, end=end_q)
    mem_batch = (await query_repo.get_report_memory_breakdown_batch([sid], period_days=1, end=end_q)).get(sid)
    assert mem_single == mem_batch

    cpu_single = await query_repo.get_report_cpu_breakdown(sid, period_days=1, end=end_q)
    cpu_batch = (await query_repo.get_report_cpu_breakdown_batch([sid], period_days=1, end=end_q)).get(sid)
    assert cpu_single == cpu_batch


async def test_report_aggregate_counter_reset_segments_summed(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-reset-cagg"))
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    base = now - timedelta(minutes=12)
    base = base - timedelta(minutes=base.minute % 5) + timedelta(seconds=30)
    series = [(0, 0), (200, 800), (400, 1600), (600, 2400), (0, 0), (500, 500), (1000, 1000)]
    for i, (user, idle) in enumerate(series):
        await collect_repo.record_metrics(
            sid,
            make_metrics(
                collected_at=base + timedelta(seconds=30 * i),
                cpu_user_s=user,
                cpu_system_s=0,
                cpu_idle_s=idle,
                cpu_iowait_s=0,
                filesystems=[],
                disk_io=[],
                net_io=[],
            ),
        )
    rows = await query_repo.get_report_aggregate([sid], period_days=1, end=now)
    assert len(rows) == 1
    cpu_p95 = rows[0].cpu_p95_pct
    assert cpu_p95 is not None
    assert 30.0 <= cpu_p95 <= 34.0


async def test_report_disk_io_baseline_counter_reset_segments_summed(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-disk-reset"))
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    base = now - timedelta(minutes=12)
    base = base - timedelta(minutes=base.minute % 5) + timedelta(seconds=30)
    reads = [0, 50, 100, 150, 0, 50, 100]
    for i, rd in enumerate(reads):
        await collect_repo.record_metrics(
            sid,
            make_metrics(
                collected_at=base + timedelta(seconds=30 * i),
                disk_io=[
                    DiskIoEntry(
                        device_id=_DISK_DEVICE_ID,
                        device_name="sda",
                        ops_read=rd,
                        ops_write=0,
                        io_read_bytes=rd * 8 * 512,
                        io_write_bytes=0,
                    )
                ],
                filesystems=[],
                net_io=[],
            ),
        )
    d = await query_repo.get_report_disk_io_baseline([sid], 1, now)
    assert sid in d
    iops_baseline = d[sid].iops_baseline
    assert iops_baseline is not None
    assert iops_baseline > 0


async def test_report_aggregate_adr0052_signals(collect_repo: SqlCollectRepository, query_repo: SqlQueryRepository):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-rs0052"))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=9)
    n = 10
    for i in range(n):
        ts = base_ts + timedelta(minutes=i)
        m = make_metrics(
            collected_at=ts,
            cpu_user_s=1000 + i * 100,
            cpu_system_s=200 + i * 30,
            cpu_idle_s=8000 + i * 400,
            cpu_iowait_s=50 + i * 20,
            cpu_steal_s=100 + i * 50,
            cpu_blocked=3,
            paging_major=500 + i * 40,
            net_tcp_retransmits=10 + i * 5,
            disk_io=[
                DiskIoEntry(
                    device_id=_DISK_DEVICE_ID,
                    device_name="sda",
                    ops_read=100 + i * 50,
                    ops_write=50 + i * 30,
                    io_read_bytes=(2000 + i * 1000) * 512,
                    io_write_bytes=(1000 + i * 500) * 512,
                    op_read_time_s=(1000 + i * 100) / 1000,
                    op_write_time_s=(500 + i * 50) / 1000,
                    io_time_s=i * 50,
                ),
            ],
            net_io=[
                NetIoEntry(
                    iface_id=_IFACE_ID,
                    iface_name="eth0",
                    rx_bytes=1_000_000 + i * 60_000,
                    tx_bytes=500_000 + i * 30_000,
                    rx_packets=1000 + i * 100,
                    tx_packets=500 + i * 50,
                    rx_errors=0,
                    tx_errors=0,
                    rx_dropped=5 + i * 2,
                    tx_dropped=2 + i,
                ),
            ],
            filesystems=[
                FilesystemEntry(
                    mountpoint="/data",
                    fstype="ext4",
                    used_bytes=(50 + i * 2) * 10**9,
                    free_bytes=(50 - i * 2) * 10**9,
                    inodes_used=i * 10_000,
                    inodes_free=1_000_000 - i * 10_000,
                ),
            ],
        )
        await collect_repo.record_metrics(sid, m)

    rows = await query_repo.get_report_aggregate([sid], period_days=1, end=base_ts + timedelta(minutes=n))
    assert len(rows) == 1
    r = rows[0]
    assert r.cpu_steal_p95_pct is not None
    assert r.cpu_steal_p95_pct > 0
    assert r.cpu_burst_ratio is not None
    assert r.cpu_burst_ratio > 0
    assert r.history_hours is not None
    assert r.history_hours > 0
    assert r.procs_blocked_p95 is not None
    assert r.procs_blocked_p95 >= 2.5
    assert r.mem_swap_paging is True
    assert r.disk_await_p95_ms is not None
    assert r.disk_await_p95_ms > 0
    assert r.net_drop_pct is not None
    assert r.net_drop_pct > 0
    assert r.net_retrans_pct is not None
    assert r.net_retrans_pct > 0


async def test_report_aggregate_runway_long_span(collect_repo: SqlCollectRepository, query_repo: SqlQueryRepository):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-runway-span"))
    end = datetime.now(UTC).replace(microsecond=0)
    base_ts = end - timedelta(days=2)
    n = 16
    for i in range(n):
        ts = base_ts + timedelta(hours=i * 3)
        m = make_metrics(
            collected_at=ts,
            filesystems=[
                FilesystemEntry(
                    mountpoint="/data",
                    fstype="ext4",
                    used_bytes=(50 + i) * 10**9,
                    free_bytes=(50 - i) * 10**9,
                    inodes_used=i * 20_000,
                    inodes_free=1_000_000 - i * 20_000,
                ),
            ],
        )
        await collect_repo.record_metrics(sid, m)
    r = (await query_repo.get_report_aggregate([sid], period_days=14, end=end))[0]
    assert r.disk_capacity_runway_days is not None
    assert r.disk_capacity_runway_days >= 0
    assert r.disk_inode_runway_days is not None
    assert r.disk_inode_runway_days >= 0


async def test_report_aggregate_adr0052_signals_absent_are_none(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
):
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-rs0052-absent")
    rows = await query_repo.get_report_aggregate([sid], period_days=1, end=end + timedelta(minutes=1))
    assert len(rows) == 1
    r = rows[0]
    assert r.procs_blocked_p95 is None
    assert r.disk_await_p95_ms is None
    assert r.disk_inode_runway_days is None
    assert r.net_retrans_pct is None
    assert r.mem_swap_paging is None


async def test_report_aggregate_percore_p95_max_reflects_busy_core(
    collect_repo: SqlCollectRepository,
    query_repo: SqlQueryRepository,
):
    from assessment_engine.db.dtos.inbound import CpuCoreEntry

    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-percore"))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=9)
    n = 10
    for i in range(n):
        ts = base_ts + timedelta(minutes=i)
        m = make_metrics(
            collected_at=ts,
            cpu_per_core=[
                CpuCoreEntry(
                    0,
                    cpu_user_s=0,
                    cpu_nice_s=0,
                    cpu_system_s=900 * i,
                    cpu_idle_s=100 * i,
                    cpu_iowait_s=0,
                    cpu_irq_s=0,
                    cpu_softirq_s=0,
                    cpu_steal_s=0,
                ),
                CpuCoreEntry(
                    1,
                    cpu_user_s=0,
                    cpu_nice_s=0,
                    cpu_system_s=50 * i,
                    cpu_idle_s=950 * i,
                    cpu_iowait_s=0,
                    cpu_irq_s=0,
                    cpu_softirq_s=0,
                    cpu_steal_s=0,
                ),
            ],
        )
        await collect_repo.record_metrics(sid, m)

    rows = await query_repo.get_report_aggregate([sid], period_days=1, end=base_ts + timedelta(minutes=n))
    assert len(rows) == 1
    r = rows[0]
    assert r.cpu_percore_p95_max is not None
    assert r.cpu_percore_p95_max >= 85.0


async def test_report_aggregate_percore_none_when_absent(
    collect_repo: SqlCollectRepository, query_repo: SqlQueryRepository
):
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-percore-absent")
    rows = await query_repo.get_report_aggregate([sid], period_days=1, end=end + timedelta(minutes=1))
    assert rows[0].cpu_percore_p95_max is None


async def test_report_aggregate_runqueue_and_oom(collect_repo: SqlCollectRepository, query_repo: SqlQueryRepository):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-runqueue-oom"))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=9)
    for i in range(10):
        m = make_metrics(
            collected_at=base_ts + timedelta(minutes=i),
            cpu_run_queue=8,
            mem_oom_kill=i,
        )
        await collect_repo.record_metrics(sid, m)
    rows = await query_repo.get_report_aggregate([sid], period_days=1, end=base_ts + timedelta(minutes=10))
    r = rows[0]
    assert r.procs_running_p95 is not None
    assert r.procs_running_p95 >= 7.5
    assert r.oom_occurred is True


async def test_report_aggregate_runqueue_oom_absent(collect_repo: SqlCollectRepository, query_repo: SqlQueryRepository):
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-runqueue-absent")
    r = (await query_repo.get_report_aggregate([sid], period_days=1, end=end + timedelta(minutes=1)))[0]
    assert r.procs_running_p95 is None
    assert r.oom_occurred is False


async def test_report_aggregate_await_conntrack_inode(
    collect_repo: SqlCollectRepository, query_repo: SqlQueryRepository
):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-new-signals"))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=11)
    for i in range(12):
        ts = base_ts + timedelta(minutes=i)
        m = make_metrics(
            collected_at=ts,
            net_conntrack_usage=55_000,
            net_conntrack_limit=65_536,
            disk_io=[
                DiskIoEntry(
                    device_id=_DISK_DEVICE_ID,
                    device_name="sda",
                    ops_read=i * 60,
                    ops_write=i * 40,
                    op_read_time_s=i * 1.5,
                    op_write_time_s=i * 1.0,
                    io_read_bytes=(2000 + i * 1000) * 512,
                    io_write_bytes=(1000 + i * 500) * 512,
                    io_time_s=i * 50,
                ),
            ],
            filesystems=[
                FilesystemEntry(
                    mountpoint="/data",
                    fstype="ext4",
                    used_bytes=60 * 10**9,
                    free_bytes=40 * 10**9,
                    inodes_used=950_000,
                    inodes_free=50_000,
                ),
            ],
        )
        await collect_repo.record_metrics(sid, m)

    end = base_ts + timedelta(minutes=12)
    r = (await query_repo.get_report_aggregate([sid], period_days=1, end=end))[0]
    assert r.disk_await_p95_ms is not None, "물리 device op_time delta 로 await 채워져야 함"
    assert 24.0 <= r.disk_await_p95_ms <= 26.0, f"await 25ms 근방 기대, got {r.disk_await_p95_ms}"
    assert r.conntrack_ratio is not None
    assert 0.83 <= r.conntrack_ratio <= 0.85, f"got {r.conntrack_ratio}"
    assert r.disk_inode_used_pct is not None
    assert 94.0 <= r.disk_inode_used_pct <= 96.0, f"got {r.disk_inode_used_pct}"
