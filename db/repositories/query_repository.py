from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from db.repositories.base_query_repository import BaseQueryRepository

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


class QueryRepository(BaseQueryRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_servers(
        self,
        page: int,
        limit: int,
        search: str | None,
        is_online: bool | None,
    ) -> list[ServerListItemResponse]:
        raise NotImplementedError

    async def get_server(self, server_id: int) -> ServerResponse | None:
        raise NotImplementedError

    async def get_storage(self, server_id: int) -> StorageResponse | None:
        raise NotImplementedError

    async def get_network(self, server_id: int) -> NetworkResponse | None:
        raise NotImplementedError

    async def get_collection_status(self, server_id: int) -> list[CollectionStatusResponse]:
        raise NotImplementedError

    async def latest_metric(self, server_id: int) -> ServerMetricResponse | None:
        raise NotImplementedError

    async def metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeriesResponse]:
        raise NotImplementedError

    async def metric_chart(
        self,
        server_id: int,
        metric_type: str,
        dimension: str | None,
        time_range: str,
        bucket: str,
        agg: str,
    ) -> list[MetricSeriesResponse]:
        raise NotImplementedError