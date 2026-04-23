from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.server_inventory import ServerInventory
from db.repositories.base_collect_repository import BaseCollectRepository

if TYPE_CHECKING:
    from consumer.schemas import InventoryInput, MetricsInput


class CollectRepository(BaseCollectRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_server_id(self, hostname: str) -> int | None:
        result = await self.session.execute(
            select(ServerInventory.id).where(ServerInventory.hostname == hostname)
        )
        return result.scalar_one_or_none()

    async def upsert_server(self, data: InventoryInput) -> None:
        raise NotImplementedError("schema 확정 후 구현")

    async def insert_metric(self, server_id: int, data: MetricsInput) -> None:
        raise NotImplementedError("schema 확정 후 구현")