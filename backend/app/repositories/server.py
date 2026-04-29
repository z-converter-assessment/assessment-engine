from __future__ import annotations
import uuid
from typing import Optional, List

from sqlalchemy import select, desc, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.domain.server import ServerDomain, ServerMetricDomain
from app.models.server import Server, ServerMetric
from app.repositories.interface import IServerRepository


def _to_server(orm: Server) -> ServerDomain:
    return ServerDomain(
        id=orm.id,
        hostname=orm.hostname,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def _to_metric(orm: ServerMetric) -> ServerMetricDomain:
    return ServerMetricDomain(
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


class ServerRepository(IServerRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, hostname: str) -> ServerDomain:
        result = await self.session.execute(select(Server).where(Server.hostname == hostname))
        orm = result.scalar_one_or_none()
        if orm is None:
            orm = Server(hostname=hostname)
            self.session.add(orm)
            await self.session.flush()
        else:
            await self.session.execute(
                update(Server).where(Server.id == orm.id).values(updated_at=func.now())
            )
        await self.session.refresh(orm)
        return _to_server(orm)

    async def insert_metric(
        self,
        server_id: uuid.UUID,
        nproc: Optional[int],
        mem_total_mb: Optional[int],
        disks: list,
        ip_internal: list,
        ip_external: list,
    ) -> ServerMetricDomain:
        orm = ServerMetric(
            server_id=server_id,
            nproc=nproc,
            mem_total_mb=mem_total_mb,
            disks=disks,
            ip_internal=ip_internal,
            ip_external=ip_external,
        )
        self.session.add(orm)
        await self.session.flush()
        await self.session.refresh(orm)
        return _to_metric(orm)

    async def get_by_id(self, server_id: uuid.UUID) -> Optional[ServerDomain]:
        result = await self.session.execute(select(Server).where(Server.id == server_id))
        orm = result.scalar_one_or_none()
        return _to_server(orm) if orm else None

    async def list_all(self) -> List[ServerDomain]:
        result = await self.session.execute(select(Server).order_by(Server.hostname))
        return [_to_server(s) for s in result.scalars().all()]

    async def latest_metric(self, server_id: uuid.UUID) -> Optional[ServerMetricDomain]:
        result = await self.session.execute(
            select(ServerMetric)
            .where(ServerMetric.server_id == server_id)
            .order_by(desc(ServerMetric.recorded_at))
            .limit(1)
        )
        orm = result.scalar_one_or_none()
        return _to_metric(orm) if orm else None

    async def metric_history(self, server_id: uuid.UUID) -> List[ServerMetricDomain]:
        result = await self.session.execute(
            select(ServerMetric)
            .where(ServerMetric.server_id == server_id)
            .order_by(desc(ServerMetric.recorded_at))
        )
        return [_to_metric(m) for m in result.scalars().all()]
