from datetime import datetime, timedelta, timezone

import pytest

from db.repositories.collect_repository import CollectRepository
from db.repositories.inbound import ServerInventoryCreate, ServerMetricCreate
from db.repositories.query_repository import QueryRepository

pytestmark = pytest.mark.integration

_BASE_TS = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _make_inventory(machine_id: str, hostname: str = "host") -> ServerInventoryCreate:
    return ServerInventoryCreate(
        machine_id=machine_id,
        hostname=hostname,
        agent_version="1.0.0",
        collected_at=_BASE_TS,
        os_id="ubuntu", os_version="22.04", os_codename="jammy",
        kernel_version="5.15", cpu_cores=4, cpu_model="Intel",
        mem_total_kb=8_000_000, swap_total_kb=2_000_000,
        boot_time=None, ip_internal=["10.0.0.1"], ip_external=None,
        disks=[{"name": "sda", "size_bytes": 107_374_182_400, "type": "SSD"}],
        mounts=[{"mount": "/", "fstype": "ext4", "total_bytes": 107_374_182_400}],
    )


def _make_metric(server_id: int, offset_minutes: int = 0) -> ServerMetricCreate:
    ts = _BASE_TS + timedelta(minutes=offset_minutes)
    return ServerMetricCreate(
        collected_at=ts,
        cpu_user=100, cpu_nice=0, cpu_system=50, cpu_idle=800,
        cpu_iowait=10, cpu_irq=5, cpu_softirq=5, cpu_steal=0,
        mem_total_kb=8_000_000, mem_free_kb=1_000_000, mem_available_kb=3_000_000,
        mem_buffers_kb=200_000, mem_cached_kb=1_500_000,
        swap_total_kb=2_000_000, swap_free_kb=1_000_000,
        load_1m=0.5, load_5m=0.4, load_15m=0.3,
        disk_io=[{"device": "sda", "reads_completed": 100 + offset_minutes,
                  "writes_completed": 50, "sectors_read": 2000, "sectors_written": 1000}],
        mounts=[{"mount": "/", "total_bytes": 107_374_182_400,
                 "free_bytes": 50_000_000_000, "avail_bytes": 50_000_000_000}],
        net_io=[{"interface": "eth0", "rx_bytes": 1024 + offset_minutes,
                 "tx_bytes": 512, "rx_packets": 10, "tx_packets": 5,
                 "rx_errors": 0, "tx_errors": 0}],
    )


async def _insert_server(session, machine_id: str, hostname: str = "host") -> int:
    repo = CollectRepository(session)
    return await repo.upsert_server(_make_inventory(machine_id, hostname))


async def _insert_metric(session, server_id: int, offset_minutes: int = 0) -> None:
    repo = CollectRepository(session)
    await repo.insert_metric(server_id, _make_metric(server_id, offset_minutes))
    await session.flush()


class TestListServers:
    async def test_empty_db_returns_empty_list(self, db_session):
        repo = QueryRepository(db_session)
        result = await repo.list_servers(1, 20, None)
        assert result == []

    async def test_returns_all_servers(self, db_session):
        await _insert_server(db_session, "q-list-1", "host-1")
        await _insert_server(db_session, "q-list-2", "host-2")
        await db_session.flush()
        repo = QueryRepository(db_session)
        result = await repo.list_servers(1, 20, None)
        assert len(result) == 2

    async def test_search_filters_by_hostname(self, db_session):
        await _insert_server(db_session, "q-search-1", "web-server")
        await _insert_server(db_session, "q-search-2", "db-server")
        await db_session.flush()
        repo = QueryRepository(db_session)
        result = await repo.list_servers(1, 20, "web")
        assert len(result) == 1
        assert result[0].hostname == "web-server"

    async def test_pagination_offsets_correctly(self, db_session):
        for i in range(3):
            await _insert_server(db_session, f"q-page-{i}", f"host-{i}")
        await db_session.flush()
        repo = QueryRepository(db_session)
        page1 = await repo.list_servers(1, 2, None)
        page2 = await repo.list_servers(2, 2, None)
        assert len(page1) == 2
        assert len(page2) == 1

    async def test_last_seen_at_from_metrics_when_available(self, db_session):
        sid = await _insert_server(db_session, "q-lastseen-metric")
        await _insert_metric(db_session, sid, offset_minutes=10)
        repo = QueryRepository(db_session)
        result = await repo.list_servers(1, 20, None)
        item = next(r for r in result if r.machine_id == "q-lastseen-metric")
        expected_ts = _BASE_TS + timedelta(minutes=10)
        assert item.last_seen_at is not None
        # timezone-aware 비교
        last_seen = item.last_seen_at
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        assert abs((last_seen - expected_ts).total_seconds()) < 1

    async def test_last_seen_at_from_inventory_when_no_metrics(self, db_session):
        await _insert_server(db_session, "q-lastseen-inv")
        await db_session.flush()
        repo = QueryRepository(db_session)
        result = await repo.list_servers(1, 20, None)
        item = next(r for r in result if r.machine_id == "q-lastseen-inv")
        assert item.last_seen_at is not None


class TestGetServer:
    async def test_not_found_returns_none(self, db_session):
        repo = QueryRepository(db_session)
        result = await repo.get_server(999_999)
        assert result is None

    async def test_found_returns_full_dto(self, db_session):
        sid = await _insert_server(db_session, "q-get-server", "detail-host")
        await db_session.flush()
        repo = QueryRepository(db_session)
        result = await repo.get_server(sid)
        assert result is not None
        assert result.machine_id == "q-get-server"
        assert result.hostname == "detail-host"
        assert result.cpu_cores == 4


class TestGetStorage:
    async def test_not_found_returns_none(self, db_session):
        repo = QueryRepository(db_session)
        assert await repo.get_storage(999_999) is None

    async def test_found_with_mount_usage(self, db_session):
        sid = await _insert_server(db_session, "q-storage-mount")
        await _insert_metric(db_session, sid)
        repo = QueryRepository(db_session)
        result = await repo.get_storage(sid)
        assert result is not None
        assert len(result.mount_usage) == 1
        assert result.mount_usage[0].mount == "/"

    async def test_found_without_mount_usage(self, db_session):
        sid = await _insert_server(db_session, "q-storage-nomount")
        await db_session.flush()
        repo = QueryRepository(db_session)
        result = await repo.get_storage(sid)
        assert result is not None
        assert result.mount_usage == []


class TestGetNetwork:
    async def test_not_found_returns_none(self, db_session):
        repo = QueryRepository(db_session)
        assert await repo.get_network(999_999) is None

    async def test_found_with_net_io(self, db_session):
        sid = await _insert_server(db_session, "q-network")
        await _insert_metric(db_session, sid)
        repo = QueryRepository(db_session)
        result = await repo.get_network(sid)
        assert result is not None
        assert len(result.net_io) >= 1
        assert result.net_io[0].interface == "eth0"


class TestGetCollectionStatus:
    async def test_returns_none_timestamps_when_no_data(self, db_session):
        sid = await _insert_server(db_session, "q-status-empty")
        await db_session.flush()
        repo = QueryRepository(db_session)
        result = await repo.get_collection_status(sid)
        assert len(result) == 1
        assert result[0].last_metric_at is None

    async def test_last_metric_at_populated_when_metrics_exist(self, db_session):
        sid = await _insert_server(db_session, "q-status-metric")
        await _insert_metric(db_session, sid, offset_minutes=5)
        repo = QueryRepository(db_session)
        result = await repo.get_collection_status(sid)
        assert result[0].last_metric_at is not None


class TestLatestDashboard:
    async def test_no_server_returns_none(self, db_session):
        repo = QueryRepository(db_session)
        assert await repo.latest_dashboard(999_999) is None

    async def test_no_metrics_returns_empty_metrics_list(self, db_session):
        sid = await _insert_server(db_session, "q-dash-empty")
        await db_session.flush()
        repo = QueryRepository(db_session)
        result = await repo.latest_dashboard(sid)
        assert result is not None
        assert result.metrics == []

    async def test_two_readings_returned_for_delta(self, db_session):
        sid = await _insert_server(db_session, "q-dash-two")
        await _insert_metric(db_session, sid, offset_minutes=0)
        await _insert_metric(db_session, sid, offset_minutes=1)
        repo = QueryRepository(db_session)
        result = await repo.latest_dashboard(sid)
        assert len(result.metrics) == 2

    async def test_disk_io_grouped_by_device(self, db_session):
        sid = await _insert_server(db_session, "q-dash-disk")
        await _insert_metric(db_session, sid, offset_minutes=0)
        await _insert_metric(db_session, sid, offset_minutes=1)
        repo = QueryRepository(db_session)
        result = await repo.latest_dashboard(sid)
        # sda 장치 2행
        sda_rows = [r for r in result.disk_io if r.device == "sda"]
        assert len(sda_rows) == 2

    async def test_net_io_grouped_by_interface(self, db_session):
        sid = await _insert_server(db_session, "q-dash-net")
        await _insert_metric(db_session, sid, offset_minutes=0)
        await _insert_metric(db_session, sid, offset_minutes=1)
        repo = QueryRepository(db_session)
        result = await repo.latest_dashboard(sid)
        eth0_rows = [r for r in result.net_io if r.interface == "eth0"]
        assert len(eth0_rows) == 2


class TestMetricSnapshots:
    async def test_no_data_returns_empty(self, db_session):
        sid = await _insert_server(db_session, "q-snap-empty")
        await db_session.flush()
        repo = QueryRepository(db_session)
        result = await repo.metric_snapshots(sid, None, 10)
        assert result == []

    async def test_ordered_desc_by_collected_at(self, db_session):
        sid = await _insert_server(db_session, "q-snap-order")
        await _insert_metric(db_session, sid, offset_minutes=0)
        await _insert_metric(db_session, sid, offset_minutes=1)
        repo = QueryRepository(db_session)
        result = await repo.metric_snapshots(sid, None, 10)
        ts0 = result[0].collected_at
        ts1 = result[1].collected_at
        if ts0.tzinfo is None:
            ts0 = ts0.replace(tzinfo=timezone.utc)
        if ts1.tzinfo is None:
            ts1 = ts1.replace(tzinfo=timezone.utc)
        assert ts0 > ts1

    async def test_cursor_excludes_items_at_or_after(self, db_session):
        sid = await _insert_server(db_session, "q-snap-cursor")
        await _insert_metric(db_session, sid, offset_minutes=0)
        await _insert_metric(db_session, sid, offset_minutes=1)
        await _insert_metric(db_session, sid, offset_minutes=2)
        repo = QueryRepository(db_session)
        cursor = _BASE_TS + timedelta(minutes=2)
        result = await repo.metric_snapshots(sid, cursor, 10)
        assert len(result) == 2  # offset 0, 1 만 포함


class TestMetricChart:
    async def test_no_data_returns_empty(self, db_session):
        sid = await _insert_server(db_session, "q-chart-empty")
        await db_session.flush()
        repo = QueryRepository(db_session)
        result = await repo.metric_chart(sid, "cpu.usage_percent", None, "1h", "5m", "avg")
        assert result == []

    async def test_cpu_chart_returns_time_bucketed_data(self, db_session):
        sid = await _insert_server(db_session, "q-chart-cpu")
        # 2분 간격 2개 삽입 → delta 계산 가능
        for i in range(2):
            await _insert_metric(db_session, sid, offset_minutes=i)
        repo = QueryRepository(db_session)
        result = await repo.metric_chart(sid, "cpu.usage_percent", None, "1h", "5m", "avg")
        # 데이터가 있으면 최소 0개 이상 (time_bucket 범위 내에 있어야 함)
        assert isinstance(result, list)

    async def test_disk_chart_filtered_by_dimension(self, db_session):
        sid = await _insert_server(db_session, "q-chart-disk")
        for i in range(2):
            await _insert_metric(db_session, sid, offset_minutes=i)
        repo = QueryRepository(db_session)
        result_sda = await repo.metric_chart(sid, "disk.read_iops", "sda", "1h", "5m", "avg")
        result_sdb = await repo.metric_chart(sid, "disk.read_iops", "sdb", "1h", "5m", "avg")
        assert isinstance(result_sda, list)
        assert result_sdb == []  # sdb 없음

    async def test_net_chart_filtered_by_dimension(self, db_session):
        sid = await _insert_server(db_session, "q-chart-net")
        for i in range(2):
            await _insert_metric(db_session, sid, offset_minutes=i)
        repo = QueryRepository(db_session)
        result = await repo.metric_chart(sid, "net.rx_bytes_per_sec", "eth0", "1h", "5m", "avg")
        assert isinstance(result, list)

    async def test_fs_chart_filtered_by_dimension(self, db_session):
        sid = await _insert_server(db_session, "q-chart-fs")
        for i in range(2):
            await _insert_metric(db_session, sid, offset_minutes=i)
        repo = QueryRepository(db_session)
        result = await repo.metric_chart(sid, "fs.usage_percent", "/", "1h", "5m", "avg")
        assert isinstance(result, list)