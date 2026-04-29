from abc import ABC, abstractmethod

from db.repositories.inbound import ServerInventoryCreate, ServerMetricCreate


class BaseCollectRepository(ABC):

    @abstractmethod
    async def find_server_id(self, machine_id: str) -> int | None: ...

    @abstractmethod
    async def upsert_server(self, data: ServerInventoryCreate) -> int: ...

    @abstractmethod
    async def insert_metric(self, server_id: int, data: ServerMetricCreate) -> None: ...
