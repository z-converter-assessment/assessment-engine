from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal

from db.repositories.outbound import (
    CollectionStatusResponse,
    DashboardRaw,
    MetricSeriesResponse,
    NetworkWithIoResponse,
    ServerListItemResponse,
    ServerResponse,
    StorageWithUsageResponse,
)

MetricType = Literal[
    "cpu.usage_percent",
    "disk.read_iops",
    "disk.write_iops",
    "fs.usage_percent",
    "net.rx_bytes_per_sec",
    "net.tx_bytes_per_sec",
]
TimeRange  = Literal["15m", "1h", "6h", "24h", "7d"]
BucketSize = Literal["5m", "1h", "1d"]
AggFunc    = Literal["avg", "max", "p95"]


class BaseQueryRepository(ABC):

    @abstractmethod
    async def list_servers(
        self,
        page: int,
        limit: int,
        search: str | None,
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
        metric_type: MetricType,
        dimension: str | None,
        time_range: TimeRange,
        bucket: BucketSize,
        agg: AggFunc,
    ) -> list[MetricSeriesResponse]: ...