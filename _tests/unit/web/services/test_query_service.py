"""
QueryService 행동 테스트.
- 서비스가 레포 메서드를 올바른 인자로 호출하는지
- None / 빈 리스트 반환 처리가 올바른지
- 헬퍼 변환 후 결과를 위로 전달하는지
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from db.repositories.dto import (
    CollectionStatusResponse,
    MetricSeriesResponse,
    NetworkResponse,
    ServerListItemResponse,
    ServerMetricResponse,
    ServerResponse,
    StorageResponse,
)
from web.view_models import (
    CollectionStatusItem,
    MetricLatestResponse,
    MetricSeriesItem,
    NetworkDetailResponse,
    ServerDetailResponse,
    ServerListItem,
    StorageDetailResponse,
)
from web.services.query_service import QueryService


def _repo(**overrides) -> AsyncMock:
    r = AsyncMock()
    r.list_servers = AsyncMock(return_value=[])
    r.get_server = AsyncMock(return_value=None)
    r.get_storage = AsyncMock(return_value=None)
    r.get_network = AsyncMock(return_value=None)
    r.get_collection_status = AsyncMock(return_value=[])
    r.latest_metric = AsyncMock(return_value=None)
    r.metric_snapshots = AsyncMock(return_value=[])
    r.metric_chart = AsyncMock(return_value=[])
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


# ---------------------------------------------------------------------------
# list_servers
# ---------------------------------------------------------------------------

class TestListServers:
    async def test_empty_repo_returns_empty(self):
        result = await QueryService(_repo()).list_servers(1, 20, None, None)
        assert result == []

    async def test_passes_args_to_repo(self):
        repo = _repo()
        await QueryService(repo).list_servers(2, 50, "web", True)
        repo.list_servers.assert_called_once_with(2, 50, "web", True)

    async def test_maps_all_results(self):
        dtos = [ServerListItemResponse(), ServerListItemResponse()]
        repo = _repo(list_servers=AsyncMock(return_value=dtos))
        with patch("web.services.query_service._to_server_list_item", return_value=ServerListItem()):
            result = await QueryService(repo).list_servers(1, 20, None, None)
        assert len(result) == 2

    async def test_search_none_passed_through(self):
        repo = _repo()
        await QueryService(repo).list_servers(1, 20, None, None)
        repo.list_servers.assert_called_once_with(1, 20, None, None)


# ---------------------------------------------------------------------------
# get_server
# ---------------------------------------------------------------------------

class TestGetServer:
    async def test_not_found_returns_none(self):
        assert await QueryService(_repo()).get_server(1) is None

    async def test_passes_server_id(self):
        repo = _repo()
        await QueryService(repo).get_server(42)
        repo.get_server.assert_called_once_with(42)

    async def test_found_returns_view_model(self):
        repo = _repo(get_server=AsyncMock(return_value=ServerResponse()))
        with patch("web.services.query_service._to_server_detail", return_value=ServerDetailResponse()):
            result = await QueryService(repo).get_server(1)
        assert result is not None


# ---------------------------------------------------------------------------
# get_storage
# ---------------------------------------------------------------------------

class TestGetStorage:
    async def test_not_found_returns_none(self):
        assert await QueryService(_repo()).get_storage(1) is None

    async def test_passes_server_id(self):
        repo = _repo()
        await QueryService(repo).get_storage(7)
        repo.get_storage.assert_called_once_with(7)

    async def test_found_returns_view_model(self):
        repo = _repo(get_storage=AsyncMock(return_value=StorageResponse()))
        with patch("web.services.query_service._to_storage_detail", return_value=StorageDetailResponse()):
            result = await QueryService(repo).get_storage(1)
        assert result is not None


# ---------------------------------------------------------------------------
# get_network
# ---------------------------------------------------------------------------

class TestGetNetwork:
    async def test_not_found_returns_none(self):
        assert await QueryService(_repo()).get_network(1) is None

    async def test_passes_server_id(self):
        repo = _repo()
        await QueryService(repo).get_network(3)
        repo.get_network.assert_called_once_with(3)

    async def test_found_returns_view_model(self):
        repo = _repo(get_network=AsyncMock(return_value=NetworkResponse()))
        with patch("web.services.query_service._to_network_detail", return_value=NetworkDetailResponse()):
            result = await QueryService(repo).get_network(1)
        assert result is not None


# ---------------------------------------------------------------------------
# get_collection_status
# ---------------------------------------------------------------------------

class TestGetCollectionStatus:
    async def test_empty_returns_empty_list(self):
        result = await QueryService(_repo()).get_collection_status(1)
        assert result == []

    async def test_passes_server_id(self):
        repo = _repo()
        await QueryService(repo).get_collection_status(5)
        repo.get_collection_status.assert_called_once_with(5)

    async def test_maps_all_results(self):
        dtos = [CollectionStatusResponse(), CollectionStatusResponse()]
        repo = _repo(get_collection_status=AsyncMock(return_value=dtos))
        with patch("web.services.query_service._to_collection_status_item", return_value=CollectionStatusItem()):
            result = await QueryService(repo).get_collection_status(1)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# get_latest_metric
# ---------------------------------------------------------------------------

class TestGetLatestMetric:
    async def test_not_found_returns_none(self):
        assert await QueryService(_repo()).get_latest_metric(1) is None

    async def test_passes_server_id(self):
        repo = _repo()
        await QueryService(repo).get_latest_metric(9)
        repo.latest_metric.assert_called_once_with(9)

    async def test_found_returns_view_model(self):
        repo = _repo(latest_metric=AsyncMock(return_value=ServerMetricResponse()))
        with patch("web.services.query_service._to_metric_latest", return_value=MetricLatestResponse()):
            result = await QueryService(repo).get_latest_metric(1)
        assert result is not None


# ---------------------------------------------------------------------------
# get_metric_snapshots
# ---------------------------------------------------------------------------

class TestGetMetricSnapshots:
    async def test_empty_returns_empty_list(self):
        result = await QueryService(_repo()).get_metric_snapshots(1, None, 10)
        assert result == []

    async def test_passes_args_to_repo(self):
        repo = _repo()
        cursor = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await QueryService(repo).get_metric_snapshots(1, cursor, 5)
        repo.metric_snapshots.assert_called_once_with(1, cursor, 5)

    async def test_cursor_none_passed_through(self):
        repo = _repo()
        await QueryService(repo).get_metric_snapshots(1, None, 10)
        repo.metric_snapshots.assert_called_once_with(1, None, 10)

    async def test_maps_all_results(self):
        dtos = [MetricSeriesResponse(), MetricSeriesResponse(), MetricSeriesResponse()]
        repo = _repo(metric_snapshots=AsyncMock(return_value=dtos))
        with patch("web.services.query_service._to_metric_series_item", return_value=MetricSeriesItem()):
            result = await QueryService(repo).get_metric_snapshots(1, None, 10)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# get_metric_chart
# ---------------------------------------------------------------------------

class TestGetMetricChart:
    async def test_empty_returns_empty_list(self):
        result = await QueryService(_repo()).get_metric_chart(
            1, "cpu.usage_percent", None, "1h", "5m", "avg"
        )
        assert result == []

    async def test_passes_all_args_to_repo(self):
        repo = _repo()
        await QueryService(repo).get_metric_chart(1, "cpu.usage_percent", "cpu0", "6h", "1h", "max")
        repo.metric_chart.assert_called_once_with(1, "cpu.usage_percent", "cpu0", "6h", "1h", "max")

    async def test_dimension_none_passed_through(self):
        repo = _repo()
        await QueryService(repo).get_metric_chart(1, "net.rx_bytes_per_sec", None, "1h", "5m", "avg")
        repo.metric_chart.assert_called_once_with(1, "net.rx_bytes_per_sec", None, "1h", "5m", "avg")

    async def test_maps_all_results(self):
        dtos = [MetricSeriesResponse(), MetricSeriesResponse()]
        repo = _repo(metric_chart=AsyncMock(return_value=dtos))
        with patch("web.services.query_service._to_metric_series_item", return_value=MetricSeriesItem()):
            result = await QueryService(repo).get_metric_chart(
                1, "cpu.usage_percent", None, "1h", "5m", "avg"
            )
        assert len(result) == 2