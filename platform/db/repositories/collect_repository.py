from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


from db.models.server_entity import ServerEntity
from db.models.metric_snapshot import MetricSnapshot
from db.repositories.i_collect_repository import ICollectRepository


class CollectRepository(ICollectRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_server(self, hostname: str) -> Optional[int]:
        result = await self.session.execute(
            select(ServerEntity).where(ServerEntity.hostname == hostname)
        )
        orm: ServerEntity | None = result.scalar_one_or_none()
        if orm is None:
            return None
        orm.updated_at = datetime.now(timezone.utc)
        return orm.id

    async def create_server(self, hostname: str) -> int:
        orm = ServerEntity(hostname=hostname)
        self.session.add(orm)
        await self.session.flush()
        return orm.id

    async def insert_metric(
        self,
        server_id: int,
        nproc: int,
        mem_total_mb: int,
        disks: list,
        ip_internal: list,
        ip_external: list,
    ) -> None:
        self.session.add(MetricSnapshot(
            server_id=server_id,
            nproc=nproc,
            mem_total_mb=mem_total_mb,
            disks=disks,
            ip_internal=ip_internal,
            ip_external=ip_external,
        ))