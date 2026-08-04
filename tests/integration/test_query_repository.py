"""QueryRepository 통합 테스트 (wire) — 정확화 검증.

검증 영역:
- inventory query (resolve_server_id, list_servers, get_server)
- _latest_per_dimension n=1/n=2 (get_storage, get_network, latest_dashboard)
- collection_status
- metric_chart(metric_trend 위임) dispatcher — v2 MetricType 전량 dispatch + SQL 정상 실행
- metric_trend 결과 정확성 (CPU LAG delta, fs.usage 시점 값, rate/dim, reset 흡수)
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
from assessment_engine.db.repositories.query.types import EnvironmentMetricType, MetricType
from tests.factories import _DISK_DEVICE_ID, agent_id_for, make_inventory, make_metrics


def _bucket_aligned_base(minutes_ago: int = 7) -> datetime:
    """5분 버킷 시작에 정렬된 과거 시각. server_metrics_5m 등 cagg 가 counter_agg delta 를 내려면 같은 5분
    버킷에 표본 2+ 가 필요 — 1분 간격 표본이 버킷 경계에 갈리지 않도록 base 를 버킷 시작에 맞춘다(ADR 0043)."""
    t = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).replace(second=0, microsecond=0)
    return t - timedelta(minutes=t.minute % 5)


pytestmark = pytest.mark.asyncio


# v2 MetricType 전량 (types.MetricType Literal 과 동기화 — dispatch 커버, #F9).
# v1 폐기: load.1m/5m/15m(소스 부재 -> cpu.run_queue), swap.usage_percent, disk.queue(-> disk.io_saturation).
_ALL_METRIC_TYPES: list[MetricType] = [
    "cpu.usage_percent",
    "cpu.user_percent",
    "cpu.system_percent",
    "cpu.iowait_percent",
    "cpu.nice_percent",
    "cpu.run_queue",
    "cpu.saturation",
    "cpu.blocked",
    "cpu.psi",
    "mem.usage_percent",
    "mem.available_percent",
    "mem.cached_percent",
    "mem.buffers_percent",
    "mem.psi",
    "mem.paging_pressure",
    "disk.read_iops",
    "disk.write_iops",
    "disk.read_kbps",
    "disk.write_kbps",
    "disk.io_saturation",
    "disk.saturation",
    "disk.psi",
    "fs.usage_percent",
    "net.rx_bytes_per_sec",
    "net.tx_bytes_per_sec",
    "net.rx_packets_per_sec",
    "net.tx_packets_per_sec",
    "net.retrans_percent",
    "net.drop_percent",
    "net.congested",
]

# EnvironmentMetricType 전량 (types.EnvironmentMetricType Literal 과 동기화 — dispatch 커버, #F9).
# collapse=True(환경 스케일 합산/count) 경로 전용 — server_ids=[1대]로도 dispatch SQL 자체는 검증 가능
# (환경 전체 서버 대상 실행은 router 통합 테스트 영역).
_ALL_ENV_METRIC_TYPES: list[EnvironmentMetricType] = [
    "cpu.usage_percent",
    "cpu.saturation_hosts",
    "mem.usage_percent",
    "mem.paging_pressure_hosts",
    "fs.usage_percent",
    "disk.saturation_hosts",
    "net.rx_bytes_per_sec",
    "net.tx_bytes_per_sec",
    "net.congested_hosts",
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
            # CPU seconds 누적 — LAG delta가 양수가 되도록 시점마다 증가 (v2 s counter)
            cpu_user_s=1000 + i * 100,
            cpu_idle_s=8000 + i * 800,
            disk_io=[
                # v2: device_id 안정키(dimension), ops/io_bytes counter (sectors*512 -> io_bytes)
                DiskIoEntry(
                    device_id="sda",
                    device_name="sda",
                    ops_read=100 + i * 50,
                    ops_write=50 + i * 25,
                    io_read_bytes=(2000 + i * 1000) * 512,
                    io_write_bytes=(1000 + i * 500) * 512,
                    op_read_time_s=1.0 + i * 0.5,
                    op_write_time_s=0.5 + i * 0.25,
                    io_time_s=1.0 + i * 0.5,
                ),
            ],
            filesystems=[
                # v2: mountpoint + used/free bytes (used=total-avail, free=avail), 실 fs=ext4
                FilesystemEntry(
                    mountpoint="/",
                    fstype="ext4",
                    used_bytes=50_000_000_000 - 18_000_000_000,
                    free_bytes=18_000_000_000,
                    inodes_used=100_000,
                    inodes_free=900_000,
                ),
            ],
            net_io=[
                NetIoEntry(
                    iface_id="eth0",
                    iface_name="eth0",
                    rx_bytes=1_000_000 + i * 100_000,
                    tx_bytes=500_000 + i * 50_000,
                    rx_packets=1000 + i * 100,
                    tx_packets=500 + i * 50,
                    rx_errors=0,
                    tx_errors=0,
                    rx_dropped=0,
                    tx_dropped=0,
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
    # 시드는 mountpoint="/" 1개만. 여러 시점이라도 mount당 1행.
    assert len(storage.filesystems) == 1
    assert storage.filesystems[0].mountpoint == "/"


# ─── _latest_per_dimension (n=2) — get_network ────────────────────────────


async def test_get_network_returns_at_most_2_per_interface(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """delta 계산용 — interface당 최신 2행 (PARTITION BY + ROW_NUMBER)."""
    sid, _ = await _seed_one_server_with_metrics(collect_repo, composite_id="q-net-1", n_points=5)
    network = await query_repo.get_network(sid)
    assert network is not None
    # 시드는 iface_id="eth0" 1개. 5시점이지만 최신 2행.
    eth0_rows = [r for r in network.net_io if r.iface_id == "eth0"]
    assert len(eth0_rows) == 2


# ─── collection_status ────────────────────────────────────────────────────


async def test_collection_status_reports_both_timestamps(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    sid, _ = await _seed_one_server_with_metrics(collect_repo, composite_id="q-cs-1", n_points=2)
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
    # filesystems: mount당 1행
    assert len(dash.filesystems) == 1


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
            cpu_user_s=9999,
            cpu_idle_s=99999,
            disk_io=[
                DiskIoEntry(
                    device_id="sda",
                    device_name="sda",
                    ops_read=9999,
                    ops_write=9999,
                    io_read_bytes=9999 * 512,
                    io_write_bytes=9999 * 512,
                )
            ],
            filesystems=[FilesystemEntry(mountpoint="/", fstype="ext4", used_bytes=50_000_000_000 - 1, free_bytes=1)],
            net_io=[
                NetIoEntry(
                    iface_id="eth0",
                    iface_name="eth0",
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
    # disk_io/net_io/filesystem 도 미래행을 최신으로 잡지 않음
    assert all(d.collected_at < future_ts for d in dash.disk_io)
    assert all(n.collected_at < future_ts for n in dash.net_io)
    assert all(mu.collected_at is not None and mu.collected_at < future_ts for mu in dash.filesystems)


# ─── metric_chart dispatcher — 17개 metric_type 모두 무사 dispatch ────────


@pytest.mark.parametrize("metric_type", _ALL_METRIC_TYPES)
async def test_metric_chart_dispatcher_all_types(
    metric_type: MetricType,
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


@pytest.mark.parametrize("metric_type", _ALL_ENV_METRIC_TYPES)
async def test_metric_trend_env_dispatcher_all_types(
    metric_type: EnvironmentMetricType,
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """EnvironmentMetricType 전량이 collapse=True dispatch 를 통과 — 결과는 비어도 OK(#F9)."""
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, composite_id=f"q-env-{metric_type}", n_points=3)
    end = base_ts + timedelta(minutes=10)
    rows = await query_repo.metric_trend(
        metric_type,
        base_ts,
        end,
        "5m",
        server_ids=[sid],
        agg="avg",
        collapse=True,
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


async def test_metric_chart_disk_io_saturation_returns_await(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """disk.io_saturation — v2 await(ms) 양 OS 통일(구 disk.queue 대체). Σ(Δop_time)/Σ(Δops)*1000.
    시드 op_time/ops 델타 + io_time_s(device util >= RS_DISKIO_UTIL_MIN 게이트 통과 — 60s 간격에 Δ40s=0.67).

    device_id 는 물리 디스크 필터(`_PHYS_DISK_SQL_FILTER`)가 조인하는 "name:{block_devices.name}" 규약 —
    inventory 에 동일 name 의 disk 노드가 있어야 chart 가 값을 낸다(tests/factories.py 상단 규약 주석)."""
    sid = await collect_repo.upsert_server(
        make_inventory(
            composite_id="q-dsat-1",
            block_devices=[{"id": "sda", "id_type": "by-path", "name": "sda", "type": "disk", "size_bytes": 10**9}],
        )
    )
    base = _bucket_aligned_base()
    for i in range(2):
        await collect_repo.record_metrics(
            sid,
            make_metrics(
                collected_at=base + timedelta(minutes=i),
                disk_io=[
                    DiskIoEntry(
                        device_id="name:sda",
                        device_name="sda",
                        ops_read=100 + i * 100,
                        ops_write=0,
                        op_read_time_s=1.0 + i * 1.0,
                        op_write_time_s=0.0,
                        io_time_s=i * 40.0,  # Δ40s / 60s wall = 0.67 util >= 0.5 게이트 통과
                        io_read_bytes=0,
                        io_write_bytes=0,
                    )
                ],
            ),
        )
    rows = await query_repo.metric_chart(
        server_id=sid,
        metric_type="disk.io_saturation",
        dimension=None,
        time_range="1h",
        bucket="5m",
        agg="avg",
        end=base + timedelta(minutes=10),
    )
    vals = [r.value for r in rows if r.value is not None]
    assert vals, "op_time/ops 델타가 있으면 disk.io_saturation await 가 값을 반환해야 함"
    assert all(v >= 0 for v in vals)  # await(ms) 비음수


async def test_metric_chart_disk_io_saturation_excludes_idle_disk(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """disk.io_saturation — ops 델타 0(유휴 디스크)은 d_ops>0 필터로 제외 → await 값 없음."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-dsat-idle"))
    base = _bucket_aligned_base()
    for i in range(2):
        # ops/op_time 불변 → 델타 0 → await 산출 불가
        await collect_repo.record_metrics(
            sid,
            make_metrics(
                collected_at=base + timedelta(minutes=i),
                disk_io=[
                    DiskIoEntry(
                        device_id="sda",
                        device_name="sda",
                        ops_read=100,
                        ops_write=0,
                        op_read_time_s=1.0,
                        op_write_time_s=0.0,
                        io_read_bytes=0,
                        io_write_bytes=0,
                    )
                ],
            ),
        )
    rows = await query_repo.metric_chart(
        server_id=sid,
        metric_type="disk.io_saturation",
        dimension=None,
        time_range="1h",
        bucket="5m",
        agg="avg",
        end=base + timedelta(minutes=10),
    )
    assert all(r.value is None for r in rows)  # d_ops=0 제외 → 값 산출 안 됨 (빈 결과 또는 value None)


async def test_metric_chart_disk_io_saturation_util_gate_excludes_low_activity(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """disk.io_saturation util-gate(2-1) — ops 델타는 있으나 io_time util < RS_DISKIO_UTIL_MIN 인 저활동
    device 는 제외. 극소 ops 로 op_time 을 나눠 await 가 폭증(writeback 잔류)해도 병목 아님 → 값 없음."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-dsat-lowutil"))
    base = _bucket_aligned_base()
    for i in range(2):
        await collect_repo.record_metrics(
            sid,
            make_metrics(
                collected_at=base + timedelta(minutes=i),
                disk_io=[
                    DiskIoEntry(
                        device_id="sda",
                        device_name="sda",
                        ops_read=100 + i * 2,
                        ops_write=0,  # Δ2 극소 ops
                        op_read_time_s=i * 10.0,
                        op_write_time_s=0.0,  # Δ10s -> await 5000ms(폭증)
                        io_time_s=i * 3.0,  # Δ3s / 60s wall = 0.05 util < 0.5 -> 게이트 탈락
                        io_read_bytes=0,
                        io_write_bytes=0,
                    )
                ],
            ),
        )
    rows = await query_repo.metric_chart(
        server_id=sid,
        metric_type="disk.io_saturation",
        dimension=None,
        time_range="1h",
        bucket="5m",
        agg="avg",
        end=base + timedelta(minutes=10),
    )
    assert all(r.value is None for r in rows)  # 저활동 device 는 await 폭증해도 util-gate 로 제외


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
    """_chart_rate_per_dimension — disk_io의 LAG/dt 기반 IOPS. dimension=device 채워짐.

    device_id 는 물리 디스크 필터가 조인하는 "name:{block_devices.name}" 규약(tests/factories.py 상단 주석) —
    `_seed_one_server_with_metrics` 공용 helper 는 기본 inventory(name=vda)와 안 맞아 여기선 전용 시드 사용."""
    sid = await collect_repo.upsert_server(
        make_inventory(
            composite_id="q-dio-1",
            block_devices=[{"id": "sda", "id_type": "by-path", "name": "sda", "type": "disk", "size_bytes": 10**9}],
        )
    )
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=3)
    for i in range(3):
        await collect_repo.record_metrics(
            sid,
            make_metrics(
                collected_at=base_ts + timedelta(minutes=i),
                disk_io=[
                    DiskIoEntry(
                        device_id="name:sda",
                        device_name="sda",
                        ops_read=100 + i * 50,
                        ops_write=50 + i * 25,
                        io_read_bytes=(2000 + i * 1000) * 512,
                        io_write_bytes=(1000 + i * 500) * 512,
                    )
                ],
                filesystems=[],
                net_io=[],
            ),
        )
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
        assert r.dimension == "sda"  # 범례 표시명 — raw device_id 아닌 block_devices.name 치환(COALESCE(dn.name, dim))
        if r.value is not None:
            assert r.value >= 0  # 음수 IOPS 없음


async def test_metric_chart_cpu_reset_excludes_counter_decrease(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """metric_chart(metric_trend 위임) — CPU counter reset(재부팅 후 jiffies 0 재시작 = 값 감소)은
    d_total>0 필터로 제외 → 그 버킷 차트 missing.

    v2 는 boot_time 차트 gate 폐기(metric.py) — reset 은 delta 부호로 흡수. 값이 감소하면 d_total<=0
    이라 그 시점(재부팅 첫 측정)이 valid 에서 빠진다. 시점 1은 정상 누적 → 정상 percent.
    """
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-rst-cpu-1"))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=10)
    boot_a = datetime(2026, 5, 9, 10, 0, tzinfo=UTC)
    boot_b = datetime(2026, 5, 9, 11, 0, tzinfo=UTC)

    # 시점 0~1: 정상 누적 / 시점 2: 재부팅 counter reset(값 감소) → d_total<0 → 제외
    cases = [(0, boot_a, 1000, 8000), (5, boot_a, 1100, 8800), (10, boot_b, 50, 400)]
    for offset, bt, cu, ci in cases:
        await collect_repo.record_metrics(
            sid,
            make_metrics(
                collected_at=base_ts + timedelta(minutes=offset),
                boot_time=bt,
                agent_started_at=bt + timedelta(seconds=10),
                cpu_user_s=cu,
                cpu_idle_s=ci,
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
    # reset(값 감소) 시점 2는 d_total<=0 → valid 제외 → 그 버킷 차트에서 빠짐.
    # 시점 1은 정상 → 정상 percent. 결과 값은 모두 0~100.
    assert all(0 <= r.value <= 100 for r in rows if r.value is not None)
    reset_bucket_ts = base_ts + timedelta(minutes=10)
    reset_bucket_in_result = any(r.collected_at.replace(tzinfo=UTC) == reset_bucket_ts.replace(second=0) for r in rows)
    assert not reset_bucket_in_result, "reset(counter 감소) 시점 차트에 포함됨 — d_total>0 필터 미적용"


async def test_metric_chart_rate_clamps_counter_decrease(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """rate 차트(rate/dim) — counter reset(값 감소)은 GREATEST(delta,0)로 0 클램프.

    v2 는 boot_time 차트 gate 폐기 — child 시계열(disk_io)은 boot_time 미보유라 reset 을 GREATEST(delta,0)
    로 흡수한다. 재부팅으로 카운터가 감소해도 음수 rate/spike 대신 0 을 낸다.
    """
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-rst-rate-1"))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=10)

    # 시점 2 reads 감소(재부팅 counter reset) → GREATEST(50-200,0)=0
    cases = [(0, 100), (5, 200), (10, 50)]
    for offset, reads in cases:
        await collect_repo.record_metrics(
            sid,
            make_metrics(
                collected_at=base_ts + timedelta(minutes=offset),
                disk_io=[
                    DiskIoEntry(
                        device_id="sda",
                        device_name="sda",
                        ops_read=reads,
                        ops_write=0,
                        io_read_bytes=0,
                        io_write_bytes=0,
                    )
                ],
                filesystems=[],
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
    # 음수/스파이크 없음 — reset 시점은 0 클램프
    assert all(r.value >= 0 for r in rows if r.value is not None)
    reset_bucket_ts = (base_ts + timedelta(minutes=10)).replace(second=0)
    reset_vals = [r.value for r in rows if r.collected_at.replace(tzinfo=UTC) == reset_bucket_ts]
    assert all(v == 0 for v in reset_vals if v is not None), "reset(counter 감소) rate 가 0 클램프 아님"


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
    assert sid is not None
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
    assert sid is not None
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
    """dimension 파라미터로 특정 device만 필터.

    device_id 는 물리 디스크 필터가 조인하는 "name:{block_devices.name}" 규약(tests/factories.py 상단 주석)."""
    sid = await collect_repo.upsert_server(
        make_inventory(
            composite_id="q-dim-1",
            block_devices=[
                {"id": "sda", "id_type": "by-path", "name": "sda", "type": "disk", "size_bytes": 10**9},
                {"id": "sdb", "id_type": "by-path", "name": "sdb", "type": "disk", "size_bytes": 10**9},
            ],
        )
    )
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=3)
    for i in range(3):
        await collect_repo.record_metrics(
            sid,
            make_metrics(
                collected_at=base_ts + timedelta(minutes=i),
                disk_io=[
                    DiskIoEntry(
                        device_id="name:sda",
                        device_name="sda",
                        ops_read=100 + i * 10,
                        ops_write=0,
                        io_read_bytes=0,
                        io_write_bytes=0,
                    ),
                    DiskIoEntry(
                        device_id="name:sdb",
                        device_name="sdb",
                        ops_read=200 + i * 20,
                        ops_write=0,
                        io_read_bytes=0,
                        io_write_bytes=0,
                    ),
                ],
                filesystems=[],
                net_io=[],
            ),
        )
    end = base_ts + timedelta(minutes=10)
    rows_sda = await query_repo.metric_chart(
        server_id=sid,
        metric_type="disk.read_iops",
        dimension="name:sda",
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
    # 범례 표시명 — raw device_id("name:sda") 아닌 block_devices.name 치환(COALESCE(dn.name, dim)).
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
            filesystems=[
                FilesystemEntry(mountpoint="/old", fstype="ext4", used_bytes=10**12 - 10**11, free_bytes=10**11)
            ],
            disk_io=[],
            net_io=[],
        ),
    )
    storage = await query_repo.get_storage(sid)
    assert storage is not None
    mount_names = [m.mountpoint for m in storage.filesystems]
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


# ─── mount 사용률 신호는 report_aggregate 가 단일 산출 ────────────────
# worst mount used% + 용량 임박 구동 마운트(runway) 모두 report_aggregate (test_query_repository_report.py).


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
            cpu_user_s=20,
            cpu_system_s=10,
            cpu_idle_s=70,
            mem_limit_bytes=100,
            mem_available_bytes=50,
            # v2: used=total-avail=60, free=avail=40 → used/(used+free)=60%
            filesystems=[FilesystemEntry(mountpoint="/", fstype="ext4", used_bytes=60, free_bytes=40)],
            disk_io=[],
            net_io=[],
        ),
    )
    # T1: 누적 200 (busy 80, idle 120) — delta: busy 50, total 100 → 50%
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts + timedelta(minutes=1),
            cpu_user_s=60,
            cpu_system_s=20,
            cpu_idle_s=120,
            mem_limit_bytes=100,
            mem_available_bytes=30,  # latest → 사용률 70%
            # used=total-avail=80, free=avail=20 → 80%
            filesystems=[FilesystemEntry(mountpoint="/", fstype="ext4", used_bytes=80, free_bytes=20)],
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
            cpu_user_s=50,
            cpu_idle_s=50,
            mem_limit_bytes=100,
            mem_available_bytes=10,
            filesystems=[],
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
                cpu_user_s=user,
                cpu_system_s=0,
                cpu_idle_s=idle,
                mem_limit_bytes=100,
                mem_available_bytes=10,
                filesystems=[],
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
                cpu_user_s=user,
                cpu_system_s=0,
                cpu_idle_s=idle,
                mem_limit_bytes=1000,
                mem_available_bytes=900,
                filesystems=[],
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
                    cpu_user_s=user,
                    cpu_system_s=0,
                    cpu_idle_s=idle,
                    mem_limit_bytes=100,
                    mem_available_bytes=50,
                    filesystems=[],
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
                    cpu_user_s=user,
                    cpu_system_s=0,
                    cpu_idle_s=idle,
                    mem_limit_bytes=mtot,
                    mem_available_bytes=mavail,
                    filesystems=[],
                    disk_io=[],
                    net_io=[],
                ),
            )
    bucket = "1h"  # 전 데이터 한 버킷으로 강제
    cpu = await query_repo.metric_trend("cpu.usage_percent", start, end, bucket, [small, big])
    # 버킷 Σd_num/Σd_total = (90+100)/(100+1000)*100 ~ 17.3% (서버 동등가중이면 50%)
    assert cpu and cpu[-1].value is not None and 15.0 <= cpu[-1].value <= 20.0
    mem = await query_repo.metric_trend("mem.usage_percent", start, end, bucket, [small, big])
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
            collected_at=base_ts,
            mem_limit_bytes=1000,
            mem_available_bytes=400,
            mem_cached_bytes=None,
            mem_buffered_bytes=None,
            filesystems=[],
            disk_io=[],
            net_io=[],
        ),
    )
    await collect_repo.record_metrics(
        lin,
        make_metrics(
            collected_at=base_ts,
            mem_limit_bytes=1000,
            mem_available_bytes=400,
            mem_cached_bytes=250,
            mem_buffered_bytes=50,
            filesystems=[],
            disk_io=[],
            net_io=[],
        ),
    )
    bucket = "1h"
    # win-only: cached 미측정 -> gap (0% 포인트 아님)
    win_cached = await query_repo.metric_trend("mem.cached_percent", start, end, bucket, [win])
    assert win_cached == []
    # 실측 host: 250/1000*100 = 25%
    lin_cached = await query_repo.metric_trend("mem.cached_percent", start, end, bucket, [lin])
    assert lin_cached and lin_cached[-1].value is not None and 20.0 <= lin_cached[-1].value <= 30.0
    # 혼재: win 이 분모에서도 제외 -> 실측 25% 유지(0 쪽으로 안 끌림)
    mixed = await query_repo.metric_trend("mem.cached_percent", start, end, bucket, [win, lin])
    assert mixed and mixed[-1].value is not None and 20.0 <= mixed[-1].value <= 30.0


# ─── 이번 세션 신규 SQL 정확성 — mem.paging_pressure / net.congested / fs.usage_percent LOCF ──────────


async def test_mem_paging_pressure_crosses_on_linux_refault(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """mem.paging_pressure — Linux 는 paging_major rate > 0 이면 즉시 1.0(존재 판정, mem_pressure_active 와 동일).

    mem.paging_pressure_hosts(환경, count) 와 원자료·임계 동일 — 서버 1대 단일 시계열로 축소한 버전(이진 0/1).
    """
    base_ts = _bucket_aligned_base(minutes_ago=10)
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-paging-hi"))
    # p0: paging_major 기준점. p1(+1m): 증가 -> delta > 0 -> crossed.
    await collect_repo.record_metrics(
        sid, make_metrics(collected_at=base_ts, paging_major=1000, filesystems=[], disk_io=[], net_io=[])
    )
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts + timedelta(minutes=1), paging_major=1100, filesystems=[], disk_io=[], net_io=[]
        ),
    )
    end = base_ts + timedelta(minutes=5)
    rows = await query_repo.metric_chart(
        server_id=sid,
        metric_type="mem.paging_pressure",
        dimension=None,
        time_range="15m",
        bucket="1m",
        agg="avg",
        end=end,
    )
    assert rows, "paging_major 증가 구간은 최소 1개 버킷을 반환해야 한다"
    values = {r.value for r in rows}
    assert values <= {0.0, 1.0}, f"이진 0/1 외 값 유입: {values}"
    assert 1.0 in values, "paging_major 증가(존재 판정)가 버킷에 반영돼야 한다"


async def test_mem_paging_pressure_flat_stays_zero(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """mem.paging_pressure — paging_major 변화 없으면(delta=0) 항상 0.0(정상)."""
    base_ts = _bucket_aligned_base(minutes_ago=10)
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-paging-flat"))
    for i in range(3):
        await collect_repo.record_metrics(
            sid,
            make_metrics(
                collected_at=base_ts + timedelta(minutes=i), paging_major=1000, filesystems=[], disk_io=[], net_io=[]
            ),
        )
    end = base_ts + timedelta(minutes=5)
    rows = await query_repo.metric_chart(
        server_id=sid,
        metric_type="mem.paging_pressure",
        dimension=None,
        time_range="15m",
        bucket="1m",
        agg="avg",
        end=end,
    )
    assert rows
    assert all(r.value == 0.0 for r in rows), "delta=0 는 어느 버킷도 압박 판정이면 안 된다"


async def test_mem_paging_pressure_crosses_on_windows_pages_input(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """회귀: Windows 는 paging.operations 를 direction=in 만 발행(type=major 없음) -> server_metrics.paging_major
    가 항상 NULL. mem.paging_pressure SQL 이 os_family 로 paging_in(Pages Input)을 선택해야 Windows 도 발화한다
    (paging_major 만 읽던 이전 버전은 이 케이스에서 rows 가 항상 빈 리스트).

    임계 WIN_PAGES_INPUT_SATURATION=20/s — p0->p1(+1m) delta 2000 -> rate 2000/60s ~= 33.3/s > 20 이면 crossed.
    """
    base_ts = _bucket_aligned_base(minutes_ago=10)
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-paging-win", os_family="windows"))
    await collect_repo.record_metrics(
        sid,
        make_metrics(collected_at=base_ts, paging_in=1000, paging_major=None, filesystems=[], disk_io=[], net_io=[]),
    )
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts + timedelta(minutes=1),
            paging_in=3000,
            paging_major=None,
            filesystems=[],
            disk_io=[],
            net_io=[],
        ),
    )
    end = base_ts + timedelta(minutes=5)
    rows = await query_repo.metric_chart(
        server_id=sid,
        metric_type="mem.paging_pressure",
        dimension=None,
        time_range="15m",
        bucket="1m",
        agg="avg",
        end=end,
    )
    assert rows, "Windows paging_in 급증 구간은 최소 1개 버킷을 반환해야 한다(paging_major NULL 이라도)"
    assert 1.0 in {r.value for r in rows}, "Windows Pages Input 임계 초과가 버킷에 반영돼야 한다"


async def test_mem_paging_pressure_hosts_counts_windows(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """회귀: mem.paging_pressure_hosts(환경 집계)도 Windows paging_in 을 선택해 카운트에 반영해야 한다."""
    base_ts = _bucket_aligned_base(minutes_ago=10)
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-paging-win-hosts", os_family="windows"))
    await collect_repo.record_metrics(
        sid,
        make_metrics(collected_at=base_ts, paging_in=1000, paging_major=None, filesystems=[], disk_io=[], net_io=[]),
    )
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts + timedelta(minutes=1),
            paging_in=3000,
            paging_major=None,
            filesystems=[],
            disk_io=[],
            net_io=[],
        ),
    )
    end = base_ts + timedelta(minutes=5)
    rows = await query_repo.metric_trend("mem.paging_pressure_hosts", base_ts, end, "1m", server_ids=[sid], agg="avg")
    assert rows
    assert max(r.value for r in rows if r.value is not None) >= 1, "Windows 호스트가 압박 카운트에 잡혀야 한다"


async def test_latest_saturation_windows_paging_uses_pages_input(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """회귀(HIGH): latest_saturation(실시간 환경/서버 상세 원자료) 이 Windows 는 paging_major(항상 NULL) 대신
    paging_in 을 읽어 paging_major_rate 를 산출해야 한다 — 그래야 mem_pressure_active(실시간)가 report_aggregate
    기반 mem_saturated(윈도우 사이징)와 같은 Windows 호스트에 대해 상반된 진단을 내지 않는다."""
    base_ts = _bucket_aligned_base(minutes_ago=10)
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-latest-sat-win", os_family="windows"))
    await collect_repo.record_metrics(
        sid,
        make_metrics(collected_at=base_ts, paging_in=1000, paging_major=None, filesystems=[], disk_io=[], net_io=[]),
    )
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts + timedelta(minutes=1),
            paging_in=3000,
            paging_major=None,
            filesystems=[],
            disk_io=[],
            net_io=[],
        ),
    )
    since = base_ts - timedelta(minutes=1)
    result = await query_repo.latest_saturation([sid], since)
    sat = result[sid]
    # delta=2000(3000-1000) / dt=60s = 33.33.../s — paging_major 는 두 행 모두 None 이라 paging_in 델타에서만 나온다.
    assert sat.paging_major_rate is not None, "Windows 는 paging_in 델타로 rate 가 산출돼야 한다(paging_major 무관)"
    assert 33.0 <= sat.paging_major_rate <= 33.5


_PHYS_IFACE_ID = "mac:52:54:00:12:34:56"  # tests/factories.make_inventory 기본 net_interfaces 와 동일 안정키


async def test_net_congested_crosses_on_retrans_spike(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """net.congested — 재전송율(>1%)이 저트래픽 게이트를 넘는 트래픽에서 발생하면 1.0.

    net.congested_hosts(환경, count) 와 동일 원자료·임계·OR 판정을 서버 1대 단일 시계열로 축소.
    """
    base_ts = _bucket_aligned_base(minutes_ago=10)
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-net-cong"))
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts,
            net_tcp_retransmits=0,
            filesystems=[],
            disk_io=[],
            net_io=[
                NetIoEntry(
                    iface_id=_PHYS_IFACE_ID,
                    iface_name="eth0",
                    rx_bytes=0,
                    tx_bytes=0,
                    rx_packets=0,
                    tx_packets=1000,
                    rx_dropped=0,
                    tx_dropped=0,
                )
            ],
        ),
    )
    # +1분: 재전송 50건 / 송신 패킷 1000건 증가 -> 5% (임계 1% 초과). 트래픽 800,000B/60s ≈ 13kB/s(임계 10 이상 충족).
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts + timedelta(minutes=1),
            net_tcp_retransmits=50,
            filesystems=[],
            disk_io=[],
            net_io=[
                NetIoEntry(
                    iface_id=_PHYS_IFACE_ID,
                    iface_name="eth0",
                    rx_bytes=400_000,
                    tx_bytes=400_000,
                    rx_packets=1000,
                    tx_packets=2000,
                    rx_dropped=0,
                    tx_dropped=0,
                )
            ],
        ),
    )
    end = base_ts + timedelta(minutes=5)
    rows = await query_repo.metric_chart(
        server_id=sid, metric_type="net.congested", dimension=None, time_range="15m", bucket="1m", agg="avg", end=end
    )
    assert rows
    values = {r.value for r in rows}
    assert values <= {0.0, 1.0}
    assert 1.0 in values, "재전송율 5%(임계 1% 초과, 저트래픽 게이트 통과)가 이상으로 판정돼야 한다"


async def test_net_congested_low_traffic_gate_suppresses_retrans(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """net.congested — 트래픽이 저트래픽 게이트(RS_NET_MIN_TRAFFIC_KBPS) 미만이면 재전송율이 임계를 넘어도 억제."""
    base_ts = _bucket_aligned_base(minutes_ago=10)
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-net-lowtraffic"))
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts,
            net_tcp_retransmits=0,
            filesystems=[],
            disk_io=[],
            net_io=[
                NetIoEntry(
                    iface_id=_PHYS_IFACE_ID,
                    iface_name="eth0",
                    rx_bytes=0,
                    tx_bytes=0,
                    rx_packets=0,
                    tx_packets=10,
                    rx_dropped=0,
                    tx_dropped=0,
                )
            ],
        ),
    )
    # +1분: 재전송 5건 / 송신 패킷 10건 증가 -> 50%(임계 초과) 이나 트래픽 자체가 거의 없음(< 10 kB/s 게이트).
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts + timedelta(minutes=1),
            net_tcp_retransmits=5,
            filesystems=[],
            disk_io=[],
            net_io=[
                NetIoEntry(
                    iface_id=_PHYS_IFACE_ID,
                    iface_name="eth0",
                    rx_bytes=100,
                    tx_bytes=100,
                    rx_packets=10,
                    tx_packets=20,
                    rx_dropped=0,
                    tx_dropped=0,
                )
            ],
        ),
    )
    end = base_ts + timedelta(minutes=5)
    rows = await query_repo.metric_chart(
        server_id=sid, metric_type="net.congested", dimension=None, time_range="15m", bucket="1m", agg="avg", end=end
    )
    assert rows
    assert all(r.value == 0.0 for r in rows), "저트래픽 게이트 미만이면 재전송율 임계 초과여도 억제돼야 한다"


async def test_fs_usage_percent_collapse_locf_no_first_bucket_distortion(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """fs.usage_percent(collapse=True) — LATERAL LOCF 로 표본 사이 버킷도 직전 값을 유지해야 한다.

    수정 전(bucket-scoped last())에는 표본이 없는 버킷이 undercount(0에 가까운 값)로 튀었다 — 이번 세션에
    fs.used_bytes 와 동일 LOCF 기법으로 fs.usage_percent(collapse=True) 도 재작성.
    """
    base_ts = _bucket_aligned_base(minutes_ago=10)
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-fs-locf"))
    # 5분 간격 표본 2개 — 그 사이 1분 버킷들은 직접 표본이 없다.
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts,
            disk_io=[],
            net_io=[],
            filesystems=[FilesystemEntry(mountpoint="/", fstype="ext4", used_bytes=60, free_bytes=40)],
        ),
    )
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts + timedelta(minutes=5),
            disk_io=[],
            net_io=[],
            filesystems=[FilesystemEntry(mountpoint="/", fstype="ext4", used_bytes=61, free_bytes=39)],
        ),
    )
    end = base_ts + timedelta(minutes=6)
    rows = await query_repo.metric_trend(
        "fs.usage_percent", base_ts, end, "1m", server_ids=[sid], agg="avg", collapse=True
    )
    assert len(rows) >= 5, "표본 사이 버킷들도 LOCF 로 값이 채워져야 한다(gap 아님)"
    for r in rows:
        assert r.value is not None
        # 60/(60+40)=60% ~ 61/(61+39)=61% 사이 — undercount 되어 0% 근처로 튀면 안 됨.
        assert 55.0 <= r.value <= 65.0, f"버킷 값이 60~61% 범위를 벗어남(LOCF 왜곡 의심): {r.value}"


# ─── cpu.saturation / disk.saturation — 신규 서버 상세 이진(0/1) 포화 3축(엔지니어 보고서 포화 추이) ──────


async def test_cpu_saturation_crosses_when_run_queue_over_per_core_threshold(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """cpu.saturation — cpu.saturation_hosts(환경, crossing 서버 수)와 동일 원자료·임계, 서버 1대 이진 0/1.

    Linux 임계 PROCS_RUNNING_PER_CORE_SATURATION=1.0 — cpu_cores=4(make_inventory 기본) 이면 run_queue>=4 크로스.
    """
    base_ts = _bucket_aligned_base(minutes_ago=10)
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-cpusat-hi"))
    await collect_repo.record_metrics(
        sid, make_metrics(collected_at=base_ts, cpu_run_queue=5.0, filesystems=[], disk_io=[], net_io=[])
    )
    end = base_ts + timedelta(minutes=5)
    rows = await query_repo.metric_chart(
        server_id=sid, metric_type="cpu.saturation", dimension=None, time_range="15m", bucket="1m", agg="avg", end=end
    )
    assert rows
    values = {r.value for r in rows}
    assert values <= {0.0, 1.0}
    assert 1.0 in values, "run_queue/core(1.25) >= 임계 1.0 이면 포화로 판정돼야 한다"


async def test_cpu_saturation_stays_zero_under_threshold(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """cpu.saturation — run_queue/core 가 임계(1.0) 미만이면 항상 0.0(정상)."""
    base_ts = _bucket_aligned_base(minutes_ago=10)
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-cpusat-lo"))
    await collect_repo.record_metrics(
        sid, make_metrics(collected_at=base_ts, cpu_run_queue=1.0, filesystems=[], disk_io=[], net_io=[])
    )
    end = base_ts + timedelta(minutes=5)
    rows = await query_repo.metric_chart(
        server_id=sid, metric_type="cpu.saturation", dimension=None, time_range="15m", bucket="1m", agg="avg", end=end
    )
    assert rows
    assert all(r.value == 0.0 for r in rows), "run_queue/core(0.25) < 임계 1.0 이면 포화 판정이면 안 된다"


async def test_disk_saturation_crosses_when_await_over_threshold(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """disk.saturation — disk.saturation_hosts(환경, crossing 서버 수)와 동일 원자료·임계(RS_DISKIO_AWAIT_MS=20ms).

    60s 간격 delta_ops=100/delta_t=4.0s -> await=40ms(임계 20ms 초과), io_time delta=40s/60s=0.67 util 게이트 통과.
    """
    base_ts = _bucket_aligned_base(minutes_ago=10)
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-disksat-hi"))
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts,
            filesystems=[],
            net_io=[],
            disk_io=[DiskIoEntry(device_id=_DISK_DEVICE_ID, ops_read=100, op_read_time_s=1.0, io_time_s=0.0)],
        ),
    )
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts + timedelta(seconds=60),
            filesystems=[],
            net_io=[],
            disk_io=[DiskIoEntry(device_id=_DISK_DEVICE_ID, ops_read=200, op_read_time_s=5.0, io_time_s=40.0)],
        ),
    )
    end = base_ts + timedelta(minutes=5)
    rows = await query_repo.metric_chart(
        server_id=sid, metric_type="disk.saturation", dimension=None, time_range="15m", bucket="1m", agg="avg", end=end
    )
    assert rows
    values = {r.value for r in rows}
    assert values <= {0.0, 1.0}
    assert 1.0 in values, "await 40ms(임계 20ms 초과)가 포화로 판정돼야 한다"


async def test_disk_saturation_stays_zero_under_threshold(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """disk.saturation — await 가 임계(20ms) 미만이면 항상 0.0(정상)."""
    base_ts = _bucket_aligned_base(minutes_ago=10)
    sid = await collect_repo.upsert_server(make_inventory(composite_id="q-disksat-lo"))
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts,
            filesystems=[],
            net_io=[],
            disk_io=[DiskIoEntry(device_id=_DISK_DEVICE_ID, ops_read=100, op_read_time_s=1.0, io_time_s=0.0)],
        ),
    )
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=base_ts + timedelta(seconds=60),
            filesystems=[],
            net_io=[],
            # delta_ops=100, delta_t=0.5s -> await=5ms(임계 20ms 미만). io_time delta=40s -> util 게이트는 통과.
            disk_io=[DiskIoEntry(device_id=_DISK_DEVICE_ID, ops_read=200, op_read_time_s=1.5, io_time_s=40.0)],
        ),
    )
    end = base_ts + timedelta(minutes=5)
    rows = await query_repo.metric_chart(
        server_id=sid, metric_type="disk.saturation", dimension=None, time_range="15m", bucket="1m", agg="avg", end=end
    )
    assert rows
    assert all(r.value == 0.0 for r in rows), "await 5ms(임계 20ms 미만)는 포화 판정이면 안 된다"
