from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.repositories.outbound import (
        CollectionStatusResponse,
        DashboardRaw,
        MetricSeriesResponse,
        NetworkWithIoResponse,
        ServerListItemResponse,
        ServerResponse,
        StorageWithUsageResponse,
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
    async def get_storage(self, server_id: int) -> StorageWithUsageResponse | None: ...

    @abstractmethod
    async def get_network(self, server_id: int) -> NetworkWithIoResponse | None: ...

    @abstractmethod
    async def get_collection_status(self, server_id: int) -> list[CollectionStatusResponse]: ...

    @abstractmethod
    async def latest_dashboard(self, server_id: int) -> DashboardRaw | None: ...

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