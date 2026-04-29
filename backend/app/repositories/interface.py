from __future__ import annotations
import uuid
from abc import ABC, abstractmethod
from typing import Optional, List

from app.domain.server import ServerDomain, ServerMetricDomain


class IServerRepository(ABC):

    @abstractmethod
    async def get_or_create(self, hostname: str) -> ServerDomain: ...

    @abstractmethod
    async def insert_metric(
        self,
        server_id: uuid.UUID,
        nproc: Optional[int],
        mem_total_mb: Optional[int],
        disks: list,
        ip_internal: list,
        ip_external: list,
    ) -> ServerMetricDomain: ...

    @abstractmethod
    async def get_by_id(self, server_id: uuid.UUID) -> Optional[ServerDomain]: ...

    @abstractmethod
    async def list_all(self) -> List[ServerDomain]: ...

    @abstractmethod
    async def latest_metric(self, server_id: uuid.UUID) -> Optional[ServerMetricDomain]: ...

    @abstractmethod
    async def metric_history(self, server_id: uuid.UUID) -> List[ServerMetricDomain]: ...