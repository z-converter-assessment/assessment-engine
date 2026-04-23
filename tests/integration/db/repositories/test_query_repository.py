import asyncio

import pytest

from db.repositories.collect_repository import CollectRepository
from db.repositories.query_repository import QueryRepository

pytestmark = pytest.mark.integration

DISKS = [{"name": "vda", "size": "30G"}]
IPS = {"internal": ["10.0.0.1"], "external": ["1.2.3.4"]}


async def _seed_server(session, hostname: str) -> int:
    repo = CollectRepository(session)
    server_id = await repo.create_server(hostname)
    await session.commit()
    return server_id


async def _seed_metric(session, server_id: int) -> None:
    repo = CollectRepository(session)
    await repo.insert_metric(
        server_id=server_id,
        nproc=4,
        mem_total_mb=8192,
        disks=DISKS,
        ip_internal=IPS["internal"],
        ip_external=IPS["external"],
    )
    await session.commit()


class TestGetById:
    async def test_not_found_returns_none(self, db_session):
        repo = QueryRepository(db_session)
        assert await repo.get_by_id(9999) is None

    async def test_found_returns_dto(self, db_session):
        server_id = await _seed_server(db_session, "get-by-id")
        repo = QueryRepository(db_session)
        result = await repo.get_by_id(server_id)
        assert result is not None
        assert result.id == server_id
        assert result.hostname == "get-by-id"


class TestListAll:
    async def test_empty_returns_empty_list(self, db_session):
        repo = QueryRepository(db_session)
        assert await repo.list_all() == []

    async def test_returns_all_servers(self, db_session):
        await _seed_server(db_session, "server-a")
        await _seed_server(db_session, "server-b")

        repo = QueryRepository(db_session)
        result = await repo.list_all()
        assert len(result) == 2

    async def test_sorted_by_hostname(self, db_session):
        await _seed_server(db_session, "z-server")
        await _seed_server(db_session, "a-server")
        await _seed_server(db_session, "m-server")

        repo = QueryRepository(db_session)
        result = await repo.list_all()
        hostnames = [s.hostname for s in result]
        assert hostnames == sorted(hostnames)


class TestLatestMetric:
    async def test_no_metrics_returns_none(self, db_session):
        server_id = await _seed_server(db_session, "no-metrics")
        repo = QueryRepository(db_session)
        assert await repo.latest_metric(server_id) is None

    async def test_returns_most_recent_metric(self, db_session):
        server_id = await _seed_server(db_session, "latest-metric")
        await _seed_metric(db_session, server_id)
        await asyncio.sleep(0.01)
        await _seed_metric(db_session, server_id)

        repo = QueryRepository(db_session)
        result = await repo.latest_metric(server_id)
        assert result is not None
        assert result.server_id == server_id

    async def test_dto_fields_populated(self, db_session):
        server_id = await _seed_server(db_session, "dto-fields")
        await _seed_metric(db_session, server_id)

        repo = QueryRepository(db_session)
        result = await repo.latest_metric(server_id)
        assert result.nproc == 4
        assert result.mem_total_mb == 8192
        assert result.disks == DISKS
        assert result.ip_internal == IPS["internal"]
        assert result.ip_external == IPS["external"]


class TestMetricHistory:
    async def test_no_metrics_returns_empty_list(self, db_session):
        server_id = await _seed_server(db_session, "no-history")
        repo = QueryRepository(db_session)
        assert await repo.metric_history(server_id) == []

    async def test_returns_all_metrics(self, db_session):
        server_id = await _seed_server(db_session, "multi-metrics")
        await _seed_metric(db_session, server_id)
        await _seed_metric(db_session, server_id)
        await _seed_metric(db_session, server_id)

        repo = QueryRepository(db_session)
        result = await repo.metric_history(server_id)
        assert len(result) == 3

    async def test_ordered_by_recorded_at_desc(self, db_session):
        server_id = await _seed_server(db_session, "ordered-history")
        for _ in range(3):
            await _seed_metric(db_session, server_id)
            await asyncio.sleep(0.01)

        repo = QueryRepository(db_session)
        result = await repo.metric_history(server_id)
        recorded_ats = [m.recorded_at for m in result]
        assert recorded_ats == sorted(recorded_ats, reverse=True)