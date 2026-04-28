from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db.repositories.outbound import (
    CollectionStatusResponse,
    DashboardRaw,
    MetricPairRaw,
    MetricSeriesResponse,
    ServerListItemResponse,
    ServerResponse,
)
from web.services.query_service import QueryService

pytestmark = pytest.mark.unit

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _make_repo() -> MagicMock:
    repo = MagicMock()
    repo.list_servers = AsyncMock(return_value=[])
    repo.get_server = AsyncMock(return_value=None)
    repo.get_storage = AsyncMock(return_value=None)
    repo.get_network = AsyncMock(return_value=None)
    repo.get_collection_status = AsyncMock(return_value=[])
    repo.latest_dashboard = AsyncMock(return_value=None)
    repo.metric_snapshots = AsyncMock(return_value=[])
    repo.metric_chart = AsyncMock(return_value=[])
    return repo


def _make_redis(online: bool = False) -> MagicMock:
    redis = MagicMock()
    redis.exists = AsyncMock(return_value=int(online))
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    return redis


def _server_list_dto(sid: int = 1) -> ServerListItemResponse:
    return ServerListItemResponse(
        id=sid, machine_id="mid", hostname=f"host-{sid}",
        os_id="ubuntu", os_version="22.04", cpu_cores=4,
        mem_total_kb=8_000_000, last_seen_at=_NOW,
    )


def _server_dto() -> ServerResponse:
    return ServerResponse(
        id=1, machine_id="mid", hostname="host-01", agent_version="1.0",
        os_id=None, os_version=None, os_codename=None, kernel_version=None,
        cpu_cores=4, cpu_model=None, mem_total_kb=4_000_000, swap_total_kb=None,
        boot_time=None, ip_internal=[], ip_external=None, disks=[], mounts=[],
        last_seen_at=_NOW,
    )


def _metric_pair() -> MetricPairRaw:
    return MetricPairRaw(
        collected_at=_NOW,
        cpu_user=100, cpu_nice=0, cpu_system=50, cpu_idle=800,
        cpu_iowait=10, cpu_irq=5, cpu_softirq=5, cpu_steal=0,
        mem_total_kb=4_000_000, mem_free_kb=1_000_000,
        mem_available_kb=2_000_000, mem_buffers_kb=100_000,
        mem_cached_kb=500_000, swap_total_kb=0, swap_free_kb=0,
        load_1m=0.5, load_5m=0.4, load_15m=0.3,
    )


class TestListServers:
    async def test_empty_repo_returns_empty_list(self):
        svc = QueryService(_make_repo(), _make_redis())
        result = await svc.list_servers(1, 20, None, None)
        assert result == []

    async def test_passes_page_limit_search_to_repo(self):
        repo = _make_repo()
        repo.list_servers = AsyncMock(return_value=[])
        svc = QueryService(repo, _make_redis())
        await svc.list_servers(2, 10, "web", None)
        repo.list_servers.assert_called_once_with(2, 10, "web")

    async def test_online_status_resolved_from_redis(self):
        repo = _make_repo()
        repo.list_servers = AsyncMock(return_value=[_server_list_dto(1)])
        redis = _make_redis(online=True)
        svc = QueryService(repo, redis)
        result = await svc.list_servers(1, 20, None, None)
        assert result[0].is_online is True

    async def test_is_online_filter_applied_in_service(self):
        repo = _make_repo()
        repo.list_servers = AsyncMock(return_value=[_server_list_dto(1), _server_list_dto(2)])
        redis = MagicMock()
        # server 1 → online, server 2 → offline
        redis.exists = AsyncMock(side_effect=[1, 0])
        svc = QueryService(repo, redis)
        result = await svc.list_servers(1, 20, None, is_online=True)
        assert len(result) == 1
        assert result[0].id == 1


class TestGetServer:
    async def test_not_found_returns_none(self):
        svc = QueryService(_make_repo(), _make_redis())
        assert await svc.get_server(999) is None

    async def test_found_returns_server_detail_response(self):
        repo = _make_repo()
        repo.get_server = AsyncMock(return_value=_server_dto())
        svc = QueryService(repo, _make_redis())
        result = await svc.get_server(1)
        assert result is not None
        assert result.hostname == "host-01"

    async def test_cache_hit_skips_repo(self):
        import dataclasses, json
        from web.services.cache_serializer import server_detail_to_json
        from web.services.mappers import to_server_detail
        cached_detail = to_server_detail(_server_dto())
        cached_json = server_detail_to_json(cached_detail)

        repo = _make_repo()
        redis = _make_redis()
        redis.get = AsyncMock(return_value=cached_json)
        svc = QueryService(repo, redis)
        result = await svc.get_server(1)
        repo.get_server.assert_not_called()
        assert result is not None

    async def test_cache_miss_populates_cache(self):
        repo = _make_repo()
        repo.get_server = AsyncMock(return_value=_server_dto())
        redis = _make_redis()
        redis.get = AsyncMock(return_value=None)
        svc = QueryService(repo, redis)
        await svc.get_server(1)
        redis.set.assert_called_once()


class TestGetStorage:
    async def test_not_found_returns_none(self):
        svc = QueryService(_make_repo(), _make_redis())
        assert await svc.get_storage(999) is None

    async def test_found_returns_storage_detail(self):
        from db.repositories.outbound import StorageWithUsageResponse
        repo = _make_repo()
        repo.get_storage = AsyncMock(return_value=StorageWithUsageResponse(
            server_id=1, hostname="h", disks=[], inventory_mounts=[], mount_usage=[],
        ))
        svc = QueryService(repo, _make_redis())
        result = await svc.get_storage(1)
        assert result is not None
        assert result.server_id == 1


class TestGetNetwork:
    async def test_not_found_returns_none(self):
        svc = QueryService(_make_repo(), _make_redis())
        assert await svc.get_network(999) is None

    async def test_found_returns_network_detail(self):
        from db.repositories.outbound import NetworkWithIoResponse
        repo = _make_repo()
        repo.get_network = AsyncMock(return_value=NetworkWithIoResponse(
            server_id=1, hostname="h", ip_internal=[], ip_external=None, net_io=[],
        ))
        svc = QueryService(repo, _make_redis())
        result = await svc.get_network(1)
        assert result is not None


class TestGetCollectionStatus:
    async def test_passes_server_id_to_repo(self):
        repo = _make_repo()
        repo.get_collection_status = AsyncMock(
            return_value=[CollectionStatusResponse(last_metric_at=None, last_inventory_at=None)]
        )
        svc = QueryService(repo, _make_redis())
        await svc.get_collection_status(42)
        repo.get_collection_status.assert_called_once_with(42)

    async def test_online_flag_from_redis(self):
        repo = _make_repo()
        repo.get_collection_status = AsyncMock(
            return_value=[CollectionStatusResponse(last_metric_at=_NOW, last_inventory_at=_NOW)]
        )
        redis = _make_redis(online=True)
        svc = QueryService(repo, redis)
        result = await svc.get_collection_status(1)
        assert result[0].is_online is True


class TestGetLatestMetric:
    async def test_not_found_returns_none(self):
        svc = QueryService(_make_repo(), _make_redis())
        assert await svc.get_latest_metric(999) is None

    async def test_found_returns_dashboard(self):
        repo = _make_repo()
        repo.latest_dashboard = AsyncMock(return_value=DashboardRaw(
            metrics=[_metric_pair()], disk_io=[], net_io=[], mounts=[],
        ))
        svc = QueryService(repo, _make_redis())
        result = await svc.get_latest_metric(1)
        assert result is not None

    async def test_cache_hit_skips_repo(self):
        from web.services.cache_serializer import dashboard_to_json
        from web.services.metrics_calculator import build_dashboard
        dash = build_dashboard(DashboardRaw(metrics=[_metric_pair()], disk_io=[], net_io=[], mounts=[]))
        cached_json = dashboard_to_json(dash)

        repo = _make_repo()
        redis = _make_redis()
        redis.get = AsyncMock(return_value=cached_json)
        svc = QueryService(repo, redis)
        await svc.get_latest_metric(1)
        repo.latest_dashboard.assert_not_called()

    async def test_cache_miss_populates_cache(self):
        repo = _make_repo()
        repo.latest_dashboard = AsyncMock(return_value=DashboardRaw(
            metrics=[_metric_pair()], disk_io=[], net_io=[], mounts=[],
        ))
        redis = _make_redis()
        redis.get = AsyncMock(return_value=None)
        svc = QueryService(repo, redis)
        await svc.get_latest_metric(1)
        redis.set.assert_called_once()


class TestGetMetricSnapshots:
    async def test_empty_returns_empty_list(self):
        svc = QueryService(_make_repo(), _make_redis())
        result = await svc.get_metric_snapshots(1, None, 10)
        assert result == []

    async def test_passes_cursor_and_limit(self):
        repo = _make_repo()
        repo.metric_snapshots = AsyncMock(return_value=[])
        svc = QueryService(repo, _make_redis())
        await svc.get_metric_snapshots(1, _NOW, 5)
        repo.metric_snapshots.assert_called_once_with(1, _NOW, 5)


class TestGetMetricChart:
    async def test_invalid_metric_type_returns_empty_without_repo_call(self):
        repo = _make_repo()
        svc = QueryService(repo, _make_redis())
        result = await svc.get_metric_chart(1, "invalid.type", None, "1h", "5m", "avg")  # type: ignore
        assert result == []
        repo.metric_chart.assert_not_called()

    async def test_valid_params_delegated_to_repo(self):
        repo = _make_repo()
        repo.metric_chart = AsyncMock(return_value=[
            MetricSeriesResponse(collected_at=_NOW, value=50.0, dimension=None)
        ])
        svc = QueryService(repo, _make_redis())
        result = await svc.get_metric_chart(1, "cpu.usage_percent", None, "1h", "5m", "avg")
        assert len(result) == 1
        repo.metric_chart.assert_called_once()