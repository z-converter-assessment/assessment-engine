from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from db.repositories.base_query_repository import BaseQueryRepository
from web.view_models import (
    ServerListItem,
    ServerDetailResponse,
    StorageDetailResponse,
    NetworkDetailResponse,
    CollectionStatusItem,
    MetricLatestResponse,
    MetricSeriesItem,
)

if TYPE_CHECKING:
    from db.repositories.dto import (
        ServerListItemResponse,
        ServerResponse,
        StorageResponse,
        NetworkResponse,
        CollectionStatusResponse,
        ServerMetricResponse,
        MetricSeriesResponse,
    )


def _to_server_list_item(dto: ServerListItemResponse) -> ServerListItem: ...
def _to_server_detail(dto: ServerResponse) -> ServerDetailResponse: ...
def _to_storage_detail(dto: StorageResponse) -> StorageDetailResponse: ...
def _to_network_detail(dto: NetworkResponse) -> NetworkDetailResponse: ...
def _to_collection_status_item(dto: CollectionStatusResponse) -> CollectionStatusItem: ...
def _to_metric_latest(dto: ServerMetricResponse) -> MetricLatestResponse: ...
def _to_metric_series_item(dto: MetricSeriesResponse) -> MetricSeriesItem: ...


class QueryService:
    def __init__(self, repo: BaseQueryRepository):
        self.repo = repo

    async def list_servers(
        self,
        page: int,
        limit: int,
        search: str | None,
        is_online: bool | None,
    ) -> list[ServerListItem]:
        dtos = await self.repo.list_servers(page, limit, search, is_online)
        return [_to_server_list_item(dto) for dto in dtos]

    async def get_server(self, server_id: int) -> ServerDetailResponse | None:
        dto = await self.repo.get_server(server_id)
        return _to_server_detail(dto) if dto else None

    async def get_storage(self, server_id: int) -> StorageDetailResponse | None:
        dto = await self.repo.get_storage(server_id)
        return _to_storage_detail(dto) if dto else None

    async def get_network(self, server_id: int) -> NetworkDetailResponse | None:
        dto = await self.repo.get_network(server_id)
        return _to_network_detail(dto) if dto else None

    async def get_collection_status(self, server_id: int) -> list[CollectionStatusItem]:
        dtos = await self.repo.get_collection_status(server_id)
        return [_to_collection_status_item(dto) for dto in dtos]

    async def get_latest_metric(self, server_id: int) -> MetricLatestResponse | None:
        dto = await self.repo.latest_metric(server_id)
        return _to_metric_latest(dto) if dto else None

    async def get_metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeriesItem]:
        dtos = await self.repo.metric_snapshots(server_id, cursor, limit)
        return [_to_metric_series_item(dto) for dto in dtos]

    async def get_metric_chart(
        self,
        server_id: int,
        metric_type: str,
        dimension: str | None,
        time_range: str,
        bucket: str,
        agg: str,
    ) -> list[MetricSeriesItem]:
        dtos = await self.repo.metric_chart(server_id, metric_type, dimension, time_range, bucket, agg)
        return [_to_metric_series_item(dto) for dto in dtos]