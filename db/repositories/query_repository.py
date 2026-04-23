from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from db.repositories.base_query_repository import ServerDTO, ServerMetricDTO, BaseQueryRepository
from db.models.server_inventory import ServerInventory
from db.models.server_metrics import ServerMetrics


def _to_server_dto(orm: ServerInventory) -> ServerDTO:
    return ServerDTO(
        id=orm.id,
        hostname=orm.hostname,
        cpu_cores=orm.cpu_cores,
        mem_total_mb=orm.mem_total_mb,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def _to_metric_dto(orm: ServerMetrics) -> ServerMetricDTO:
    return ServerMetricDTO(
        id=orm.id,
        server_id=orm.server_id,
        collected_at=orm.collected_at,
        created_at=orm.created_at,
        cpu_user_pct=orm.cpu_user_pct,
        cpu_system_pct=orm.cpu_system_pct,
        cpu_iowait_pct=orm.cpu_iowait_pct,
        mem_used_mb=orm.mem_used_mb,
        mem_available_mb=orm.mem_available_mb,
        swap_used_mb=orm.swap_used_mb,
        disk_read_iops=orm.disk_read_iops,
        disk_write_iops=orm.disk_write_iops,
        disk_read_bytes=orm.disk_read_bytes,
        disk_write_bytes=orm.disk_write_bytes,
        disk_usage=orm.disk_usage,
        net_rx_bytes=orm.net_rx_bytes,
        net_tx_bytes=orm.net_tx_bytes,
        load_1m=orm.load_1m,
    )


class QueryRepository(BaseQueryRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, server_id: int) -> Optional[ServerDTO]:
        result = await self.session.execute(
            select(ServerInventory).where(ServerInventory.id == server_id)
        )
        orm: ServerInventory | None = result.scalar_one_or_none()
        return _to_server_dto(orm) if orm else None

    async def list_all(self) -> List[ServerDTO]:
        result = await self.session.execute(
            select(ServerInventory).order_by(ServerInventory.hostname)
        )
        return [_to_server_dto(s) for s in result.scalars().all()]

    async def latest_metric(self, server_id: int) -> Optional[ServerMetricDTO]:
        result = await self.session.execute(
            select(ServerMetrics)
            .where(ServerMetrics.server_id == server_id)
            .order_by(desc(ServerMetrics.collected_at))
            .limit(1)
        )
        orm: ServerMetrics | None = result.scalar_one_or_none()
        return _to_metric_dto(orm) if orm else None

    async def metric_history(self, server_id: int) -> List[ServerMetricDTO]:
        result = await self.session.execute(
            select(ServerMetrics)
            .where(ServerMetrics.server_id == server_id)
            .order_by(desc(ServerMetrics.collected_at))
        )
        return [_to_metric_dto(m) for m in result.scalars().all()]