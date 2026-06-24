"""QueryRepository — 보고서·환경 요약 SQL 통합 테스트 (본 세션 v3~v5 추가분).

검증 영역:
- report_aggregate — iowait_p95/peak + inventory 합계 컬럼(cpu_cores·mem_total_kb·disks·boot_time) 추가
- report_mount_worst — 마운트별 max_used + fill_rate days_until_full 추정
- report_uptime_stats — period 안 boot_time DISTINCT count - 1
- report_disk_io_baseline — sectors·ops delta 평균 (IOPS·throughput_kbps)
- report_net_io_baseline — rx/tx bytes delta 평균 (kbps)
- report_mount_usage — 전체 마운트 윈도우 평균 사용률 (개별 보고서 스토리지 상세)
- report_memory_breakdown — used/available/cached/buffers 윈도우 평균 (전체 메모리 대비)
- report_cpu_breakdown — user/system/iowait LAG delta 비율
"""

from datetime import UTC, datetime, timedelta

import pytest

from assessment_engine.db.dtos.inbound import (
    DiskIoEntry,
    MountUsageEntry,
    NetIoEntry,
)
from assessment_engine.db.repositories.collect_repository import CollectRepository
from tests.factories import make_inventory, make_metrics

pytestmark = pytest.mark.asyncio


async def _seed_server_with_period_metrics(
    collect_repo: CollectRepository,
    composite_id: str,
    n_points: int = 10,
    interval_min: int = 1,
) -> tuple[int, datetime, datetime]:
    """server 1대 + n_points 시점의 metrics 시계열 (간격 interval_min).

    각 시점 cpu/disk/net 누적 카운터 단조 증가 — LAG delta가 양수.
    mount avail_bytes는 단조 감소 (디스크 채워지는 추세).
    """
    sid = await collect_repo.upsert_server(make_inventory(composite_id=composite_id))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=interval_min * (n_points - 1))

    for i in range(n_points):
        ts = base_ts + timedelta(minutes=interval_min * i)
        m = make_metrics(
            collected_at=ts,
            cpu_user=1000 + i * 100,
            cpu_system=200 + i * 30,
            cpu_iowait=50 + i * 40,  # iowait 단조 증가
            cpu_idle=8000 + i * 600,
            disk_io=[
                DiskIoEntry(
                    device="sda",
                    reads_completed=100 + i * 50,  # 분당 50 IOPS reads
                    writes_completed=50 + i * 30,  # 분당 30 IOPS writes
                    sectors_read=2000 + i * 1000,  # 분당 500 sectors = 256000 bytes
                    sectors_written=1000 + i * 500,
                ),
            ],
            mounts=[
                MountUsageEntry(
                    mount="/data",
                    total_bytes=100 * 10**9,
                    free_bytes=(50 - i * 2) * 10**9,
                    avail_bytes=(50 - i * 2) * 10**9,  # 분당 2GB 채움 -> 25일 후 full
                ),
            ],
            net_io=[
                NetIoEntry(
                    interface="eth0",
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


async def test_report_aggregate_returns_iowait_and_inventory(collect_repo, query_repo):
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
    assert r.mem_total_kb == 8 * 1024 * 1024
    assert r.disks and r.disks[0]["size_bytes"] == 100 * 10**9
    assert r.boot_time is not None


# ─── report_mount_worst — fill_rate 추정 ─────────────────────────────────


async def test_report_mount_worst_estimates_days_until_full(collect_repo, query_repo):
    sid, _start, end = await _seed_server_with_period_metrics(
        collect_repo,
        "r-mount",
        n_points=10,
    )
    mount_map = await query_repo.report_mount_worst(
        [sid],
        period_days=1,
        end=end + timedelta(minutes=1),
    )
    assert sid in mount_map
    mount, used_pct, days_until_full = mount_map[sid]
    assert mount == "/data"
    assert used_pct is not None and used_pct >= 50.0  # 50% 이상 사용 중
    # 분당 2GB 채움 + 잔여 약 32GB -> 매우 짧은 시간 안 full. period_days=1 기준 fill_rate라 days >= 0
    assert days_until_full is not None and days_until_full >= 0


async def test_report_mount_worst_no_consumption_returns_none(collect_repo, query_repo):
    """avail_bytes 변동 없음 -> fill_rate 추정 불가 -> days_until_full None."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-mnt-stable"))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
    for i in range(5):
        m = make_metrics(
            collected_at=base_ts + timedelta(minutes=i),
            mounts=[
                MountUsageEntry(
                    mount="/",
                    total_bytes=100 * 10**9,
                    free_bytes=30 * 10**9,
                    avail_bytes=30 * 10**9,  # 고정
                )
            ],
        )
        await collect_repo.record_metrics(sid, m)

    mount_map = await query_repo.report_mount_worst(
        [sid],
        period_days=1,
        end=base_ts + timedelta(hours=1),
    )
    if sid in mount_map:
        _mount, _used, days = mount_map[sid]
        assert days is None


# ─── report_uptime_stats — boot_time DISTINCT count - 1 ──────────────────


async def test_report_uptime_stats_no_reboot(collect_repo, query_repo):
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-up-stable")
    counts = await query_repo.report_uptime_stats(
        [sid],
        period_days=1,
        end=end + timedelta(minutes=1),
    )
    # period 안 boot_time 1개 (DISTINCT count = 1) -> 0회 재부팅
    assert counts.get(sid, 0) == 0


async def test_report_uptime_stats_counts_reboot_transitions(collect_repo, query_repo):
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


async def test_report_disk_io_baseline_iops_and_throughput(collect_repo, query_repo):
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
    iops, throughput_kbps, iops_p95, iops_peak, kbps_p95, kbps_peak = io_map[sid]
    # 분당 50+30=80 ops + 1분 간격 -> IOPS = 80/60 ~= 1.33. floor int 변환
    assert iops is not None and iops >= 1
    assert throughput_kbps is not None and throughput_kbps > 0
    # p95/peak는 시점 rate 기반 — baseline 이상 (모든 시점 동일 rate라 p95 ≈ peak ≈ baseline)
    assert iops_p95 is not None and iops_p95 > 0
    assert iops_peak is not None and iops_peak >= iops_p95
    assert kbps_p95 is not None and kbps_p95 > 0
    assert kbps_peak is not None and kbps_peak >= kbps_p95


async def test_report_disk_io_baseline_missing_data_returns_empty(collect_repo, query_repo):
    """metric 없는 서버 -> dict에서 누락."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-disk-empty"))
    io_map = await query_repo.report_disk_io_baseline(
        [sid],
        period_days=1,
        end=datetime.now(UTC),
    )
    assert sid not in io_map


# ─── report_net_io_baseline — rx/tx kbps ─────────────────────────────────


async def test_report_net_io_baseline_rx_tx(collect_repo, query_repo):
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
    rx_kbps, tx_kbps, rx_p95, rx_peak, tx_p95, tx_peak = net_map[sid]
    # 분당 60000 bytes / 60s / 1024 ~= 0.97 kB/s rx
    assert rx_kbps is not None and rx_kbps > 0
    assert tx_kbps is not None and tx_kbps > 0
    assert rx_kbps > tx_kbps  # rx 60KB/min > tx 30KB/min
    assert rx_p95 is not None and rx_peak is not None and rx_peak >= rx_p95
    assert tx_p95 is not None and tx_peak is not None and tx_peak >= tx_p95


# ─── 합산: 5 SQL이 같은 server_ids·period 입력 일관 동작 ──────────────────


async def test_all_report_queries_share_server_ids_and_period(collect_repo, query_repo):
    """get_report·get_inventory_export가 호출하는 5개 SQL이 같은 입력으로 정합 결과."""
    sid, _start, end = await _seed_server_with_period_metrics(
        collect_repo,
        "r-combo",
        n_points=10,
        interval_min=1,
    )
    end_q = end + timedelta(minutes=1)
    raws = await query_repo.report_aggregate([sid], period_days=1, end=end_q)
    mount = await query_repo.report_mount_worst([sid], period_days=1, end=end_q)
    uptime = await query_repo.report_uptime_stats([sid], period_days=1, end=end_q)
    disk_io = await query_repo.report_disk_io_baseline([sid], period_days=1, end=end_q)
    net_io = await query_repo.report_net_io_baseline([sid], period_days=1, end=end_q)

    assert len(raws) == 1
    assert sid in mount
    assert sid in disk_io
    assert sid in net_io
    # uptime은 boot_time 변경 없으면 0 또는 누락 (둘 다 reboot_count=0 의미)
    assert uptime.get(sid, 0) == 0


# ─── 개별 보고서 심화 — 마운트별 / 메모리 구성 / CPU 분류 ──────────────────


async def test_report_mount_usage_returns_all_mounts_avg_pct(collect_repo, query_repo):
    """worst 1개가 아닌 전체 마운트의 윈도우 평균 사용률 (avail 단조감소 -> 평균 50% 이상)."""
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-mnt-usage")
    vols = await query_repo.report_mount_usage(sid, period_days=1, end=end + timedelta(minutes=1))
    assert len(vols) == 1
    v = vols[0]
    assert v.mount == "/data"
    assert v.total_bytes == 100 * 10**9
    assert v.used_pct is not None and v.used_pct >= 50.0


async def test_report_memory_breakdown_pct_split(collect_repo, query_repo):
    """used/available/cached/buffers 윈도우 평균 (전체 메모리 대비). 기본 mem 8GB·available 5GB."""
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-mem-bd")
    mb = await query_repo.report_memory_breakdown(sid, period_days=1, end=end + timedelta(minutes=1))
    # mem_total=8GB, available=5GB -> used 37.5% / available 62.5%, cached 1GB=12.5%, buffers 200MB~2.4%
    assert mb.used_pct == pytest.approx(37.5, abs=0.1)
    assert mb.available_pct == pytest.approx(62.5, abs=0.1)
    assert mb.cached_pct == pytest.approx(12.5, abs=0.1)
    assert mb.buffers_pct is not None and 0 < mb.buffers_pct < 5


async def test_report_cpu_breakdown_delta_split(collect_repo, query_repo):
    """user/system/iowait LAG delta 비율. step delta user 100·system 30·iowait 40·idle 600 -> total 770."""
    sid, _start, end = await _seed_server_with_period_metrics(collect_repo, "r-cpu-bd")
    cb = await query_repo.report_cpu_breakdown(sid, period_days=1, end=end + timedelta(minutes=1))
    assert cb.user_pct == pytest.approx(100 / 770 * 100, abs=0.5)
    assert cb.system_pct == pytest.approx(30 / 770 * 100, abs=0.5)
    assert cb.iowait_pct == pytest.approx(40 / 770 * 100, abs=0.5)


# ─── cagg counter reset 처리 (ADR 0043 — counter_agg 정석) ────────────────────


async def test_report_aggregate_counter_reset_segments_summed(collect_repo, query_repo):
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
                cpu_user=user,
                cpu_system=0,
                cpu_idle=idle,
                cpu_iowait=0,
                mounts=[],
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


async def test_report_disk_io_baseline_counter_reset_segments_summed(collect_repo, query_repo):
    """disk reads/writes 카운터 reset(재부팅)을 server_disk_io_5m cagg counter_agg 가 일률 흡수.

    기존 LAG baseline 은 boot_time gate 없이 delta>=0 만이라 reset 처리가 CPU 와 불일치했다(ADR 0043 weirdness).
    counter_agg 는 reset 전후 단조 세그먼트를 합산 — IOPS baseline 이 reset 점프로 왜곡되지 않는다.
    """
    sid = await collect_repo.upsert_server(make_inventory(composite_id="r-disk-reset"))
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    base = now - timedelta(minutes=12)
    base = base - timedelta(minutes=base.minute % 5) + timedelta(seconds=30)
    # reads 누적 분당 약 100/min(= delta 50/30s). reset at idx 3 (값 감소). 한 5분 버킷.
    reads = [0, 50, 100, 150, 0, 50, 100]  # idx 3->4 에서 150->0 감소(reset)
    for i, r in enumerate(reads):
        await collect_repo.record_metrics(
            sid,
            make_metrics(
                collected_at=base + timedelta(seconds=30 * i),
                disk_io=[
                    DiskIoEntry(
                        device="sda",
                        reads_completed=r,
                        writes_completed=0,
                        sectors_read=r * 8,
                        sectors_written=0,
                    )
                ],
                mounts=[],
                net_io=[],
            ),
        )
    d = await query_repo.report_disk_io_baseline([sid], 1, now)
    assert sid in d
    iops_baseline = d[sid][0]
    # counter_agg: reads delta = (150-0)+(100-0)=250 over ~5분 관측. naive last-first=100-0=100 이면 과소.
    # reset 음수 점프(150->0)가 IOPS 를 음수/0 으로 왜곡하지 않고 양수 baseline 산출.
    assert iops_baseline is not None and iops_baseline > 0
