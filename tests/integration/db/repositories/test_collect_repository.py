import pytest
from sqlalchemy import select

from db.models.server_entity import ServerEntity
from db.repositories.collect_repository import CollectRepository

pytestmark = pytest.mark.integration


class TestFindServer:
    async def test_not_found_returns_none(self, db_session):
        repo = CollectRepository(db_session)
        assert await repo.find_server("nonexistent") is None

    async def test_found_returns_id(self, db_session):
        repo = CollectRepository(db_session)
        server_id = await repo.create_server("find-me")
        await db_session.commit()

        result = await repo.find_server("find-me")
        assert result == server_id

    async def test_found_updates_updated_at(self, db_session):
        repo = CollectRepository(db_session)
        await repo.create_server("update-test")
        await db_session.commit()

        result = await db_session.execute(
            select(ServerEntity).where(ServerEntity.hostname == "update-test")
        )
        server = result.scalar_one()
        original_updated_at = server.updated_at

        await repo.find_server("update-test")
        await db_session.commit()
        await db_session.refresh(server)

        assert server.updated_at >= original_updated_at


class TestCreateServer:
    async def test_returns_integer_id(self, db_session):
        repo = CollectRepository(db_session)
        server_id = await repo.create_server("new-server")
        await db_session.commit()
        assert isinstance(server_id, int)
        assert server_id > 0

    async def test_persisted_to_db(self, db_session):
        repo = CollectRepository(db_session)
        server_id = await repo.create_server("persisted")
        await db_session.commit()

        result = await db_session.execute(
            select(ServerEntity).where(ServerEntity.id == server_id)
        )
        server = result.scalar_one_or_none()
        assert server is not None
        assert server.hostname == "persisted"

    async def test_duplicate_hostname_raises(self, db_session):
        from sqlalchemy.exc import IntegrityError

        repo = CollectRepository(db_session)
        await repo.create_server("dup")
        await db_session.commit()

        with pytest.raises(IntegrityError):
            await repo.create_server("dup")
            await db_session.commit()


class TestInsertMetric:
    async def test_metric_persisted(self, db_session):
        from sqlalchemy import select
        from db.models.metric_snapshot import MetricSnapshot

        repo = CollectRepository(db_session)
        server_id = await repo.create_server("metric-server")
        await repo.insert_metric(
            server_id=server_id,
            nproc=8,
            mem_total_mb=16384,
            disks=[{"name": "vda", "size": "100G"}],
            ip_internal=["192.168.1.1"],
            ip_external=["1.2.3.4"],
        )
        await db_session.commit()

        result = await db_session.execute(
            select(MetricSnapshot).where(MetricSnapshot.server_id == server_id)
        )
        metric: MetricSnapshot | None = result.scalar_one_or_none()
        assert metric is not None
        assert metric.nproc == 8
        assert metric.mem_total_mb == 16384
        assert metric.disks == [{"name": "vda", "size": "100G"}]
        assert metric.ip_internal == ["192.168.1.1"]
        assert metric.ip_external == ["1.2.3.4"]