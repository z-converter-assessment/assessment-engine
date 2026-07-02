"""QueryRepository 통합 테스트 — Phase 1 정확화 검증.

검증 영역:
- inventory query (resolve_server_id, list_servers, get_server)
- _latest_per_dimension n=1/n=2 (get_storage, get_network, latest_dashboard)
- collection_status
- metric_chart dispatcher (17개 metric_type 모두 dispatch + 4개 helper SQL 정상 실행)
- metric_chart helper 결과 정확성 (cpu_delta LAG 계산, fs_usage 시점 값, rate_per_dim)
"""

from datetime import UTC, datetime, timedelta

import pytest

from assessment_engine.db.dtos.inbound import (
    DiskIoEntry,
    MountUsageEntry,
    NetIoEntry,
)
from assessment_engine.db.repositories.collect_repository import CollectRepository
from assessment_engine.db.repositories.query.query_repository import QueryRepository
from tests.factories import agent_id_for, make_inventory, make_metrics


def _bucket_aligned_base(minutes_ago: int = 7) -> datetime:
    """5분 버킷 시작에 정렬된 과거 시각. server_metrics_5m 등 cagg 가 counter_agg delta 를 내려면 같은 5분
    버킷에 표본 2+ 가 필요 — 1분 간격 표본이 버킷 경계에 갈리지 않도록 base 를 버킷 시작에 맞춘다(ADR 0043)."""
    t = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).replace(second=0, microsecond=0)
    return t - timedelta(minutes=t.minute % 5)


pytestmark = pytest.mark.asyncio


_ALL_METRIC_TYPES = [
    "cpu.usage_percent",
    "cpu.user_percent",
    "cpu.system_percent",
    "cpu.iowait_percent",
    "load.1m",
    "load.5m",
    "load.15m",
    "mem.usage_percent",
    "mem.available_percent",
    "mem.cached_percent",
    "mem.buffers_percent",
    "swap.usage_percent",
    "disk.read_iops",
    "disk.write_iops",
    "disk.queue",
    "fs.usage_percent",
    "net.rx_bytes_per_sec",
    "net.tx_bytes_per_sec",
]


async def _seed_one_server_with_metrics(
    collect_repo: CollectRepository,
    composite_id: str = "q-001",
    n_points: int = 3,
) -> tuple[int, datetime]:
    """공통 fixture helper — server 1대 + n_points 시점의 metrics 시계열."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id=composite_id))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=n_points)

    for i in range(n_points):
        ts = base_ts + timedelta(minutes=i)
        m = make_metrics(
            collected_at=ts,
            # CPU jiffies 누적 — LAG delta가 양수가 되도록 시점마다 증가
            cpu_user=1000 + i * 100,
            cpu_idle=8000 + i * 800,
            disk_io=[
                DiskIoEntry(
                    device="sda",
                    reads_completed=100 + i * 50,
                    writes_completed=50 + i * 25,
                    sectors_read=2000 + i * 1000,
                    sectors_written=1000 + i * 500,
                ),
            ],
            mounts=[
                MountUsageEntry(
                    mount="/", total_bytes=50_000_000_000, free_bytes=20_000_000_000, avail_bytes=18_000_000_000
                ),
            ],
            net_io=[
                NetIoEntry(
                    interface="eth0",
                    rx_bytes=1_000_000 + i * 100_000,
                    tx_bytes=500_000 + i * 50_000,
                    rx_packets=1000 + i * 100,
                    tx_packets=500 + i * 50,
                    rx_errors=0,
                    tx_errors=0,
                ),
            ],
        )
        await collect_repo.record_metrics(sid, m)

    return sid, base_ts


# ─── inventory ────────────────────────────────────────────────────────────


async def test_resolve_server_id_existing(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-resolve-1"))
    inv_row = (
        await collect_repo.session.execute(
            __import__("sqlalchemy").text("SELECT public_id FROM server_inventory WHERE id = :id"),
            {"id": sid},
        )
    ).scalar_one()
    resolved = await query_repo.resolve_server_id(str(inv_row))
    assert resolved == sid


async def test_resolve_server_id_missing(query_repo: QueryRepository):
    assert await query_repo.resolve_server_id("00000000-0000-0000-0000-000000000000") is None


async def test_list_servers_returns_with_last_seen_at(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """list_servers DTO에 last_seen_at 포함 — Redis fail-open fallback용."""
    await collect_repo.upsert_server(make_inventory(composite_id="q-list-1", hostname="host-a"))
    await collect_repo.upsert_server(make_inventory(composite_id="q-list-2", hostname="host-b"))
    rows = await query_repo.list_servers(page=1, limit=10, search=None)
    assert len(rows) >= 2
    for r in rows:
        assert r.last_seen_at is not None  # collected_at으로 자동 채워짐


async def test_list_servers_search_filter(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    await collect_repo.upsert_server(make_inventory(composite_id="q-srch-1", hostname="alpha-server"))
    await collect_repo.upsert_server(make_inventory(composite_id="q-srch-2", hostname="beta-server"))
    rows = await query_repo.list_servers(page=1, limit=10, search="alpha")
    hostnames = [r.hostname for r in rows]
    assert any("alpha" in h for h in hostnames)
    assert not any("beta" in h for h in hostnames)


async def test_get_server_returns_full_detail(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    sid = await collect_repo.upsert_server(
        make_inventory(composite_id="q-detail-1", hostname="detail-host", cpu_cores=12)
    )
    detail = await query_repo.get_server(sid)
    assert detail is not None
    assert detail.hostname == "detail-host"
    assert detail.cpu_cores == 12


async def test_get_server_missing(query_repo: QueryRepository):
    assert await query_repo.get_server(999_999) is None


# ─── _latest_per_dimension (n=1) — get_storage ────────────────────────────


async def test_get_storage_returns_only_latest_per_mount(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """같은 mount의 여러 시점 데이터 중 최신 1행만 반환 (DISTINCT ON)."""
    sid, _ = await _seed_one_server_with_metrics(collect_repo, composite_id="q-stor-1", n_points=3)
    storage = await query_repo.get_storage(sid)
    assert storage is not None
    # 시드는 mount="/" 1개만. 여러 시점이라도 mount당 1행.
    assert len(storage.mount_usage) == 1
    assert storage.mount_usage[0].mount == "/"


# ─── _latest_per_dimension (n=2) — get_network ────────────────────────────


async def test_get_network_returns_at_most_2_per_interface(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """delta 계산용 — interface당 최신 2행 (PARTITION BY + ROW_NUMBER)."""
    sid, _ = await _seed_one_server_with_metrics(collect_repo, composite_id="q-net-1", n_points=5)
    network = await query_repo.get_network(sid)
    assert network is not None
    # 시드는 interface="eth0" 1개. 5시점이지만 최신 2행.
    eth0_rows = [r for r in network.net_io if r.interface == "eth0"]
    assert len(eth0_rows) == 2


# ─── collection_status ────────────────────────────────────────────────────


async def test_collection_status_reports_both_timestamps(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, composite_id="q-cs-1", n_points=2)
    status = await query_repo.get_collection_status(sid)
    assert status is not None
    assert status.last_inventory_at is not None
    assert status.last_metric_at is not None


async def test_collection_status_missing_server(query_repo: QueryRepository):
    assert await query_repo.get_collection_status(999_999) is None


# ─── latest_dashboard — 3번의 _latest_per_dimension 통합 ──────────────────


async def test_latest_dashboard_returns_all_four_blocks(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    sid, _ = await _seed_one_server_with_metrics(collect_repo, composite_id="q-dash-1", n_points=3)
    dash = await query_repo.latest_dashboard(sid)
    assert dash is not None
    # server_metrics는 최신 2행 (delta 계산용)
    assert len(dash.metrics) == 2
    # disk_io: device당 최신 2행 (1개 device x 2 = 2)
    assert len(dash.disk_io) == 2
    # net_io: interface당 최신 2행
    assert len(dash.net_io) == 2
    # mount_usage: mount당 1행
    assert len(dash.mounts) == 1


async def test_latest_dashboard_missing_server(query_repo: QueryRepository):
    assert await query_repo.latest_dashboard(999_999) is None


async def test_latest_dashboard_skips_future_timestamp_rows(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """미래 timestamp 행 방어 — 시계 어긋난 agent 가 미래 collected_at 으로 발행해도 그 행을 "최신"으로
    잡지 않는다. 미래행이 cur 를 가로채면 CPU delta(연속 2행)가 깨지므로, latest_dashboard 는 now()
    이하 행만 본다 (server_metrics + disk_io/net_io/mount 모든 블록 동일 정책)."""
    sid, _ = await _seed_one_server_with_metrics(collect_repo, composite_id="q-future-1", n_points=3)
    # 시계가 +7시간 튄 미래행 1개 추가 (Windows RTC=local TZ 해석 재현)
    future_ts = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=7)
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=future_ts,
            cpu_user=9999,
            cpu_idle=99999,
            disk_io=[
                DiskIoEntry(
                    device="sda", reads_completed=9999, writes_completed=9999, sectors_read=9999, sectors_written=9999
                )
            ],
            mounts=[MountUsageEntry(mount="/", total_bytes=50_000_000_000, free_bytes=1, avail_bytes=1)],
            net_io=[
                NetIoEntry(
                    interface="eth0",
                    rx_bytes=9_999_999,
                    tx_bytes=9_999_999,
                    rx_packets=9999,
                    tx_packets=9999,
                    rx_errors=0,
                    tx_errors=0,
                )
            ],
        ),
    )
    dash = await query_repo.latest_dashboard(sid)
    assert dash is not None
    # 미래행 제외 후에도 정상 최신 2행 — 그리고 cur(최신)가 미래행이 아니어야 한다
    assert len(dash.metrics) == 2
    assert all(m.collected_at < future_ts for m in dash.metrics)
    # disk_io/net_io/mount 도 미래행을 최신으로 잡지 않음
    assert all(d.collected_at < future_ts for d in dash.disk_io)
    assert all(n.collected_at < future_ts for n in dash.net_io)
    assert all(mu.collected_at < future_ts for mu in dash.mounts)


# ─── metric_chart dispatcher — 17개 metric_type 모두 무사 dispatch ────────


@pytest.mark.parametrize("metric_type", _ALL_METRIC_TYPES)
async def test_metric_chart_dispatcher_all_types(
    metric_type: str,
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """모든 metric_type이 dispatcher를 통과하고 SQL이 정상 실행 — 결과는 비어도 OK."""
    sid, _ = await _seed_one_server_with_metrics(collect_repo, composite_id=f"q-mc-{metric_type}", n_points=3)
    rows = await query_repo.metric_chart(
        server_id=sid,
        metric_type=metric_type,
        dimension=None,
        time_range="1h",
        bucket="5m",
        agg="avg",
        end=None,
    )
    assert isinstance(rows, list)


# ─── metric_chart helper 결과 정확성 ──────────────────────────────────────


async def test_metric_chart_cpu_usage_percent_returns_data(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """_chart_cpu_delta — n_points=3이면 LAG로 d_total > 0인 행 2개 → 시간 버킷 >= 1."""
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, composite_id="q-cpu-1", n_points=3)
    end = base_ts + timedelta(minutes=10)
    rows = await query_repo.metric_chart(
        server_id=sid,
        metric_type="cpu.usage_percent",
        dimension=None,
        time_range="15m",
        bucket="1m",
        agg="avg",
        end=end,
    )
    # delta 가능한 시점이 >= 1 → 적어도 1개 버킷
    assert len(rows) >= 1
    for r in rows:
        if r.value is not None:
            assert 0 <= r.value <= 100


async def test_metric_chart_disk_queue_returns_windows_gauge(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """disk.queue — Windows sat_disk_queue gauge 를 서버간 AVG 후 time_bucket agg. 시드 큐 깊이 반영."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-dq-1"))
    base = _bucket_aligned_base()
    for i in range(2):
        await collect_repo.record_metrics(
            sid, make_metrics(collected_at=base + timedelta(minutes=i), sat_disk_queue=3.0 + i)
        )
    rows = await query_repo.metric_chart(
        server_id=sid, metric_type="disk.queue", dimension=None,
        time_range="1h", bucket="5m", agg="avg", end=base + timedelta(minutes=10),
    )
    vals = [r.value for r in rows if r.value is not None]
    assert vals, "sat_disk_queue 값이 있으면 disk.queue 추이가 값을 반환해야 함"
    assert all(v >= 3.0 for v in vals)  # 시드 큐 깊이(3.0, 4.0) 반영 — AVG


async def test_metric_chart_disk_queue_excludes_null_linux(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """disk.queue — sat_disk_queue null(Linux, iowait 사용) 서버는 IS NOT NULL 필터로 제외 → 값 없음."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-dq-null"))
    base = _bucket_aligned_base()
    for i in range(2):
        # sat_disk_queue 미지정 → None (Linux 는 disk_queue 미발행)
        await collect_repo.record_metrics(sid, make_metrics(collected_at=base + timedelta(minutes=i)))
    rows = await query_repo.metric_chart(
        server_id=sid, metric_type="disk.queue", dimension=None,
        time_range="1h", bucket="5m", agg="avg", end=base + timedelta(minutes=10),
    )
    assert all(r.value is None for r in rows)  # null 제외 → 값 산출 안 됨 (빈 결과 또는 value None)


async def test_metric_chart_fs_usage_returns_per_mount(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """_chart_fs — dimension=mount, 시점 값. 각 행에 dimension 채워짐."""
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, composite_id="q-fs-1", n_points=2)
    end = base_ts + timedelta(minutes=10)
    rows = await query_repo.metric_chart(
        server_id=sid,
        metric_type="fs.usage_percent",
        dimension=None,
        time_range="15m",
        bucket="1m",
        agg="avg",
        end=end,
    )
    assert len(rows) >= 1
    for r in rows:
        assert r.dimension == "/"
        if r.value is not None:
            assert 0 <= r.value <= 100


async def test_metric_chart_disk_read_iops_per_device(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """_chart_rate_per_dimension — disk_io의 LAG/dt 기반 IOPS. dimension=device 채워짐."""
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, composite_id="q-dio-1", n_points=3)
    end = base_ts + timedelta(minutes=10)
    rows = await query_repo.metric_chart(
        server_id=sid,
        metric_type="disk.read_iops",
        dimension=None,
        time_range="15m",
        bucket="1m",
        agg="avg",
        end=end,
    )
    assert len(rows) >= 1
    for r in rows:
        assert r.dimension == "sda"
        if r.value is not None:
            assert r.value >= 0  # 음수 IOPS 없음


async def test_metric_chart_cpu_excludes_boot_time_change_point(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """metric_chart(metric_trend 위임) — boot_time jitter 초과 변경 시점은 reset 확정 → 차트 missing (CLAUDE.md B1).

    재부팅 후 jiffies는 0부터 시작이지만 드물게 prev보다 큰 값일 수도. 옛 d<0 휴리스틱은
    못 잡지만 boot_time 비교는 spike 방지.
    """
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-rst-cpu-1"))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=10)
    boot_a = datetime(2026, 5, 9, 10, 0, tzinfo=UTC)
    boot_b = datetime(2026, 5, 9, 11, 0, tzinfo=UTC)

    # 시점 0~1: boot_a 정상 누적 / 시점 2: boot_b + 양수 d (재부팅 후 큰 값으로 가정)
    # 옛 휴리스틱(d<0)으론 못 잡고 boot_time 비교만 잡는 케이스
    cases = [(0, boot_a, 1000, 8000), (5, boot_a, 1100, 8800), (10, boot_b, 2000, 16000)]
    for offset, bt, cu, ci in cases:
        await collect_repo.record_metrics(
            sid,
            make_metrics(
                collected_at=base_ts + timedelta(minutes=offset),
                boot_time=bt,
                agent_started_at=bt + timedelta(seconds=10),
                cpu_user=cu,
                cpu_idle=ci,
            ),
        )

    end = base_ts + timedelta(minutes=15)
    rows = await query_repo.metric_chart(
        server_id=sid,
        metric_type="cpu.usage_percent",
        dimension=None,
        time_range="1h",
        bucket="1m",
        agg="avg",
        end=end,
    )
    # reset 처리됐으면 시점 2(boot_b 첫 측정)는 NULL → 그 버킷 차트에서 제외.
    # 시점 1은 정상 (boot_a 동일) → 정상 percent. 결과 >= 1행, 모두 0~100.
    assert all(0 <= r.value <= 100 for r in rows if r.value is not None)
    reset_bucket_ts = base_ts + timedelta(minutes=10)
    reset_bucket_in_result = any(r.collected_at.replace(tzinfo=UTC) == reset_bucket_ts.replace(second=0) for r in rows)
    assert not reset_bucket_in_result, "reset 시점 차트에 포함됨 — boot_time 비교 미적용"


async def test_metric_chart_rate_excludes_boot_time_change_point(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """_chart_rate_per_dimension — boot_time 변경 시 d_val 양수여도 reset 확정 → 차트 missing."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-rst-rate-1"))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=10)
    boot_a = datetime(2026, 5, 9, 10, 0, tzinfo=UTC)
    boot_b = datetime(2026, 5, 9, 11, 0, tzinfo=UTC)

    # 시점 2의 d_val 양수(100→300)지만 boot_time 변경 → reset 확정
    cases = [(0, boot_a, 100), (5, boot_a, 200), (10, boot_b, 300)]
    for offset, bt, reads in cases:
        await collect_repo.record_metrics(
            sid,
            make_metrics(
                collected_at=base_ts + timedelta(minutes=offset),
                boot_time=bt,
                agent_started_at=bt + timedelta(seconds=10),
                disk_io=[
                    DiskIoEntry(
                        device="sda", reads_completed=reads, writes_completed=0, sectors_read=0, sectors_written=0
                    )
                ],
                mounts=[],
                net_io=[],
            ),
        )

    end = base_ts + timedelta(minutes=15)
    rows = await query_repo.metric_chart(
        server_id=sid,
        metric_type="disk.read_iops",
        dimension="sda",
        time_range="1h",
        bucket="1m",
        agg="avg",
        end=end,
    )
    # 시점 2의 ts 버킷이 결과에 없어야 함 (reset 처리됐다면)
    reset_bucket_ts = (base_ts + timedelta(minutes=10)).replace(second=0)
    reset_bucket_in_result = any(r.collected_at.replace(tzinfo=UTC) == reset_bucket_ts for r in rows)
    assert not reset_bucket_in_result, "rate 차트가 reset 시점 spike 표시 — boot_time 비교 미적용"
    # 그 외 행은 비음수 (음수 IOPS는 옛 휴리스틱이 이미 거름)
    assert all(r.value >= 0 for r in rows if r.value is not None)


async def test_reboot_events_classifies_boot_time_change_as_reboot(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """server_inventory_history에 boot_time 변경 시점 → kind='reboot'."""
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    boot_a = datetime(2026, 5, 9, 10, 0, tzinfo=UTC)
    boot_b = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    agent_a = boot_a + timedelta(seconds=10)
    agent_b = boot_b + timedelta(seconds=10)

    # upsert_server가 _inventory_changed 감지 + history append. boot_time 변경이 trigger.
    await collect_repo.upsert_server(
        make_inventory(
            composite_id="q-rb-1",
            collected_at=base_ts,
            boot_time=boot_a,
            agent_started_at=agent_a,
        )
    )
    await collect_repo.upsert_server(
        make_inventory(
            composite_id="q-rb-1",
            collected_at=base_ts + timedelta(hours=1),
            boot_time=boot_b,
            agent_started_at=agent_b,
        )
    )

    # agent_id 단일 키 (#C1) — make_inventory 가 composite_id 라벨로 파생한 값과 일치.
    sid = await collect_repo.find_server_id(agent_id_for("q-rb-1"))
    events = await query_repo.reboot_events(
        sid,
        start=base_ts - timedelta(minutes=1),
        end=base_ts + timedelta(hours=2),
    )
    # 첫 등록(prev_boot=NULL → reboot) + boot_time 변경(reboot) → 2건
    assert len(events) == 2
    assert all(ev.kind == "reboot" for ev in events)


async def test_reboot_events_classifies_agent_only_change_as_restart(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """boot_time 동일 + agent_started_at만 변경 → kind='restart'."""
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    boot = datetime(2026, 5, 9, 10, 0, tzinfo=UTC)
    agent_a = boot + timedelta(seconds=10)
    agent_b = boot + timedelta(minutes=30)  # 같은 부팅, 에이전트만 재시작

    await collect_repo.upsert_server(
        make_inventory(
            composite_id="q-rb-2",
            collected_at=base_ts,
            boot_time=boot,
            agent_started_at=agent_a,
        )
    )
    await collect_repo.upsert_server(
        make_inventory(
            composite_id="q-rb-2",
            collected_at=base_ts + timedelta(hours=1),
            boot_time=boot,
            agent_started_at=agent_b,
        )
    )

    # agent_id 단일 키 (#C1) — make_inventory 가 composite_id 라벨로 파생한 값과 일치.
    sid = await collect_repo.find_server_id(agent_id_for("q-rb-2"))
    events = await query_repo.reboot_events(
        sid,
        start=base_ts - timedelta(minutes=1),
        end=base_ts + timedelta(hours=2),
    )
    # 첫 등록(reboot) + agent 변경(restart) → 2건
    assert len(events) == 2
    assert events[0].kind == "reboot"
    assert events[1].kind == "restart"


async def test_metric_chart_dimension_filter(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """dimension 파라미터로 특정 device만 필터."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-dim-1"))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=3)
    for i in range(3):
        await collect_repo.record_metrics(
            sid,
            make_metrics(
                collected_at=base_ts + timedelta(minutes=i),
                disk_io=[
                    DiskIoEntry(
                        device="sda",
                        reads_completed=100 + i * 10,
                        writes_completed=0,
                        sectors_read=0,
                        sectors_written=0,
                    ),
                    DiskIoEntry(
                        device="sdb",
                        reads_completed=200 + i * 20,
                        writes_completed=0,
                        sectors_read=0,
                        sectors_written=0,
                    ),
                ],
                mounts=[],
                net_io=[],
            ),
        )
    end = base_ts + timedelta(minutes=10)
    rows_sda = await query_repo.metric_chart(
        server_id=sid,
        metric_type="disk.read_iops",
        dimension="sda",
        time_range="15m",
        bucket="1m",
        agg="avg",
        end=end,
    )
    rows_all = await query_repo.metric_chart(
        server_id=sid,
        metric_type="disk.read_iops",
        dimension=None,
        time_range="15m",
        bucket="1m",
        agg="avg",
        end=end,
    )
    assert all(r.dimension == "sda" for r in rows_sda)
    assert any(r.dimension == "sdb" for r in rows_all)


# ─── metric_snapshots ─────────────────────────────────────────────────────


async def test_metric_snapshots_returns_timestamps(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    sid, _ = await _seed_one_server_with_metrics(collect_repo, composite_id="q-snap-1", n_points=5)
    rows = await query_repo.metric_snapshots(sid, cursor=None, limit=10)
    assert len(rows) == 5
    for r in rows:
        assert r.collected_at is not None
        assert r.value is None and r.dimension is None  # timestamp 목록만


async def test_metric_snapshots_cursor_pagination(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """cursor 이전 timestamps만."""
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, composite_id="q-snap-2", n_points=4)
    cursor = base_ts + timedelta(minutes=2)
    rows = await query_repo.metric_snapshots(sid, cursor=cursor, limit=10)
    assert all(r.collected_at < cursor for r in rows)


# ─── batch resolve / get_servers (C5 N+1 회피) ────────────────────────────


async def test_resolve_server_ids_batch_returns_dict(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
    db_session,
):
    """N개 public_id → {public_id: server_id} 단일 SQL."""
    sid_a = await collect_repo.upsert_server(make_inventory(composite_id="q-batch-a"))
    sid_b = await collect_repo.upsert_server(make_inventory(composite_id="q-batch-b"))
    pid_a = (
        await db_session.execute(
            __import__("sqlalchemy").text("SELECT public_id FROM server_inventory WHERE id=:i"),
            {"i": sid_a},
        )
    ).scalar_one()
    pid_b = (
        await db_session.execute(
            __import__("sqlalchemy").text("SELECT public_id FROM server_inventory WHERE id=:i"),
            {"i": sid_b},
        )
    ).scalar_one()
    result = await query_repo.resolve_server_ids([str(pid_a), str(pid_b)])
    assert result == {str(pid_a): sid_a, str(pid_b): sid_b}


async def test_resolve_server_ids_skips_missing(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
    db_session,
):
    """미존재 public_id는 dict에서 누락 — caller가 missing 분기."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-batch-missing"))
    pid = (
        await db_session.execute(
            __import__("sqlalchemy").text("SELECT public_id FROM server_inventory WHERE id=:i"),
            {"i": sid},
        )
    ).scalar_one()
    fake_pid = "00000000-0000-0000-0000-000000000000"
    result = await query_repo.resolve_server_ids([str(pid), fake_pid])
    assert str(pid) in result
    assert fake_pid not in result


async def test_resolve_server_ids_empty_input(query_repo: QueryRepository):
    """빈 입력 → 빈 dict (DB 쿼리 0건)."""
    assert await query_repo.resolve_server_ids([]) == {}


async def test_get_servers_batch_returns_all_details(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """N개 server_id → N개 ServerDetail 단일 SQL."""
    sid_a = await collect_repo.upsert_server(make_inventory(composite_id="q-gs-a", hostname="host-a"))
    sid_b = await collect_repo.upsert_server(make_inventory(composite_id="q-gs-b", hostname="host-b"))
    details = await query_repo.get_servers([sid_a, sid_b])
    assert len(details) == 2
    by_host = {d.hostname: d for d in details}
    assert by_host["host-a"].id == sid_a
    assert by_host["host-b"].id == sid_b


async def test_get_servers_skips_missing(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """미존재 server_id는 결과에서 누락 — caller가 dict로 매핑."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-gs-missing"))
    details = await query_repo.get_servers([sid, 999_999])
    assert len(details) == 1
    assert details[0].id == sid


async def test_get_servers_empty_input(query_repo: QueryRepository):
    """빈 입력 → 빈 list (DB 쿼리 0건)."""
    assert await query_repo.get_servers([]) == []


# ─── partition pruning (C5 _latest_per_dimension 30d 윈도우) ──────────────


async def test_latest_per_dimension_excludes_data_older_than_30d(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """_latest_per_dimension은 30d 윈도우. 31일 전 mount 데이터는 get_storage 결과에서 제외."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-prune-1"))
    old_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(days=31)

    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=old_ts,
            mounts=[MountUsageEntry(mount="/old", total_bytes=10**12, free_bytes=10**11, avail_bytes=10**11)],
            disk_io=[],
            net_io=[],
        ),
    )
    storage = await query_repo.get_storage(sid)
    assert storage is not None
    mount_names = [m.mount for m in storage.mount_usage]
    assert "/old" not in mount_names, "30d 이상 오래된 mount가 결과에 포함됨 — partition pruning 미적용"


# ─── attention 신호: metric_gap_warnings (통신 끊김 운영신호) ──────────────
# disk_usage_warnings 는 운영신호에서 USE Method 로 이동(코드 제거) — 관련 테스트 삭제됨.


async def test_metric_gap_warnings_excludes_recent_metric(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """방금 metric 발행한 서버는 갭 없음 — 결과 제외."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-gap-fresh", hostname="fresh-host"))
    await collect_repo.record_metrics(sid, make_metrics(collected_at=datetime.now(UTC)))
    rows = await query_repo.metric_gap_warnings(gap_minutes=5, recent_hours=24, limit=10)
    assert all(r.hostname != "fresh-host" for r in rows)


async def test_metric_gap_warnings_includes_gap_in_window(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """5분~24h 윈도우 안에 마지막 metric → 결과 포함."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-gap-mid", hostname="gap-host"))
    last_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=10)
    await collect_repo.record_metrics(sid, make_metrics(collected_at=last_ts))
    rows = await query_repo.metric_gap_warnings(gap_minutes=5, recent_hours=24, limit=10)
    matching = [r for r in rows if r.hostname == "gap-host"]
    assert len(matching) == 1
    assert matching[0].last_metric_at == last_ts


async def test_metric_gap_warnings_excludes_dead_server(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """24h 이상 metric 없는 dead 서버는 갭 결과 제외 (한때 살아있던 서버 대상이 아님)."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-gap-dead", hostname="dead-host"))
    long_ago = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=48)
    await collect_repo.record_metrics(sid, make_metrics(collected_at=long_ago))
    rows = await query_repo.metric_gap_warnings(gap_minutes=5, recent_hours=24, limit=10)
    assert all(r.hostname != "dead-host" for r in rows)


async def test_metric_gap_warnings_no_metric_excluded(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """metric 한 번도 발행 안 한 서버 — JOIN 조건으로 제외."""
    await collect_repo.upsert_server(make_inventory(composite_id="q-gap-none", hostname="never-host"))
    rows = await query_repo.metric_gap_warnings(gap_minutes=5, recent_hours=24, limit=10)
    assert all(r.hostname != "never-host" for r in rows)


# ─── latest_disk_max_pct는 2026-05-12 cleanup으로 제거됨 ────────────────
# risk_top 카드 dead code화 결과. mount 사용률 신호는 report_mount_worst로 흡수
# (tests/integration/test_query_repository_report.py).


# ─── environment_utilization ──────────────────────────────────────────────


async def test_environment_utilization_returns_averages(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """CPU·MEM·DISK capacity-weighted 평균 정상 산출 (단일 서버라 동등가중과 동치)."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-util-01", hostname="util-host"))
    base_ts = _bucket_aligned_base()
    # T0: 누적 100 (busy 30, idle 70) → 30%, mem available 50/100, mount used 60%
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts,
            cpu_user=20,
            cpu_system=10,
            cpu_idle=70,
            mem_total_kb=100,
            mem_available_kb=50,
            mounts=[MountUsageEntry(mount="/", total_bytes=100, free_bytes=40, avail_bytes=40, kind="data")],
            disk_io=[],
            net_io=[],
        ),
    )
    # T1: 누적 200 (busy 80, idle 120) — delta: busy 50, total 100 → 50%
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts + timedelta(minutes=1),
            cpu_user=60,
            cpu_system=20,
            cpu_idle=120,
            mem_total_kb=100,
            mem_available_kb=30,  # latest → 사용률 70%
            mounts=[
                MountUsageEntry(mount="/", total_bytes=100, free_bytes=20, avail_bytes=20, kind="data")
            ],  # latest → 80%
            disk_io=[],
            net_io=[],
        ),
    )
    # CPU: LAG pair 1개. (1 - Σd_idle/Σd_total)*100 = (1 - 50/100)*100 = 50%
    util = await query_repo.environment_utilization(period_days=1, end=datetime.now(UTC))
    assert util.cpu_avg_pct is not None and 49.0 <= util.cpu_avg_pct <= 51.0
    # MEM capacity-weighted = Σused/Σtotal = (50+70)/(100+100) = 60%
    assert util.mem_avg_pct is not None and 59.0 <= util.mem_avg_pct <= 61.0
    # DISK capacity-weighted = Σused/Σtotal = (60+80)/(100+100) = 70%
    assert util.disk_avg_pct is not None and 69.0 <= util.disk_avg_pct <= 71.0
    assert util.sample_size >= 1


async def test_environment_utilization_excludes_outside_window(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """기간 밖 메트릭은 평균에서 제외."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-util-stale"))
    # 30일 전 메트릭 — 기본 period_days=1 밖
    stale_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(days=30)
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=stale_ts,
            cpu_user=50,
            cpu_idle=50,
            mem_total_kb=100,
            mem_available_kb=10,
            mounts=[],
            disk_io=[],
            net_io=[],
        ),
    )
    util = await query_repo.environment_utilization(period_days=1, end=datetime.now(UTC))
    assert util is not None  # 정상 호출 + 기간 밖 데이터로 인한 예외 없음


async def test_environment_utilization_capacity_weighted(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """capacity-weighted: 자원 총량(jiffies/KB) 큰 서버가 큰 비중 — 서버 동등가중과 다른 결과."""
    base_ts = _bucket_aligned_base()
    end = datetime.now(UTC)
    # 소형·고활용: d_total 100, d_idle 10 -> CPU 90%, MEM used 90/100
    small = await collect_repo.upsert_server(make_inventory(composite_id="q-cap-small"))
    for i, (user, idle) in enumerate([(0, 0), (90, 10)]):
        await collect_repo.record_metrics(
            small,
            make_metrics(
                collected_at=base_ts + timedelta(minutes=i),
                cpu_user=user,
                cpu_system=0,
                cpu_idle=idle,
                mem_total_kb=100,
                mem_available_kb=10,
                mounts=[],
                disk_io=[],
                net_io=[],
            ),
        )
    # 대형·저활용: d_total 1000, d_idle 900 -> CPU 10%, MEM used 100/1000. 자원 10배
    big = await collect_repo.upsert_server(make_inventory(composite_id="q-cap-big"))
    for i, (user, idle) in enumerate([(0, 0), (100, 900)]):
        await collect_repo.record_metrics(
            big,
            make_metrics(
                collected_at=base_ts + timedelta(minutes=i),
                cpu_user=user,
                cpu_system=0,
                cpu_idle=idle,
                mem_total_kb=1000,
                mem_available_kb=900,
                mounts=[],
                disk_io=[],
                net_io=[],
            ),
        )
    util = await query_repo.environment_utilization(period_days=1, end=end, server_ids=[small, big])
    # CPU capacity-weighted = (1 - Σd_idle/Σd_total)*100 = (1 - 910/1100)*100 ≈ 17.3% (동등가중이면 50%)
    assert util.cpu_avg_pct is not None and 15.0 <= util.cpu_avg_pct <= 20.0
    # MEM capacity-weighted = Σused/Σtotal = (180+200)/(200+2000)*100 ≈ 17.3% (동등가중이면 50%)
    assert util.mem_avg_pct is not None and 15.0 <= util.mem_avg_pct <= 20.0


async def test_environment_utilization_server_ids_filter(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """server_ids 지정 시 해당 서버만 집계 (selection 보고서 경로)."""
    base_ts = _bucket_aligned_base()
    end = datetime.now(UTC)
    a = await collect_repo.upsert_server(make_inventory(composite_id="q-sid-a"))
    b = await collect_repo.upsert_server(make_inventory(composite_id="q-sid-b"))
    # A: CPU 90%, B: CPU 10% (동일 jiffies 규모 — capacity-weighted=동등가중)
    for sid_, pairs in [(a, [(0, 0), (90, 10)]), (b, [(0, 0), (10, 90)])]:
        for i, (user, idle) in enumerate(pairs):
            await collect_repo.record_metrics(
                sid_,
                make_metrics(
                    collected_at=base_ts + timedelta(minutes=i),
                    cpu_user=user,
                    cpu_system=0,
                    cpu_idle=idle,
                    mem_total_kb=100,
                    mem_available_kb=50,
                    mounts=[],
                    disk_io=[],
                    net_io=[],
                ),
            )
    only_a = await query_repo.environment_utilization(period_days=1, end=end, server_ids=[a])
    assert only_a.cpu_avg_pct is not None and 89.0 <= only_a.cpu_avg_pct <= 91.0
    assert only_a.sample_size == 1
    both = await query_repo.environment_utilization(period_days=1, end=end, server_ids=[a, b])
    assert both.cpu_avg_pct is not None and 49.0 <= both.cpu_avg_pct <= 51.0  # (90+10) 통합 = 50%
    assert both.sample_size == 2


async def test_metric_trend_capacity_weighted(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """환경 추이 차트도 capacity-weighted — 버킷 값을 큰 자원 서버가 지배 (카드와 동일 가중)."""
    base_ts = _bucket_aligned_base()
    start = base_ts - timedelta(minutes=1)
    end = datetime.now(UTC)
    small = await collect_repo.upsert_server(make_inventory(composite_id="q-trend-small"))
    big = await collect_repo.upsert_server(make_inventory(composite_id="q-trend-big"))
    # small 고활용(cpu 90%, mem 90%) / big 저활용(cpu 10%, mem 10%) + 자원 10배
    for sid_, pairs, mtot, mavail in [
        (small, [(0, 0), (90, 10)], 100, 10),
        (big, [(0, 0), (100, 900)], 1000, 900),
    ]:
        for i, (user, idle) in enumerate(pairs):
            await collect_repo.record_metrics(
                sid_,
                make_metrics(
                    collected_at=base_ts + timedelta(minutes=i),
                    cpu_user=user,
                    cpu_system=0,
                    cpu_idle=idle,
                    mem_total_kb=mtot,
                    mem_available_kb=mavail,
                    mounts=[],
                    disk_io=[],
                    net_io=[],
                ),
            )
    bi, td = "1 hour", timedelta(hours=1)  # 전 데이터 한 버킷으로 강제
    cpu = await query_repo.metric_trend("cpu.usage_percent", start, end, bi, td, [small, big])
    # 버킷 Σd_num/Σd_total = (90+100)/(100+1000)*100 ≈ 17.3% (서버 동등가중이면 50%)
    assert cpu and cpu[-1].value is not None and 15.0 <= cpu[-1].value <= 20.0
    mem = await query_repo.metric_trend("mem.usage_percent", start, end, bi, td, [small, big])
    # Σused/Σtotal = (180+200)/(200+2000)*100 ≈ 17.3% (서버 동등가중이면 50%)
    assert mem and mem[-1].value is not None and 15.0 <= mem[-1].value <= 20.0


async def test_metric_trend_cached_null_component_is_gap_not_zero(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """미측정 성분(Windows mem_cached=null)은 0% 가 아니라 gap 으로 표시 — 측정 0 과 구분 (#C2·A1).

    이전 COALESCE-to-0 는 null 성분을 "측정된 0% cached" 로 오도했다. 성분 IS NOT NULL 가드로 win-only 는
    데이터 포인트 없음(gap), 실측 host 는 값 산출. 혼재 시 win 이 분모·분자에서 함께 제외돼 실측값을 0 쪽으로
    끌어내리지 않는다(capacity-weighted 정합, environment_utilization 과 동일 가드).
    """
    base_ts = _bucket_aligned_base()
    start = base_ts - timedelta(minutes=1)
    end = datetime.now(UTC)
    win = await collect_repo.upsert_server(make_inventory(composite_id="q-cached-win"))
    lin = await collect_repo.upsert_server(make_inventory(composite_id="q-cached-lin"))
    # win: cached/buffers 미측정(null) — Windows 계약. lin: 실측.
    await collect_repo.record_metrics(
        win,
        make_metrics(
            collected_at=base_ts, mem_total_kb=1000, mem_available_kb=400,
            mem_cached_kb=None, mem_buffers_kb=None, mounts=[], disk_io=[], net_io=[],
        ),
    )
    await collect_repo.record_metrics(
        lin,
        make_metrics(
            collected_at=base_ts, mem_total_kb=1000, mem_available_kb=400,
            mem_cached_kb=250, mem_buffers_kb=50, mounts=[], disk_io=[], net_io=[],
        ),
    )
    bi, td = "1 hour", timedelta(hours=1)
    # win-only: cached 미측정 -> gap (0% 포인트 아님)
    win_cached = await query_repo.metric_trend("mem.cached_percent", start, end, bi, td, [win])
    assert win_cached == []
    # 실측 host: 250/1000*100 = 25%
    lin_cached = await query_repo.metric_trend("mem.cached_percent", start, end, bi, td, [lin])
    assert lin_cached and lin_cached[-1].value is not None and 20.0 <= lin_cached[-1].value <= 30.0
    # 혼재: win 이 분모에서도 제외 -> 실측 25% 유지(0 쪽으로 안 끌림)
    mixed = await query_repo.metric_trend("mem.cached_percent", start, end, bi, td, [win, lin])
    assert mixed and mixed[-1].value is not None and 20.0 <= mixed[-1].value <= 30.0
