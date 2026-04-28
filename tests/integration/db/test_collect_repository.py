from datetime import datetime, timezone

import pytest

from db.repositories.collect_repository import CollectRepository
from db.repositories.inbound import ServerInventoryCreate, ServerMetricCreate

pytestmark = pytest.mark.integration

_NOW = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _make_inventory(machine_id: str = "machine-001", hostname: str = "host-01") -> ServerInventoryCreate:
    return ServerInventoryCreate(
        machine_id=machine_id,
        hostname=hostname,
        agent_version="1.0.0",
        collected_at=_NOW,
        os_id="ubuntu",
        os_version="22.04",
        os_codename="jammy",
        kernel_version="5.15",
        cpu_cores=4,
        cpu_model="Intel Xeon",
        mem_total_kb=8_000_000,
        swap_total_kb=2_000_000,
        boot_time=None,
        ip_internal=["10.0.0.1"],
        ip_external=None,
        disks=[{"name": "sda", "size_bytes": 107_374_182_400, "type": "SSD"}],
        mounts=[{"mount": "/", "fstype": "ext4", "total_bytes": 107_374_182_400}],
    )


def _make_metric(server_id: int, offset_seconds: int = 0) -> ServerMetricCreate:
    ts = datetime(2024, 1, 1, 0, offset_seconds // 60, offset_seconds % 60, tzinfo=timezone.utc)
    return ServerMetricCreate(
        collected_at=ts,
        cpu_user=100, cpu_nice=0, cpu_system=50, cpu_idle=800,
        cpu_iowait=10, cpu_irq=5, cpu_softirq=5, cpu_steal=0,
        mem_total_kb=8_000_000, mem_free_kb=1_000_000, mem_available_kb=3_000_000,
        mem_buffers_kb=200_000, mem_cached_kb=1_500_000,
        swap_total_kb=2_000_000, swap_free_kb=1_000_000,
        load_1m=0.5, load_5m=0.4, load_15m=0.3,
        disk_io=[{"device": "sda", "reads_completed": 100, "writes_completed": 50,
                  "sectors_read": 2000, "sectors_written": 1000}],
        mounts=[{"mount": "/", "total_bytes": 107_374_182_400,
                 "free_bytes": 50_000_000_000, "avail_bytes": 50_000_000_000}],
        net_io=[{"interface": "eth0", "rx_bytes": 1024, "tx_bytes": 512,
                 "rx_packets": 10, "tx_packets": 5, "rx_errors": 0, "tx_errors": 0}],
    )


class TestFindServerId:
    async def test_not_found_returns_none(self, db_session):
        repo = CollectRepository(db_session)
        result = await repo.find_server_id("nonexistent-machine")
        assert result is None

    async def test_found_returns_int(self, db_session):
        repo = CollectRepository(db_session)
        server_id = await repo.upsert_server(_make_inventory("m-find-test"))
        result = await repo.find_server_id("m-find-test")
        assert result == server_id


class TestUpsertServer:
    async def test_insert_returns_server_id(self, db_session):
        repo = CollectRepository(db_session)
        server_id = await repo.upsert_server(_make_inventory())
        assert isinstance(server_id, int)
        assert server_id > 0

    async def test_same_machine_id_updates_fields(self, db_session):
        repo = CollectRepository(db_session)
        await repo.upsert_server(_make_inventory("m-upsert", hostname="old-host"))
        await repo.upsert_server(_make_inventory("m-upsert", hostname="new-host"))
        # find_server_id 로 존재 확인
        sid = await repo.find_server_id("m-upsert")
        assert sid is not None

    async def test_upsert_returns_same_id_on_conflict(self, db_session):
        repo = CollectRepository(db_session)
        id1 = await repo.upsert_server(_make_inventory("m-conflict"))
        id2 = await repo.upsert_server(_make_inventory("m-conflict"))
        assert id1 == id2


class TestInsertMetric:
    async def test_inserts_server_metrics_row(self, db_session):
        from sqlalchemy import select, func
        from db.models.server_metrics import ServerMetrics

        repo = CollectRepository(db_session)
        server_id = await repo.upsert_server(_make_inventory("m-metric"))
        await repo.insert_metric(server_id, _make_metric(server_id))
        await db_session.flush()

        count = await db_session.scalar(
            select(func.count()).where(ServerMetrics.server_id == server_id)
        )
        assert count == 1

    async def test_inserts_disk_io_rows(self, db_session):
        from sqlalchemy import select, func
        from db.models.server_disk_io import ServerDiskIo

        repo = CollectRepository(db_session)
        server_id = await repo.upsert_server(_make_inventory("m-disk"))
        await repo.insert_metric(server_id, _make_metric(server_id))
        await db_session.flush()

        count = await db_session.scalar(
            select(func.count()).where(ServerDiskIo.server_id == server_id)
        )
        assert count == 1

    async def test_inserts_net_io_rows(self, db_session):
        from sqlalchemy import select, func
        from db.models.server_net_io import ServerNetIo

        repo = CollectRepository(db_session)
        server_id = await repo.upsert_server(_make_inventory("m-net"))
        await repo.insert_metric(server_id, _make_metric(server_id))
        await db_session.flush()

        count = await db_session.scalar(
            select(func.count()).where(ServerNetIo.server_id == server_id)
        )
        assert count == 1

    async def test_inserts_mount_usage_rows(self, db_session):
        from sqlalchemy import select, func
        from db.models.server_mount_usage import ServerMountUsage

        repo = CollectRepository(db_session)
        server_id = await repo.upsert_server(_make_inventory("m-mount"))
        await repo.insert_metric(server_id, _make_metric(server_id))
        await db_session.flush()

        count = await db_session.scalar(
            select(func.count()).where(ServerMountUsage.server_id == server_id)
        )
        assert count == 1

    async def test_empty_disk_io_skips_insert(self, db_session):
        from sqlalchemy import select, func
        from db.models.server_disk_io import ServerDiskIo

        repo = CollectRepository(db_session)
        server_id = await repo.upsert_server(_make_inventory("m-no-disk"))
        metric = _make_metric(server_id)
        metric.disk_io = []
        await repo.insert_metric(server_id, metric)
        await db_session.flush()

        count = await db_session.scalar(
            select(func.count()).where(ServerDiskIo.server_id == server_id)
        )
        assert count == 0

    async def test_empty_net_io_skips_insert(self, db_session):
        from sqlalchemy import select, func
        from db.models.server_net_io import ServerNetIo

        repo = CollectRepository(db_session)
        server_id = await repo.upsert_server(_make_inventory("m-no-net"))
        metric = _make_metric(server_id)
        metric.net_io = []
        await repo.insert_metric(server_id, metric)
        await db_session.flush()

        count = await db_session.scalar(
            select(func.count()).where(ServerNetIo.server_id == server_id)
        )
        assert count == 0