from __future__ import annotations
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.dto.outbound import ServerDTO
from app.models.server_entity import ServerEntity
from app.models.metric_snapshot import MetricSnapshot
from app.repositories.i_write_repository import IWriteRepository


def _to_server_dto(orm: ServerEntity) -> ServerDTO:
    return ServerDTO(
        id=orm.id,
        hostname=orm.hostname,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


class WriteRepository(IWriteRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, hostname: str) -> ServerDTO:
        result = await self.session.execute(select(ServerEntity).where(ServerEntity.hostname == hostname))
        orm = result.scalar_one_or_none()
        if orm is None:
            orm = ServerEntity(hostname=hostname)
            self.session.add(orm)
        else:
            assert isinstance(orm, ServerEntity)
            await self.session.execute(
                update(ServerEntity).where(ServerEntity.id == orm.id).values(updated_at=func.now())
            )
        await self.session.flush()
        await self.session.refresh(orm)
        return _to_server_dto(orm)

    async def insert_metric(
        self,
        server_id: UUID,
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