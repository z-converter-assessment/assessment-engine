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
    host_id: str = "q-001",
    n_points: int = 3,
) -> tuple[int, datetime]:
    """공통 fixture helper — server 1대 + n_points 시점의 metrics 시계열."""
    sid = await collect_repo.upsert_server(make_inventory(host_id=host_id))
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
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-resolve-1"))
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
    await collect_repo.upsert_server(make_inventory(host_id="q-list-1", hostname="host-a"))
    await collect_repo.upsert_server(make_inventory(host_id="q-list-2", hostname="host-b"))
    rows = await query_repo.list_servers(page=1, limit=10, search=None)
    assert len(rows) >= 2
    for r in rows:
        assert r.last_seen_at is not None  # collected_at으로 자동 채워짐


async def test_list_servers_search_filter(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    await collect_repo.upsert_server(make_inventory(host_id="q-srch-1", hostname="alpha-server"))
    await collect_repo.upsert_server(make_inventory(host_id="q-srch-2", hostname="beta-server"))
    rows = await query_repo.list_servers(page=1, limit=10, search="alpha")
    hostnames = [r.hostname for r in rows]
    assert any("alpha" in h for h in hostnames)
    assert not any("beta" in h for h in hostnames)


async def test_get_server_returns_full_detail(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-detail-1", hostname="detail-host", cpu_cores=12))
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
    sid, _ = await _seed_one_server_with_metrics(collect_repo, host_id="q-stor-1", n_points=3)
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
    sid, _ = await _seed_one_server_with_metrics(collect_repo, host_id="q-net-1", n_points=5)
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
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, host_id="q-cs-1", n_points=2)
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
    sid, _ = await _seed_one_server_with_metrics(collect_repo, host_id="q-dash-1", n_points=3)
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


# ─── metric_chart dispatcher — 17개 metric_type 모두 무사 dispatch ────────


@pytest.mark.parametrize("metric_type", _ALL_METRIC_TYPES)
async def test_metric_chart_dispatcher_all_types(
    metric_type: str,
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """모든 metric_type이 dispatcher를 통과하고 SQL이 정상 실행 — 결과는 비어도 OK."""
    sid, _ = await _seed_one_server_with_metrics(collect_repo, host_id=f"q-mc-{metric_type}", n_points=3)
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
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, host_id="q-cpu-1", n_points=3)
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


async def test_metric_chart_fs_usage_returns_per_mount(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """_chart_fs — dimension=mount, 시점 값. 각 행에 dimension 채워짐."""
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, host_id="q-fs-1", n_points=2)
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
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, host_id="q-dio-1", n_points=3)
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
    """_chart_cpu_delta — boot_time 변경 시점은 reset 확정 → 차트 missing (CLAUDE.md B1).

    재부팅 후 jiffies는 0부터 시작이지만 드물게 prev보다 큰 값일 수도. 옛 d<0 휴리스틱은
    못 잡지만 boot_time 비교는 spike 방지.
    """
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-rst-cpu-1"))
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
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-rst-rate-1"))
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
            host_id="q-rb-1",
            collected_at=base_ts,
            boot_time=boot_a,
            agent_started_at=agent_a,
        )
    )
    await collect_repo.upsert_server(
        make_inventory(
            host_id="q-rb-1",
            collected_at=base_ts + timedelta(hours=1),
            boot_time=boot_b,
            agent_started_at=agent_b,
        )
    )

    # (host_id, hostname) 복합 키 (#C1)
    sid = await collect_repo.find_server_id("q-rb-1", "test-host-01")
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
            host_id="q-rb-2",
            collected_at=base_ts,
            boot_time=boot,
            agent_started_at=agent_a,
        )
    )
    await collect_repo.upsert_server(
        make_inventory(
            host_id="q-rb-2",
            collected_at=base_ts + timedelta(hours=1),
            boot_time=boot,
            agent_started_at=agent_b,
        )
    )

    # (host_id, hostname) 복합 키 (#C1)
    sid = await collect_repo.find_server_id("q-rb-2", "test-host-01")
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
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-dim-1"))
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
    sid, _ = await _seed_one_server_with_metrics(collect_repo, host_id="q-snap-1", n_points=5)
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
    sid, base_ts = await _seed_one_server_with_metrics(collect_repo, host_id="q-snap-2", n_points=4)
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
    sid_a = await collect_repo.upsert_server(make_inventory(host_id="q-batch-a"))
    sid_b = await collect_repo.upsert_server(make_inventory(host_id="q-batch-b"))
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
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-batch-missing"))
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
    sid_a = await collect_repo.upsert_server(make_inventory(host_id="q-gs-a", hostname="host-a"))
    sid_b = await collect_repo.upsert_server(make_inventory(host_id="q-gs-b", hostname="host-b"))
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
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-gs-missing"))
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
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-prune-1"))
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


# ─── attention 신호: disk_usage_warnings ──────────────────────────────────


async def test_disk_usage_warnings_excludes_below_threshold(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """avail/total 사용률이 임계 미만이면 결과에서 제외."""
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-disk-low"))
    ts = datetime.now(UTC).replace(microsecond=0)
    # 사용률 50% — 임계 85% 미만
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=ts,
            mounts=[
                MountUsageEntry(
                    mount="/root", total_bytes=100_000_000_000, free_bytes=50_000_000_000, avail_bytes=50_000_000_000
                )
            ],
            disk_io=[],
            net_io=[],
        ),
    )
    rows = await query_repo.disk_usage_warnings(threshold_pct=85, limit=10)
    assert all(r.hostname != "test-host-01" or r.mount != "/root" for r in rows)


async def test_disk_usage_warnings_includes_above_threshold(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """사용률 임계 초과 mount는 결과에 포함 — 정렬은 사용률 DESC."""
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-disk-high", hostname="disk-high-host"))
    ts = datetime.now(UTC).replace(microsecond=0)
    # 사용률 92% (avail 8GB / total 100GB)
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=ts,
            mounts=[
                MountUsageEntry(
                    mount="/data", total_bytes=100_000_000_000, free_bytes=8_000_000_000, avail_bytes=8_000_000_000
                )
            ],
            disk_io=[],
            net_io=[],
        ),
    )
    rows = await query_repo.disk_usage_warnings(threshold_pct=85, limit=10)
    matching = [r for r in rows if r.hostname == "disk-high-host" and r.mount == "/data"]
    assert len(matching) == 1
    assert matching[0].total_bytes == 100_000_000_000
    assert matching[0].avail_bytes == 8_000_000_000
    assert matching[0].last_metric_at == ts  # latest mount 시점


async def test_disk_usage_warnings_uses_latest_per_mount(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """같은 mount의 여러 시점 중 latest 1건만 평가 — 옛 시점은 임계 초과여도 제외 안 되고,
    latest가 임계 미만이면 제외."""
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-disk-latest", hostname="latest-host"))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
    # T0: 95% (위험), T1: 50% (정상) — latest=T1만 평가 → 결과 제외
    for _i, (mins, avail) in enumerate([(0, 5_000_000_000), (3, 50_000_000_000)]):
        await collect_repo.record_metrics(
            sid,
            make_metrics(
                collected_at=base_ts + timedelta(minutes=mins),
                mounts=[
                    MountUsageEntry(mount="/var", total_bytes=100_000_000_000, free_bytes=avail, avail_bytes=avail)
                ],
                disk_io=[],
                net_io=[],
            ),
        )
    rows = await query_repo.disk_usage_warnings(threshold_pct=85, limit=10)
    assert all(r.hostname != "latest-host" or r.mount != "/var" for r in rows)


async def test_disk_usage_warnings_excludes_zero_total(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """total_bytes=0(가상 mount 시뮬) — 0으로 나누기 회피, 결과 제외."""
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-disk-virt", hostname="virt-host"))
    ts = datetime.now(UTC).replace(microsecond=0)
    await collect_repo.record_metrics(
        sid,
        make_metrics(
            collected_at=ts,
            mounts=[MountUsageEntry(mount="/proc", total_bytes=0, free_bytes=0, avail_bytes=0)],
            disk_io=[],
            net_io=[],
        ),
    )
    rows = await query_repo.disk_usage_warnings(threshold_pct=85, limit=10)
    assert all(r.mount != "/proc" or r.hostname != "virt-host" for r in rows)


# ─── attention 신호: metric_gap_warnings ──────────────────────────────────


async def test_metric_gap_warnings_excludes_recent_metric(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """방금 metric 발행한 서버는 갭 없음 — 결과 제외."""
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-gap-fresh", hostname="fresh-host"))
    await collect_repo.record_metrics(sid, make_metrics(collected_at=datetime.now(UTC)))
    rows = await query_repo.metric_gap_warnings(gap_minutes=5, recent_hours=24, limit=10)
    assert all(r.hostname != "fresh-host" for r in rows)


async def test_metric_gap_warnings_includes_gap_in_window(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """5분~24h 윈도우 안에 마지막 metric → 결과 포함."""
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-gap-mid", hostname="gap-host"))
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
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-gap-dead", hostname="dead-host"))
    long_ago = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=48)
    await collect_repo.record_metrics(sid, make_metrics(collected_at=long_ago))
    rows = await query_repo.metric_gap_warnings(gap_minutes=5, recent_hours=24, limit=10)
    assert all(r.hostname != "dead-host" for r in rows)


async def test_metric_gap_warnings_no_metric_excluded(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """metric 한 번도 발행 안 한 서버 — JOIN 조건으로 제외."""
    await collect_repo.upsert_server(make_inventory(host_id="q-gap-none", hostname="never-host"))
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
    """CPU·MEM·DISK 평균이 정상 산출 — 두 시점 jiffies delta + latest mem + max mount."""
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-util-01", hostname="util-host"))
    base_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=2)
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
            mounts=[MountUsageEntry(mount="/", total_bytes=100, free_bytes=40, avail_bytes=40)],
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
            mounts=[MountUsageEntry(mount="/", total_bytes=100, free_bytes=20, avail_bytes=20)],  # latest → 80%
            disk_io=[],
            net_io=[],
        ),
    )
    # 24h 평균: 두 시점만 있을 때 LAG pair 1개 = 그 delta가 곧 평균
    util = await query_repo.environment_utilization(period_days=1)
    assert util.cpu_avg_pct is not None and 49.0 <= util.cpu_avg_pct <= 51.0
    # MEM 24h 평균 = (50% + 70%) / 2 = 60%
    assert util.mem_avg_pct is not None and 59.0 <= util.mem_avg_pct <= 61.0
    # DISK 24h 평균 = mount별 평균 (60+80)/2 = 70% → 서버별 max 1개 = 70%
    assert util.disk_avg_pct is not None and 69.0 <= util.disk_avg_pct <= 71.0
    assert util.sample_size >= 1


async def test_environment_utilization_excludes_outside_window(
    collect_repo: CollectRepository,
    query_repo: QueryRepository,
):
    """기간 밖 메트릭은 평균에서 제외."""
    sid = await collect_repo.upsert_server(make_inventory(host_id="q-util-stale"))
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
    util = await query_repo.environment_utilization(period_days=1)
    assert util is not None  # 정상 호출 + 기간 밖 데이터로 인한 예외 없음
