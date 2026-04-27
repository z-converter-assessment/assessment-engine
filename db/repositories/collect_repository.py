from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from db.repositories.base_collect_repository import BaseCollectRepository

if TYPE_CHECKING:
    from db.repositories.dto import ServerInventoryCreate, ServerMetricCreate


class CollectRepository(BaseCollectRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_server_id(self, machine_id: str) -> int | None:
        raise NotImplementedError

    async def upsert_server(self, data: ServerInventoryCreate) -> None:
        raise NotImplementedError

    async def insert_metric(self, server_id: int, data: ServerMetricCreate) -> None:
        raise NotImplementedError