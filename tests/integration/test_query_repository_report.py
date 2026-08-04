"""QueryRepository — 보고서·환경 요약 SQL 통합 테스트 (본 세션 v3~v5 추가분).

검증 영역:
- report_aggregate — iowait_p95/peak + inventory(cpu_cores·mem_total_bytes·block_devices) + 용량 임박 마운트
- report_uptime_stats — period 안 boot_time DISTINCT count - 1
- report_disk_io_baseline — io_bytes·ops delta 평균 (IOPS·throughput_kbps), DiskIoBaselineRaw
- report_net_io_baseline — rx/tx bytes delta 평균 (kbps), NetIoBaselineRaw
- report_memory_breakdown — used/available/cached/buffers 윈도우 평균 (전체 메모리 대비)
- report_cpu_breakdown — user/system/iowait LAG delta 비율
"""

from datetime import UTC, datetime, timedelta

import pytest

from assessment_engine.db.dtos.inbound import (
    DiskIoEntry,
    FilesystemEntry,
    NetIoEntry,
)
from assessment_engine.db.repositories.collect_repository import CollectRepository
from assessment_engine.db.repositories.query.query_repository import QueryRepository
from tests.approx import approx
from tests.factories import _DISK_DEVICE_ID, _IFACE_ID, make_inventory, make_metrics

pytestmark = pytest.mark.asyncio


async def _seed_server_with_period_metrics(
    collect_repo: CollectRepository,
    composite_id: str,
    n_points: int = 10,
    interval_min: int = 1,
) -> tuple[int, datetime, datetime]:
    """server 1대 + n_points 시점의 metrics 시계열 (간격 interval_min).

    각 시점 cpu/disk/net 누적 카운터 단조 증가 — counter_agg delta가 양수.
    filesystem free_bytes는 단조 감소 (디스크 채워지는 추세). device_id/iface_id 는 inventory
    block_device/net_interface 와 조인되는 안정 id(물리 필터 정합).
    """
    sid = await collect_repo.upsert_server(make_inventory(composite_id=composite_id))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=interval_min * (n_points - 1))

    for i in range(n_points):
        ts = base_ts + timedelta(minutes=interval_min * i)
        m = make_metrics(
            collected_at=ts,
            cpu_user_s=1000 + i * 100,
            cpu_system_s=200 + i * 30,
            cpu_iowait_s=50 + i * 40,  # iowait 단조 증가
            cpu_idle_s=8000 + i * 600,
            disk_io=[
                DiskIoEntry(
                    device_id=_DISK_DEVICE_ID,
                    device_name="sda",
                    ops_read=100 + i * 50,  # 분당 50 IOPS reads
                    ops_write=50 + i * 30,  # 분당 30 IOPS writes
                    io_read_bytes=(2000 + i * 1000) * 512,  # By (sectors*512 환산). 분당 512000 By
                    io_write_bytes=(1000 + i * 500) * 512,
                ),
            ],
            filesystems=[
                FilesystemEntry(
                    mountpoint="/data",
                    fstype="ext4",
                    used_bytes=(50 + i * 2) * 10**9,  # total 100GB - free -> 분당 2GB 채움
                    free_bytes=(50 - i * 2) * 10**9,  # 분당 2GB 감소 -> 25일 후 full
                ),
            ],
            net_io=[
                NetIoEntry(
                    iface_id=_IFACE_ID,
                    iface_name="eth0",
                    rx_bytes=1_000_000 + i * 60_000,  # 분당 60KB = 1KB/s
                    tx_bytes=500_000 + i * 30_000,  # 분당 30KB = 0.5KB/s
                    rx_packets=1000 + i * 100,
                    tx_packets=500 + i * 50,
                    rx_errors=0,
                    tx_errors=0,
                ),
            ],
        )
        await collect_repo.record_metrics(sid, m)

    return sid, base_ts, base_ts + timedelta(minutes=interval_min * (n_points - 1))


# ─── report_aggregate iowait + inventory 합계 ─────────────────────────────


async def test_report_aggregate_returns_iowait_and_inventory(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-iowait")
    rows = await query_repo.report_aggregate(
        [sid],
        period_days=1,
        end=end + timedelta(minutes=1),
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.iowait_p95_pct is not None and r.iowait_p95_pct > 0
    assert r.iowait_peak_pct is not None and r.iowait_peak_pct >= r.iowait_p95_pct
    assert r.cpu_cores == 4
    assert r.mem_total_bytes == 8 * 1024**3
    assert r.block_devices and r.block_devices[0]["size_bytes"] == 100 * 10**9
    assert r.boot_time is not None


async def test_report_aggregate_returns_reproduction_columns(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """report_aggregate SELECT 가 server_inventory 재현 9컬럼을 ReportRowRaw 로 왕복 — SELECT 오타·매핑 누락 가드.

    재현 컬럼은 server_inventory LEFT JOIN 직결이라 metric 불요. JSONB(boot/nonblock_mounts) 왕복 포함.
    """
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
                {"source": "tmpfs", "target": "/run", "fstype": "tmpfs",
                 "options": ["rw", "nosuid"], "fs_freq": 0, "fs_passno": 0}
            ],
        )
    )
    rows = await query_repo.report_aggregate([sid], period_days=1, end=datetime.now(UTC))
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


# ─── report_uptime_stats — boot_time DISTINCT count - 1 ──────────────────


async def test_report_uptime_stats_no_reboot(collect_repo: CollectRepository, query_repo: QueryRepository):
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-up-stable")
    counts = await query_repo.report_uptime_stats(
        [sid],
        period_days=1,
        end=end + timedelta(minutes=1),
    )
    # period 안 boot_time 1개 (DISTINCT count = 1) -> 0회 재부팅
    assert counts.get(sid, 0) == 0


async def test_report_uptime_stats_counts_reboot_transitions(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """boot_time 2회 변경 -> reboot_count 2 (inventory_history는 boot_time 변경 시 append)."""
    boot1 = datetime(2026, 5, 1, tzinfo=UTC)
    boot2 = datetime(2026, 5, 5, tzinfo=UTC)
    boot3 = datetime(2026, 5, 10, tzinfo=UTC)

    # 3번 upsert — boot_time 다를 때마다 history append
    sid = await collect_repo.upsert_server(
        make_inventory(
            composite_id="r-up-reboot",
            boot_time=boot1,
            collected_at=datetime(2026, 5, 1, 1, tzinfo=UTC),
        )
    )
    await collect_repo.upsert_server(
        make_inventory(
            composite_id="r-up-reboot",
            boot_time=boot2,
            collected_at=datetime(2026, 5, 5, 1, tzinfo=UTC),
        )
    )
    await collect_repo.upsert_server(
        make_inventory(
            composite_id="r-up-reboot",
            boot_time=boot3,
            collected_at=datetime(2026, 5, 10, 1, tzinfo=UTC),
        )
    )

    counts = await query_repo.report_uptime_stats(
        [sid],
        period_days=30,
        end=datetime(2026, 5, 15, tzinfo=UTC),
    )
    assert counts.get(sid, 0) == 2  # boot_time DISTINCT 3 - 1 = 2회 재부팅


# ─── report_disk_io_baseline — IOPS + throughput ─────────────────────────


async def test_report_disk_io_baseline_iops_and_throughput(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    sid, _start, end = await _seed_server_with_period_metrics(
        collect_repo,
        "r-disk-io",
        n_points=10,
        interval_min=1,
    )
    io_map = await query_repo.report_disk_io_baseline(
        [sid],
        period_days=1,
        end=end + timedelta(minutes=1),
    )
    assert sid in io_map
    bl = io_map[sid]
    iops, throughput_kbps = bl.iops_baseline, bl.throughput_kbps_baseline
    iops_p95, iops_peak, kbps_p95, kbps_peak = bl.iops_p95, bl.iops_peak, bl.kbps_p95, bl.kbps_peak
    # 분당 50+30=80 ops + 1분 간격 -> IOPS = 80/60 ~= 1.33. floor int 변환
    assert iops is not None and iops >= 1
    assert throughput_kbps is not None and throughput_kbps > 0
    # p95/peak는 시점 rate 기반 — baseline 이상 (모든 시점 동일 rate라 p95 ≈ peak ≈ baseline)
    assert iops_p95 is not None and iops_p95 > 0
    assert iops_peak is not None and iops_peak >= iops_p95
    assert kbps_p95 is not None and kbps_p95 > 0
    assert kbps_peak is not None and kbps_peak >= kbps_p95


async def test_report_disk_io_baseline_missing_data_returns_empty(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """metric 없는 서버 -> dict에서 누락."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-disk-empty"))
    io_map = await query_repo.report_disk_io_baseline(
        [sid],
        period_days=1,
        end=datetime.now(UTC),
    )
    assert sid not in io_map


# ─── report_net_io_baseline — rx/tx kbps ─────────────────────────────────


async def test_report_net_io_baseline_rx_tx(collect_repo: CollectRepository, query_repo: QueryRepository):
    sid, _start, end = await _seed_server_with_period_metrics(
        collect_repo,
        "r-net",
        n_points=10,
        interval_min=1,
    )
    net_map = await query_repo.report_net_io_baseline(
        [sid],
        period_days=1,
        end=end + timedelta(minutes=1),
    )
    assert sid in net_map
    bl = net_map[sid]
    rx_kbps, tx_kbps = bl.rx_kbps_baseline, bl.tx_kbps_baseline
    rx_p95, rx_peak, tx_p95, tx_peak = bl.rx_p95, bl.rx_peak, bl.tx_p95, bl.tx_peak
    # 분당 60000 bytes / 60s / 1024 ~= 0.97 kB/s rx
    assert rx_kbps is not None and rx_kbps > 0
    assert tx_kbps is not None and tx_kbps > 0
    assert rx_kbps > tx_kbps  # rx 60KB/min > tx 30KB/min
    assert rx_p95 is not None and rx_peak is not None and rx_peak >= rx_p95
    assert tx_p95 is not None and tx_peak is not None and tx_peak >= tx_p95


# ─── 합산: 5 SQL이 같은 server_ids·period 입력 일관 동작 ──────────────────


async def test_all_report_queries_share_server_ids_and_period(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """get_report·get_inventory_export가 호출하는 4개 SQL이 같은 입력으로 정합 결과."""
    sid, _start, end = await _seed_server_with_period_metrics(
        collect_repo,
        "r-combo",
        n_points=10,
        interval_min=1,
    )
    end_q = end + timedelta(minutes=1)
    raws = await query_repo.report_aggregate([sid], period_days=1, end=end_q)
    uptime = await query_repo.report_uptime_stats([sid], period_days=1, end=end_q)
    disk_io = await query_repo.report_disk_io_baseline([sid], period_days=1, end=end_q)
    net_io = await query_repo.report_net_io_baseline([sid], period_days=1, end=end_q)

    assert len(raws) == 1
    # 마운트 최악 used% 는 report_aggregate 단일 산출 (별도 report_mount_worst 폐기)
    assert raws[0].worst_mount_used_pct is not None
    assert sid in disk_io
    assert sid in net_io
    # uptime은 boot_time 변경 없으면 0 또는 누락 (둘 다 reboot_count=0 의미)
    assert uptime.get(sid, 0) == 0


# ─── 개별 보고서 심화 — 메모리 구성 / CPU 분류 ──────────────────


async def test_report_memory_breakdown_pct_split(collect_repo: CollectRepository, query_repo: QueryRepository):
    """used/available/cached/buffers 윈도우 평균 (전체 메모리 대비). 기본 mem 8GB·available 5GB."""
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-mem-bd")
    mb = await query_repo.report_memory_breakdown(sid, period_days=1, end=end + timedelta(minutes=1))
    # mem_total=8GB, available=5GB -> used 37.5% / available 62.5%, cached 1GB=12.5%, buffers 200MB~2.4%
    assert mb.used_pct == approx(37.5, abs=0.1)
    assert mb.available_pct == approx(62.5, abs=0.1)
    assert mb.cached_pct == approx(12.5, abs=0.1)
    assert mb.buffers_pct is not None and 0 < mb.buffers_pct < 5


async def test_report_cpu_breakdown_delta_split(collect_repo: CollectRepository, query_repo: QueryRepository):
    """user/system/iowait counter_agg delta 비율. step delta user 100·system 30·iowait 40·idle 600 -> total 770."""
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-cpu-bd")
    cb = await query_repo.report_cpu_breakdown(sid, period_days=1, end=end + timedelta(minutes=1))
    assert cb.user_pct == approx(100 / 770 * 100, abs=0.5)
    assert cb.system_pct == approx(30 / 770 * 100, abs=0.5)
    assert cb.iowait_pct == approx(40 / 770 * 100, abs=0.5)


async def test_report_breakdowns_single_equals_batch(collect_repo: CollectRepository, query_repo: QueryRepository):
    """single(sid) 은 batch([sid])[sid] 의 N=1 특수화 — memory·cpu 두 축 값 완전 동일 (정합성).

    단독 보고서(single 경로)와 N대 선택 child(batch prefetch 경로)가 같은 서버에 대해 같은 값을 내야 한다.
    """
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-eq-batch")
    end_q = end + timedelta(minutes=1)

    mem_single = await query_repo.report_memory_breakdown(sid, period_days=1, end=end_q)
    mem_batch = (await query_repo.report_memory_breakdown_batch([sid], period_days=1, end=end_q)).get(sid)
    assert mem_single == mem_batch

    cpu_single = await query_repo.report_cpu_breakdown(sid, period_days=1, end=end_q)
    cpu_batch = (await query_repo.report_cpu_breakdown_batch([sid], period_days=1, end=end_q)).get(sid)
    assert cpu_single == cpu_batch


# ─── cagg counter reset 처리 (ADR 0043 — counter_agg 정석) ────────────────────


async def test_report_aggregate_counter_reset_segments_summed(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """재부팅 counter reset 을 server_metrics_5m cagg counter_agg 가 값-감소 기준 일률 흡수.

    reset 전후 CPU 부하가 다를 때, naive last-first 는 reset 이후 세그먼트만 보아 틀리지만(여기선 50%),
    counter_agg 는 두 단조 세그먼트를 합산해 가중 평균(약 32%)을 산출한다. 5분 버킷 1개 안에 reset 포함.
    """
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-reset-cagg"))
    # 한 5분 버킷에 7표본(30s 간격) — 정렬된 과거 base. cpu total = user+idle (system/iowait/others=0).
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    base = now - timedelta(minutes=12)
    base = base - timedelta(minutes=base.minute % 5) + timedelta(seconds=30)
    # (user, idle) 누적. i0~3: 부하 20%(idle 80%), i4 reset(0,0), i5~6: 부하 50%(idle 50%).
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
    rows = await query_repo.report_aggregate([sid], period_days=1, end=now)
    assert len(rows) == 1
    cpu_p95 = rows[0].cpu_p95_pct
    # counter_agg: total delta = 3000+2000=5000, idle delta = 2400+1000=3400 -> 1-3400/5000 = 32%.
    # reset 점프(naive 50% 또는 비정상 spike)로 부풀지 않음.
    assert cpu_p95 is not None and 30.0 <= cpu_p95 <= 34.0


async def test_report_disk_io_baseline_counter_reset_segments_summed(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """disk ops 카운터 reset(재부팅)을 server_disk_io_5m cagg counter_agg 가 일률 흡수.

    기존 LAG baseline 은 boot_time gate 없이 delta>=0 만이라 reset 처리가 CPU 와 불일치했다(ADR 0043 weirdness).
    counter_agg 는 reset 전후 단조 세그먼트를 합산 — IOPS baseline 이 reset 점프로 왜곡되지 않는다.
    """
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-disk-reset"))
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    base = now - timedelta(minutes=12)
    base = base - timedelta(minutes=base.minute % 5) + timedelta(seconds=30)
    # reads 누적 분당 약 100/min(= delta 50/30s). reset at idx 3 (값 감소). 한 5분 버킷.
    reads = [0, 50, 100, 150, 0, 50, 100]  # idx 3->4 에서 150->0 감소(reset)
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
                        io_read_bytes=rd * 8 * 512,  # By (sectors*512 환산)
                        io_write_bytes=0,
                    )
                ],
                filesystems=[],
                net_io=[],
            ),
        )
    d = await query_repo.report_disk_io_baseline([sid], 1, now)
    assert sid in d
    iops_baseline = d[sid].iops_baseline
    # counter_agg: reads delta = (150-0)+(100-0)=250 over ~5분 관측. naive last-first=100-0=100 이면 과소.
    # reset 음수 점프(150->0)가 IOPS 를 음수/0 으로 왜곡하지 않고 양수 baseline 산출.
    assert iops_baseline is not None and iops_baseline > 0


# ─── ADR 0052 신 신호 report_aggregate 집계 ──────────────────────────────


async def test_report_aggregate_adr0052_signals(collect_repo: CollectRepository, query_repo: QueryRepository):
    """ADR 0052 신 신호가 report_aggregate 로 집계되는지 — steal p95·burst·D-state·swap paging·await·
    drop%·retrans%·history_hours. 같은 5분 버킷 다중 시점으로 counter_agg delta 성립.

    runway(바이트/inode)는 fill-rate 최소 span 게이트가 있어 긴-span 전용 테스트로 분리
    (test_report_aggregate_runway_long_span).
    """
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
            cpu_steal_s=100 + i * 50,  # steal 단조 증가 -> steal% p95 > 0
            cpu_blocked=3,  # D-state gauge -> procs_blocked_p95 ~ 3
            paging_major=500 + i * 40,  # refault(하드폴트) 단조 증가 -> mem_swap_paging True
            net_tcp_retransmits=10 + i * 5,  # 재전송 단조 증가
            disk_io=[
                DiskIoEntry(
                    device_id=_DISK_DEVICE_ID,
                    device_name="sda",
                    ops_read=100 + i * 50,
                    ops_write=50 + i * 30,
                    io_read_bytes=(2000 + i * 1000) * 512,
                    io_write_bytes=(1000 + i * 500) * 512,
                    op_read_time_s=(1000 + i * 100) / 1000,  # await 원자료(s) 단조 증가
                    op_write_time_s=(500 + i * 50) / 1000,
                    io_time_s=i * 50,  # device busy s (분당 +50s -> %util 0.83 >= await 게이트 0.5)
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
                    rx_dropped=5 + i * 2,  # 드롭 단조 증가 -> drop% > 0
                    tx_dropped=2 + i,
                ),
            ],
            filesystems=[
                FilesystemEntry(
                    mountpoint="/data",
                    fstype="ext4",
                    used_bytes=(50 + i * 2) * 10**9,
                    free_bytes=(50 - i * 2) * 10**9,  # 바이트 감소 -> runway 산출
                    inodes_used=i * 10_000,  # inode 사용 증가
                    inodes_free=1_000_000 - i * 10_000,  # inode 감소 -> inode runway 산출
                ),
            ],
        )
        await collect_repo.record_metrics(sid, m)

    rows = await query_repo.report_aggregate([sid], period_days=1, end=base_ts + timedelta(minutes=n))
    assert len(rows) == 1
    r = rows[0]
    # CPU 신뢰도 신호
    assert r.cpu_steal_p95_pct is not None and r.cpu_steal_p95_pct > 0
    assert r.cpu_burst_ratio is not None and r.cpu_burst_ratio > 0
    assert r.history_hours is not None and r.history_hours > 0
    # D-state + swap paging (메모리 근본원인 판별)
    assert r.procs_blocked_p95 is not None and r.procs_blocked_p95 >= 2.5
    assert r.mem_swap_paging is True
    # 디스크 I/O await (물리 device op_time delta, 양 OS 통일)
    assert r.disk_await_p95_ms is not None and r.disk_await_p95_ms > 0
    # 네트워크 품질 (드롭·재전송)
    assert r.net_drop_pct is not None and r.net_drop_pct > 0
    assert r.net_retrans_pct is not None and r.net_retrans_pct > 0


async def test_report_aggregate_runway_long_span(collect_repo: CollectRepository, query_repo: QueryRepository):
    """용량/inode runway 는 fill-rate 최소 span(RS_DISK_RATE_MIN_SPAN_DAYS ~1.25일) 넘는 관측에서만 산출 —
    짧은 span 외삽(노이즈) 배제. 가용 이력 전체 span(bucket <= end, 하한 없음, F10) 기반이라 period 창과 무관.

    2일 span + free/inode 감소 추세 -> byte·inode runway 양수. (짧은 span 은 None: adr0052_signals 참조.)
    """
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-runway-span"))
    end = datetime.now(UTC).replace(microsecond=0)
    base_ts = end - timedelta(days=2)
    n = 16
    for i in range(n):
        ts = base_ts + timedelta(hours=i * 3)  # 0..45h span (~1.9일 > 1.25 게이트)
        m = make_metrics(
            collected_at=ts,
            filesystems=[
                FilesystemEntry(
                    mountpoint="/data",
                    fstype="ext4",
                    used_bytes=(50 + i) * 10**9,
                    free_bytes=(50 - i) * 10**9,  # free 감소 -> byte runway 산출
                    inodes_used=i * 20_000,
                    inodes_free=1_000_000 - i * 20_000,  # inode 감소 -> inode runway 산출
                ),
            ],
        )
        await collect_repo.record_metrics(sid, m)
    r = (await query_repo.report_aggregate([sid], period_days=14, end=end))[0]
    assert r.disk_capacity_runway_days is not None and r.disk_capacity_runway_days >= 0
    assert r.disk_inode_runway_days is not None and r.disk_inode_runway_days >= 0


async def test_report_aggregate_adr0052_signals_absent_are_none(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """신 신호 미발행(옛 agent) 시 신 필드는 None/False graceful — 옛 경로 무손상."""
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-rs0052-absent")
    rows = await query_repo.report_aggregate([sid], period_days=1, end=end + timedelta(minutes=1))
    assert len(rows) == 1
    r = rows[0]
    # 미발행 신호 — steal 은 0(기본), await/procs_blocked/runway(inode)/retrans 는 None, swap paging False.
    assert r.procs_blocked_p95 is None
    assert r.disk_await_p95_ms is None  # op_read/write_time_s 미발행
    assert r.disk_inode_runway_days is None  # inodes_free 미발행
    assert r.net_retrans_pct is None  # net_tcp_retransmits 미발행
    assert r.mem_swap_paging is False  # paging_major 미발행


# ─── ADR 0052 per-core 단일스레드 신호 ────────────────────────────────────


async def test_report_aggregate_percore_p95_max_reflects_busy_core(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """cpu_percore_p95_max = 가장 바쁜 코어의 p95. 코어0 90%·코어1 5% -> max ~90(집계 평균은 낮음).
    server_cpu_core_5m cagg 코어별 counter_agg delta 로 코어별 util% 산출.
    """
    from assessment_engine.db.dtos.inbound import CpuCoreEntry

    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-percore"))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=9)
    n = 10
    for i in range(n):
        ts = base_ts + timedelta(minutes=i)
        m = make_metrics(
            collected_at=ts,
            cpu_per_core=[
                # 코어0 바쁨: idle +100/step, system +900/step -> total 1000, util = 90%
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
                # 코어1 유휴: idle +950/step -> util = 5%
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

    rows = await query_repo.report_aggregate([sid], period_days=1, end=base_ts + timedelta(minutes=n))
    assert len(rows) == 1
    r = rows[0]
    # 가장 바쁜 코어(0) p95 ~90 반영 — 단일스레드 보호(RS_CPU_PERCORE_HOLD=85) 발화선 위
    assert r.cpu_percore_p95_max is not None and r.cpu_percore_p95_max >= 85.0


async def test_report_aggregate_percore_none_when_absent(collect_repo: CollectRepository, query_repo: QueryRepository):
    """per-core 미발행(구 agent·Windows) 시 cpu_percore_p95_max None — graceful skip."""
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-percore-absent")
    rows = await query_repo.report_aggregate([sid], period_days=1, end=end + timedelta(minutes=1))
    assert rows[0].cpu_percore_p95_max is None


# ─── ADR 0052 run-queue(cpu_run_queue) + OOM ──────────────────────────────


async def test_report_aggregate_runqueue_and_oom(collect_repo: CollectRepository, query_repo: QueryRepository):
    """Linux CPU 포화 신호 run_queue p95 + OOM 발생 bool. 실행큐 8·OOM 증가 -> p95>=8, oom True."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-runqueue-oom"))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=9)
    for i in range(10):
        m = make_metrics(
            collected_at=base_ts + timedelta(minutes=i),
            cpu_run_queue=8,  # gauge -> p95 ~8
            mem_oom_kill=i,  # 단조 증가 -> delta>0 -> oom 발생
        )
        await collect_repo.record_metrics(sid, m)
    rows = await query_repo.report_aggregate([sid], period_days=1, end=base_ts + timedelta(minutes=10))
    r = rows[0]
    assert r.procs_running_p95 is not None and r.procs_running_p95 >= 7.5
    assert r.oom_occurred is True


async def test_report_aggregate_runqueue_oom_absent(collect_repo: CollectRepository, query_repo: QueryRepository):
    """cpu_run_queue·mem_oom_kill 미발행 시 p95 None·oom False — graceful."""
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-runqueue-absent")
    r = (await query_repo.report_aggregate([sid], period_days=1, end=end + timedelta(minutes=1)))[0]
    assert r.procs_running_p95 is None
    assert r.oom_occurred is False


# ─── ADR 0052 신 신호 값 검증 — disk await(counter_agg) · conntrack ratio · inode used% ───
async def test_report_aggregate_await_conntrack_inode(collect_repo: CollectRepository, query_repo: QueryRepository):
    """신 신호 3종 실값 검증 — await 는 물리 device op_time delta 로 양 OS 통일):

    - disk await: Σ(Δ op_read_time_s + Δ op_write_time_s) / Σ(Δ ops_read + Δ ops_write) * 1000 = ms.
      매 분당 op_time += 2.5s · ops += 100 -> await = 25ms(> 20 임계). io_time 분당 +50s -> %util 0.83(게이트 0.5).
    - conntrack: server_metrics_5m conntrack_ratio_max = usage/limit = 55000/65536 = 0.839 (0-1 ratio).
    - inode used%: worst mount inode_pct = used/(used+free)*100 = 950000/1000000*100 = 95%(>= 85 정적 가드).
    """
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-new-signals"))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=11)
    for i in range(12):
        ts = base_ts + timedelta(minutes=i)
        m = make_metrics(
            collected_at=ts,
            net_conntrack_usage=55_000,
            net_conntrack_limit=65_536,
            # 물리 device op_time/ops delta 로 await 산출 (분당 op_time +2.5s, ops +100 -> 25ms).
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
                    io_time_s=i * 50,  # device busy s (분당 +50s -> %util 0.83 >= await 게이트 0.5)
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
    r = (await query_repo.report_aggregate([sid], period_days=1, end=end))[0]
    assert r.disk_await_p95_ms is not None, "물리 device op_time delta 로 await 채워져야 함"
    assert 24.0 <= r.disk_await_p95_ms <= 26.0, f"await 25ms 근방 기대, got {r.disk_await_p95_ms}"
    assert r.conntrack_ratio is not None and 0.83 <= r.conntrack_ratio <= 0.85, f"got {r.conntrack_ratio}"
    assert r.disk_inode_used_pct is not None and 94.0 <= r.disk_inode_used_pct <= 96.0, f"got {r.disk_inode_used_pct}"
