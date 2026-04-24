from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List


@dataclass
class ServerDTO:
    id: int
    hostname: str
    cpu_cores: int
    mem_total_mb: int
    created_at: datetime
    updated_at: datetime


@dataclass
class ServerMetricDTO:
    id: int
    server_id: int
    collected_at: datetime
    created_at: datetime
    cpu_user_pct: float
    cpu_system_pct: float
    cpu_iowait_pct: float
    mem_used_mb: int
    mem_available_mb: int | None
    swap_used_mb: int
    disk_read_iops: int | None
    disk_write_iops: int | None
    disk_read_bytes: int | None
    disk_write_bytes: int | None
    disk_usage: list
    net_rx_bytes: int | None
    net_tx_bytes: int | None
    load_1m: float


class BaseQueryRepository(ABC):

    @abstractmethod
    async def get_by_id(self, server_id: int) -> Optional[ServerDTO]: ...

    @abstractmethod
    async def list_all(self) -> List[ServerDTO]: ...

    @abstractmethod
    async def latest_metric(self, server_id: int) -> Optional[ServerMetricDTO]: ...

    @abstractmethod
    async def metric_history(self, server_id: int) -> List[ServerMetricDTO]: ...