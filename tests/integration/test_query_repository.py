"""QueryRepository 통합 테스트 — Phase 1 정확화 검증.

검증 영역:
- inventory query (resolve_server_id, list_servers, get_server)
- _latest_per_dimension n=1/n=2 (get_storage, get_network, latest_dashboard)
- collection_status
- metric_chart dispatcher (17개 metric_type 모두 dispatch + 4개 helper SQL 정상 실행)
- metric_chart helper 결과 정확성 (cpu_delta LAG 계산, fs_usage 시점 값, rate_per_dim)
"""
from datetime import datetime, timedelta, timezone

import pytest

from assessment_engine.db.repositories.collect_repository import CollectRepository
from assessment_engine.db.repositories.inbound import (
    DiskIoEntry,
    MountUsageEntry,
    NetIoEntry,
)
from assessment_engine.db.repositories.query_repository import QueryRepository
from tests.factories import make_inventory, make_metrics


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
    "fs.usage_percent",
    "net.rx_bytes_per_sec",
    "net.tx_bytes_per_sec",
]


async def _seed_one_server_with_metrics(
    collect_repo: CollectRepository,
    machine_id: str = "q-001",
    n_points: int = 3,
) -> tuple[int, datetime]:
    """공통 fixture helper — server 1대 + n_points 시점의 metrics 시계열."""
    sid = await collect_repo.upsert_server(make_inventory(machine_id=machine_id))
    base_ts = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=n_points)

    for i in range(n_points):
        ts = base_ts + timedelta(minutes=i)
        m = make_metrics(
            collected_at=ts,
            # CPU jiffies 누적 — LAG delta가 양수가 되도록 시점마다 증가
            cpu_user=1000 + i * 100,
            cpu_idle=8000 + i * 800,
            disk_io=[
                DiskIoEntry(device="sda",
                            reads_completed=100 + i * 50,
                            writes_completed=50 + i * 25,
                            sectors_read=2000 + i * 1000,
                            sectors_written=1000 + i * 500),
            ],
            mounts=[
                MountUsageEntry(mount="/", total_bytes=50_000_000_000,
                                free_bytes=20_000_000_000, avail_bytes=18_000_000_000),
            ],
            net_io=[
                NetIoEntry(interface="eth0",
                           rx_bytes=1_000_000 + i * 100_000,
                           tx_bytes=500_000 + i * 50_000,
                           rx_packets=1000 + i * 100,
                           tx_packets=500 + i * 50,
                           rx_errors=0, tx_errors=0),
            ],
        )
        await collect_repo.record_metrics(sid, m)

    return sid, base_ts


# ─── inventory ────────────────────────────────────────────────────────────

async def test_resolve_server_id_existing(
    collect_repo: CollectRepository, query_repo: QueryRepository,
):
    sid = await collect_repo.upsert_server(make_inventory(machine_id="q-resolve-1"))
    inv_row = (await collect_repo.session.execute(
        __import__("sqlalchemy").text("SELECT public_id FROM server_inventory WHERE id = :id"),
        {"id": sid},
    )).scalar_one()
    resolved = await query_repo.resolve_server_id(str(inv_row))
    assert resolved == sid


async def test_resolve_server_id_missing(query_repo: QueryRepository):
    assert await query_repo.resolve_server_id("00000000-0000-0000-0000-000000000000") is None


async def test_list_servers_returns_with_last_seen_at(
    collect_repo: CollectRepository, query_repo: QueryRepository,
):
    """list_servers DTO에 last_seen_at 포함 — Redis fail-open fallback용."""
    await collect_repo.upsert_server(make_inventory(machine_id="q-list-1", hostname="host-a"))
    await collect_repo.upsert_server(make_inventory(machine_id="q-list-2", hostname="host-b"))
    rows = await query_repo.list_servers(page=1, limit=10, search=None)
    assert len(rows) >= 2
    for r in rows:
        assert r.last_seen_at is not None  # collected_at으로 자동 채워짐


async def test_list_servers_search_filter(
    collect_repo: CollectRepository, query_repo: QueryRepository,
):
    await collect_repo.upsert_server(make_inventory(machine_id="q-srch-1", hostname="alpha-server"))
    await collect_repo.upsert_server(make_inventory(machine_id="q-srch-2", hostname="beta-server"))
    rows = await query_repo.list_servers(page=1, limit=10, search="alpha")
    hostnames = [r.hostname for r in rows]
    assert any("alpha" in h for h in hostnames)
    assert not any("beta" in h for h in hostnames)


async def test_get_server_returns_full_detail(
    collect_repo: CollectRepository, query_repo: QueryRepository,
):
    sid = await collect_repo.upsert_server(
        make_inventory(machine_id="q-detail-1", hostname="detail-host", cpu_cores=12)
    )
    detail = await query_repo.get_server(sid)
    assert detail is not None
    assert detail.hostname == "detail-host"
    assert detail.cpu_cores == 12


async def test_get_server_missing(query_repo: QueryRepository):
    assert await query_repo.get_server(999_999) is None


# ─── _latest_per_dimension (n=1) — get_storage ────────────────────────────

async def test_get_storage_returns_only_latest_per_mount(
    collect_repo: CollectRepository, query_repo: QueryRepository,
):
    """같은 mount의 여러 시점 데이터 중 최신 1행만 반환 (DISTINCT ON)."""
    sid, _ = await _seed_one_server_with_metrics(collect_repo, machine_id="q-stor-1", n_points=3)
    storage = await query_repo.get_storage(sid)
    assert storage is not None
    # 시드는 mount="/" 1개만. 여러 시점이라도 mount당 1행.
    assert len(storage.mount_usage) == 1
    assert storage.mount_usage[0].mount == "/"


# ─── _latest_per_dimension (n=2) — get_network ────────────────────────────

async def test_get_network_returns_at_most_2_per_interface(
    collect_repo: CollectRepository, query_repo: QueryRepository,
):
    """delta 계산용 — interface당 최신 2행 (PARTITION BY + ROW_NUMBER)."""
    sid, _ = await _seed_one_server_with_metrics(collect_repo, machine_id="q-net-1", n_points=5)
    network = await query_repo.get_network(sid)
    assert network is not None
    # 시드는 interface="eth0" 1개. 5시점이지만 최신 2행.
    eth0_rows = [r for r in network.net_io if r.interface == "eth0"]
    assert len(eth0_rows) == 2


# ─── collection_status ────────────────────────────────────────────────────

async def test_collection_status_reports_both_timestamps(
    collect_repo: CollectRepository, query_repo: QueryRepository,
):
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, machine_id="q-cs-1", n_points=2)
    status = await query_repo.get_collection_status(sid)
    assert status is not None
    assert status.last_inventory_at is not None
    assert status.last_metric_at is not None


async def test_collection_status_missing_server(query_repo: QueryRepository):
    assert await query_repo.get_collection_status(999_999) is None


# ─── latest_dashboard — 3번의 _latest_per_dimension 통합 ──────────────────

async def test_latest_dashboard_returns_all_four_blocks(
    collect_repo: CollectRepository, query_repo: QueryRepository,
):
    sid, _ = await _seed_one_server_with_metrics(collect_repo, machine_id="q-dash-1", n_points=3)
    dash = await query_repo.latest_dashboard(sid)
    assert dash is not None
    # server_metrics는 최신 2행 (delta 계산용)
    assert len(dash.metrics) == 2
    # disk_io: device당 최신 2행 (1개 device × 2 = 2)
    assert len(dash.disk_io) == 2
    # net_io: interface당 최신 2행
    assert len(dash.net_io) == 2
    # mount_usage: mount당 1행
    assert len(dash.mounts) == 1


async def test_latest_dashboard_missing_server(query_repo: QueryRepository):
    assert await query_repo.latest_dashboard(999_999) is None


# ─── metric_chart dispatcher — 17개 metric_type 모두 무사 dispatch ────────

@pytest.mark.parametrize("metric_type", _ALL_METRIC_TYPES)
async def test_metric_chart_dispatcher_all_types(
    metric_type: str,
    collect_repo: CollectRepository, query_repo: QueryRepository,
):
    """모든 metric_type이 dispatcher를 통과하고 SQL이 정상 실행 — 결과는 비어도 OK."""
    sid, _ = await _seed_one_server_with_metrics(collect_repo, machine_id=f"q-mc-{metric_type}", n_points=3)
    rows = await query_repo.metric_chart(
        server_id=sid, metric_type=metric_type, dimension=None,
        time_range="1h", bucket="5m", agg="avg", end=None,
    )
    assert isinstance(rows, list)


# ─── metric_chart helper 결과 정확성 ──────────────────────────────────────

async def test_metric_chart_cpu_usage_percent_returns_data(
    collect_repo: CollectRepository, query_repo: QueryRepository,
):
    """_chart_cpu_delta — n_points=3이면 LAG로 d_total > 0인 행 2개 → 시간 버킷 ≥ 1."""
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, machine_id="q-cpu-1", n_points=3)
    end = base_ts + timedelta(minutes=10)
    rows = await query_repo.metric_chart(
        server_id=sid, metric_type="cpu.usage_percent", dimension=None,
        time_range="15m", bucket="1m", agg="avg", end=end,
    )
    # delta 가능한 시점이 ≥ 1 → 적어도 1개 버킷
    assert len(rows) >= 1
    for r in rows:
        if r.value is not None:
            assert 0 <= r.value <= 100


async def test_metric_chart_fs_usage_returns_per_mount(
    collect_repo: CollectRepository, query_repo: QueryRepository,
):
    """_chart_fs — dimension=mount, 시점 값. 각 행에 dimension 채워짐."""
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, machine_id="q-fs-1", n_points=2)
    end = base_ts + timedelta(minutes=10)
    rows = await query_repo.metric_chart(
        server_id=sid, metric_type="fs.usage_percent", dimension=None,
        time_range="15m", bucket="1m", agg="avg", end=end,
    )
    assert len(rows) >= 1
    for r in rows:
        assert r.dimension == "/"
        if r.value is not None:
            assert 0 <= r.value <= 100


async def test_metric_chart_disk_read_iops_per_device(
    collect_repo: CollectRepository, query_repo: QueryRepository,
):
    """_chart_rate_per_dimension — disk_io의 LAG/dt 기반 IOPS. dimension=device 채워짐."""
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, machine_id="q-dio-1", n_points=3)
    end = base_ts + timedelta(minutes=10)
    rows = await query_repo.metric_chart(
        server_id=sid, metric_type="disk.read_iops", dimension=None,
        time_range="15m", bucket="1m", agg="avg", end=end,
    )
    assert len(rows) >= 1
    for r in rows:
        assert r.dimension == "sda"
        if r.value is not None:
            assert r.value >= 0  # 음수 IOPS 없음


async def test_metric_chart_dimension_filter(
    collect_repo: CollectRepository, query_repo: QueryRepository,
):
    """dimension 파라미터로 특정 device만 필터."""
    sid = await collect_repo.upsert_server(make_inventory(machine_id="q-dim-1"))
    base_ts = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=3)
    for i in range(3):
        await collect_repo.record_metrics(sid, make_metrics(
            collected_at=base_ts + timedelta(minutes=i),
            disk_io=[
                DiskIoEntry(device="sda", reads_completed=100 + i*10, writes_completed=0, sectors_read=0, sectors_written=0),
                DiskIoEntry(device="sdb", reads_completed=200 + i*20, writes_completed=0, sectors_read=0, sectors_written=0),
            ],
            mounts=[], net_io=[],
        ))
    end = base_ts + timedelta(minutes=10)
    rows_sda = await query_repo.metric_chart(
        server_id=sid, metric_type="disk.read_iops", dimension="sda",
        time_range="15m", bucket="1m", agg="avg", end=end,
    )
    rows_all = await query_repo.metric_chart(
        server_id=sid, metric_type="disk.read_iops", dimension=None,
        time_range="15m", bucket="1m", agg="avg", end=end,
    )
    assert all(r.dimension == "sda" for r in rows_sda)
    assert any(r.dimension == "sdb" for r in rows_all)


# ─── metric_snapshots ─────────────────────────────────────────────────────

async def test_metric_snapshots_returns_timestamps(
    collect_repo: CollectRepository, query_repo: QueryRepository,
):
    sid, _ = await _seed_one_server_with_metrics(collect_repo, machine_id="q-snap-1", n_points=5)
    rows = await query_repo.metric_snapshots(sid, cursor=None, limit=10)
    assert len(rows) == 5
    for r in rows:
        assert r.collected_at is not None
        assert r.value is None and r.dimension is None  # timestamp 목록만


async def test_metric_snapshots_cursor_pagination(
    collect_repo: CollectRepository, query_repo: QueryRepository,
):
    """cursor 이전 timestamps만."""
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, machine_id="q-snap-2", n_points=4)
    cursor = base_ts + timedelta(minutes=2)
    rows = await query_repo.metric_snapshots(sid, cursor=cursor, limit=10)
    assert all(r.collected_at < cursor for r in rows)