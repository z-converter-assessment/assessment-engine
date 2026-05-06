import json
from datetime import datetime
from typing import AsyncIterator, get_args

from redis.asyncio import Redis
from redis.exceptions import RedisError

from config import consumer_settings
from db.repositories.base_query_repository import (
    AggFunc,
    BaseQueryRepository,
    BucketSize,
    MetricType,
    TimeRange,
)

from web.services.cache_serializer import (
    dashboard_from_json,
    dashboard_to_json,
    server_detail_from_json,
    server_detail_to_json,
)
from web.services.mappers import (
    to_collection_status_item,
    to_metric_series_item,
    to_network_detail,
    to_server_detail,
    to_server_list_item,
    to_storage_detail,
)
from web.services.metrics_calculator import build_dashboard
from web.view_models import (
    CollectionStatusItem,
    MetricDashboard,
    MetricSeriesItem,
    NetworkDetailResponse,
    ServerDetailResponse,
    ServerListItem,
    StorageDetailResponse,
)

_VALID_METRIC_TYPES = frozenset(get_args(MetricType))
_VALID_TIME_RANGES  = frozenset(get_args(TimeRange))
_VALID_BUCKETS      = frozenset(get_args(BucketSize))
_VALID_AGGS         = frozenset(get_args(AggFunc))


class QueryService:
    def __init__(self, repo: BaseQueryRepository, redis: Redis):
        self.repo = repo
        self.redis = redis

    async def resolve_server_id(self, public_id: str) -> int | None:
        return await self.repo.resolve_server_id(public_id)

    async def _is_online(self, server_id: int) -> bool:
        return bool(await self.redis.exists(consumer_settings.redis_key_online.format(server_id)))

    async def list_servers(
        self,
        page: int,
        limit: int,
        search: str | None,
        is_online: bool | None,
    ) -> list[ServerListItem]:
        dtos = await self.repo.list_servers(page, limit, search)
        items: list[ServerListItem] = []
        for dto in dtos:
            item = to_server_list_item(dto)
            item.is_online = await self._is_online(dto.id)
            items.append(item)
        if is_online is not None:
            items = [i for i in items if i.is_online == is_online]
        return items

    async def get_server(self, server_id: int) -> ServerDetailResponse | None:
        cache_key = consumer_settings.redis_key_cache_inventory.format(server_id)
        cached = await self.redis.get(cache_key)
        if cached:
            return server_detail_from_json(cached)

        dto = await self.repo.get_server(server_id)
        if not dto:
            return None
        result = to_server_detail(dto)
        await self.redis.set(cache_key, server_detail_to_json(result), ex=300)
        return result

    async def get_storage(self, server_id: int) -> StorageDetailResponse | None:
        dto = await self.repo.get_storage(server_id)
        return to_storage_detail(dto) if dto else None

    async def get_network(self, server_id: int) -> NetworkDetailResponse | None:
        dto = await self.repo.get_network(server_id)
        return to_network_detail(dto) if dto else None

    async def get_collection_status(self, server_id: int) -> CollectionStatusItem | None:
        dto = await self.repo.get_collection_status(server_id)
        if dto is None:
            return None
        online = await self._is_online(server_id)
        return to_collection_status_item(dto, online)

    async def get_latest_metric(self, server_id: int) -> MetricDashboard | None:
        cache_key = consumer_settings.redis_key_cache_metrics.format(server_id)
        cached = await self.redis.get(cache_key)
        if cached:
            return dashboard_from_json(cached)

        raw = await self.repo.latest_dashboard(server_id)
        if not raw or not raw.metrics:
            return None
        result = build_dashboard(raw)
        await self.redis.set(cache_key, dashboard_to_json(result), ex=60)
        return result

    async def get_metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeriesItem]:
        dtos = await self.repo.metric_snapshots(server_id, cursor, limit)
        return [to_metric_series_item(dto) for dto in dtos]

    async def get_metric_chart(
        self,
        server_id: int,
        metric_type: MetricType,
        dimension: str | None,
        time_range: TimeRange,
        bucket: BucketSize,
        agg: AggFunc,
    ) -> list[MetricSeriesItem]:
        if (metric_type not in _VALID_METRIC_TYPES
                or time_range not in _VALID_TIME_RANGES
                or bucket not in _VALID_BUCKETS
                or agg not in _VALID_AGGS):
            return []

        dtos = await self.repo.metric_chart(server_id, metric_type, dimension, time_range, bucket, agg)
        return [to_metric_series_item(dto) for dto in dtos]

    async def stream_metrics_events(self, server_id: int) -> AsyncIterator[str]:
        async with self.redis.pubsub() as pubsub:
            await pubsub.subscribe(consumer_settings.redis_channel_metrics)
            try:
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                    except (ValueError, TypeError):
                        continue
                    if payload.get("server_id") == server_id:
                        yield message["data"]
            except RedisError:
                pass