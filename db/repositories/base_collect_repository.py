from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from consumer.schemas import InventoryInput, MetricsInput


class BaseCollectRepository(ABC):

    @abstractmethod
    async def find_server_id(self, hostname: str) -> int | None: ...

    @abstractmethod
    async def upsert_server(self, data: InventoryInput) -> None: ...

    @abstractmethod
    async def insert_metric(self, server_id: int, data: MetricsInput) -> None: ...