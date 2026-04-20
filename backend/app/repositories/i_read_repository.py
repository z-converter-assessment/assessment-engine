from abc import ABC, abstractmethod
from typing import Optional, List
from uuid import UUID

from app.dto.outbound import ServerDTO, ServerMetricDTO


class IReadRepository(ABC):

    @abstractmethod
    async def get_by_id(self, server_id: UUID) -> Optional[ServerDTO]: ...

    @abstractmethod
    async def list_all(self) -> List[ServerDTO]: ...

    @abstractmethod
    async def latest_metric(self, server_id: UUID) -> Optional[ServerMetricDTO]: ...

    @abstractmethod
    async def metric_history(self, server_id: UUID) -> List[ServerMetricDTO]: ...