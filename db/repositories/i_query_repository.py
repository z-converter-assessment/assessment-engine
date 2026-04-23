from abc import ABC, abstractmethod
from typing import Optional, List

from db.repositories.dto import ServerDTO, ServerMetricDTO


class IQueryRepository(ABC):

    @abstractmethod
    async def get_by_id(self, server_id: int) -> Optional[ServerDTO]: ...

    @abstractmethod
    async def list_all(self) -> List[ServerDTO]: ...

    @abstractmethod
    async def latest_metric(self, server_id: int) -> Optional[ServerMetricDTO]: ...

    @abstractmethod
    async def metric_history(self, server_id: int) -> List[ServerMetricDTO]: ...