from __future__ import annotations
from typing import List, Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from db.repositories.dto import ServerDTO, ServerMetricDTO
from db.models.server_entity import ServerEntity
from db.models.metric_snapshot import MetricSnapshot
from db.repositories.i_query_repository import IQueryRepository


def _to_server_dto(orm: ServerEntity) -> ServerDTO:
    return ServerDTO(
        id=orm.id,
        hostname=orm.hostname,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def _to_metric_dto(orm: MetricSnapshot) -> ServerMetricDTO:
    return ServerMetricDTO(
        id=orm.id,
        server_id=orm.server_id,
        recorded_at=orm.recorded_at,
        created_at=orm.created_at,
        nproc=orm.nproc,
        mem_total_mb=orm.mem_total_mb,
        disks=orm.disks,
        ip_internal=orm.ip_internal,
        ip_external=orm.ip_external,
    )


class QueryRepository(IQueryRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, server_id: int) -> Optional[ServerDTO]:
        result = await self.session.execute(select(ServerEntity).where(ServerEntity.id == server_id))
        orm: ServerEntity | None = result.scalar_one_or_none()
        return _to_server_dto(orm) if orm else None

    async def list_all(self) -> List[ServerDTO]:
        result = await self.session.execute(select(ServerEntity).order_by(ServerEntity.hostname))
        return [_to_server_dto(s) for s in result.scalars().all()]

    async def latest_metric(self, server_id: int) -> Optional[ServerMetricDTO]:
        result = await self.session.execute(
            select(MetricSnapshot)
            .where(MetricSnapshot.server_id == server_id)
            .order_by(desc(MetricSnapshot.recorded_at))
            .limit(1)
        )
        orm: MetricSnapshot | None = result.scalar_one_or_none()
        return _to_metric_dto(orm) if orm else None

    async def metric_history(self, server_id: int) -> List[ServerMetricDTO]:
        result = await self.session.execute(
            select(MetricSnapshot)
            .where(MetricSnapshot.server_id == server_id)
            .order_by(desc(MetricSnapshot.recorded_at))
        )
        return [_to_metric_dto(m) for m in result.scalars().all()]