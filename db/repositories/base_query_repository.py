from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

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


class BaseQueryRepository(ABC):

    @abstractmethod
    async def list_servers(
        self,
        page: int,
        limit: int,
        search: str | None,
        is_online: bool | None,
    ) -> list[ServerListItemResponse]: ...

    @abstractmethod
    async def get_server(self, server_id: int) -> ServerResponse | None: ...

    @abstractmethod
    async def get_storage(self, server_id: int) -> StorageResponse | None: ...

    @abstractmethod
    async def get_network(self, server_id: int) -> NetworkResponse | None: ...

    @abstractmethod
    async def get_collection_status(self, server_id: int) -> list[CollectionStatusResponse]: ...

    @abstractmethod
    async def latest_metric(self, server_id: int) -> ServerMetricResponse | None: ...

    @abstractmethod
    async def metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeriesResponse]: ...

    @abstractmethod
    async def metric_chart(
        self,
        server_id: int,
        metric_type: str,
        dimension: str | None,
        time_range: str,
        bucket: str,
        agg: str,
    ) -> list[MetricSeriesResponse]: ...