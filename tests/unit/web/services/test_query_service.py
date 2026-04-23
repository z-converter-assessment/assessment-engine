from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

import pytest

from db.repositories.dto import ServerDTO, ServerMetricDTO
from web.api.items import DiskInfo
from web.services.query_service import QueryService, _fmt, _real_disks

_KST = timezone(timedelta(hours=9))


def _server(server_id: int = 1, hostname: str = "server01") -> ServerDTO:
    now = datetime.now(timezone.utc)
    return ServerDTO(id=server_id, hostname=hostname, created_at=now, updated_at=now)


def _metric(server_id: int = 1, disks: list | None = None) -> ServerMetricDTO:
    now = datetime.now(timezone.utc)
    return ServerMetricDTO(
        id=1,
        server_id=server_id,
        recorded_at=now,
        created_at=now,
        nproc=4,
        mem_total_mb=8192,
        disks=disks or [{"name": "vda", "size": "30G"}],
        ip_internal=["10.0.0.1"],
        ip_external=["1.2.3.4"],
    )


@pytest.fixture
def repo():
    r = AsyncMock()
    r.list_all = AsyncMock(return_value=[])
    r.get_by_id = AsyncMock(return_value=None)
    r.latest_metric = AsyncMock(return_value=None)
    r.metric_history = AsyncMock(return_value=[])
    return r


class TestFmt:
    def test_converts_utc_to_kst(self):
        dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert _fmt(dt) == "2024-01-01 09:00:00"

    def test_format_string(self):
        dt = datetime(2024, 6, 15, 12, 30, 45, tzinfo=timezone.utc)
        result = _fmt(dt)
        assert result == "2024-06-15 21:30:45"


class TestRealDisks:
    def test_filters_zero_byte_disks(self):
        disks = [{"name": "vda", "size": "30G"}, {"name": "loop0", "size": "0B"}]
        result = _real_disks(disks)
        assert len(result) == 1
        assert result[0].name == "vda"

    def test_returns_disk_info_objects(self):
        disks = [{"name": "vda", "size": "30G"}]
        result = _real_disks(disks)
        assert isinstance(result[0], DiskInfo)
        assert result[0].size == "30G"

    def test_empty_list_returns_empty(self):
        assert _real_disks([]) == []

    def test_all_zero_byte_returns_empty(self):
        disks = [{"name": "loop0", "size": "0B"}, {"name": "loop1", "size": "0B"}]
        assert _real_disks(disks) == []


class TestListServers:
    async def test_empty_repo_returns_empty(self, repo):
        result = await QueryService(repo).list_servers()
        assert result == []

    async def test_server_without_metric_excluded(self, repo):
        repo.list_all.return_value = [_server()]
        repo.latest_metric.return_value = None

        result = await QueryService(repo).list_servers()
        assert result == []

    async def test_server_with_metric_included(self, repo):
        repo.list_all.return_value = [_server(server_id=1)]
        repo.latest_metric.return_value = _metric(server_id=1)

        result = await QueryService(repo).list_servers()
        assert len(result) == 1
        assert result[0].hostname == "server01"

    async def test_multiple_servers_all_returned(self, repo):
        repo.list_all.return_value = [_server(server_id=1, hostname="a"), _server(server_id=2, hostname="b")]
        repo.latest_metric.side_effect = [_metric(server_id=1), _metric(server_id=2)]

        result = await QueryService(repo).list_servers()
        assert len(result) == 2


class TestGetServer:
    async def test_server_not_found_returns_none(self, repo):
        repo.get_by_id.return_value = None
        assert await QueryService(repo).get_server(1) is None

    async def test_server_without_metric_returns_none(self, repo):
        repo.get_by_id.return_value = _server()
        repo.latest_metric.return_value = None
        assert await QueryService(repo).get_server(1) is None

    async def test_server_with_metric_returns_item(self, repo):
        repo.get_by_id.return_value = _server(server_id=1, hostname="server01")
        repo.latest_metric.return_value = _metric(server_id=1)

        result = await QueryService(repo).get_server(1)
        assert result is not None
        assert result.hostname == "server01"
        assert result.nproc == 4

    async def test_zero_byte_disks_filtered(self, repo):
        repo.get_by_id.return_value = _server()
        repo.latest_metric.return_value = _metric(
            disks=[{"name": "vda", "size": "30G"}, {"name": "loop0", "size": "0B"}]
        )

        result = await QueryService(repo).get_server(1)
        assert len(result.disks) == 1
        assert result.disks[0].name == "vda"


class TestGetHistory:
    async def test_server_not_found_returns_none(self, repo):
        repo.get_by_id.return_value = None
        assert await QueryService(repo).get_history(1) is None

    async def test_returns_server_and_history(self, repo):
        repo.get_by_id.return_value = _server(server_id=1)
        repo.metric_history.return_value = [_metric(), _metric()]

        result = await QueryService(repo).get_history(1)
        assert result is not None
        server, history = result
        assert server.hostname == "server01"
        assert len(history) == 2

    async def test_empty_history_returns_empty_list(self, repo):
        repo.get_by_id.return_value = _server()
        repo.metric_history.return_value = []

        result = await QueryService(repo).get_history(1)
        assert result is not None
        _, history = result
        assert history == []