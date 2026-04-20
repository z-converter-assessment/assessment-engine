from datetime import datetime, timezone, timedelta
from uuid import UUID
from typing import Optional

from app.api.views import DiskInfo, MetricHistoryItem, ServerItem
from app.dto.outbound import ServerDTO, ServerMetricDTO
from app.repositories.i_read_repository import IReadRepository

_KST = timezone(timedelta(hours=9))


def _fmt(dt: datetime) -> str:
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M:%S")


def _real_disks(disks: list) -> list[DiskInfo]:
    return [DiskInfo(name=d["name"], size=d["size"]) for d in disks if d.get("size", "0B") != "0B"]


def _to_server_item(server: ServerDTO, m: ServerMetricDTO) -> ServerItem:
    return ServerItem(
        id=server.id,
        hostname=server.hostname,
        updated_at=_fmt(server.updated_at),
        nproc=m.nproc,
        mem_total_mb=m.mem_total_mb,
        disks=_real_disks(m.disks),
        ip_internal=m.ip_internal,
        ip_external=m.ip_external,
    )


class QueryService:
    def __init__(self, repo: IReadRepository):
        self.repo = repo

    async def list_servers(self) -> list[ServerItem]:
        servers = await self.repo.list_all()
        result = []
        for s in servers:
            m = await self.repo.latest_metric(s.id)
            if m:
                result.append(_to_server_item(s, m))
        return result

    async def get_server(self, server_id: UUID) -> Optional[ServerItem]:
        server = await self.repo.get_by_id(server_id)
        if not server:
            return None
        m = await self.repo.latest_metric(server_id)
        if not m:
            return None
        return _to_server_item(server, m)

    async def get_history(self, server_id: UUID) -> tuple[Optional[ServerDTO], list[MetricHistoryItem]]:
        server = await self.repo.get_by_id(server_id)
        if not server:
            return None, []
        history = await self.repo.metric_history(server_id)
        items = [
            MetricHistoryItem(
                recorded_at=_fmt(m.recorded_at),
                nproc=m.nproc,
                mem_total_mb=m.mem_total_mb,
                disks=_real_disks(m.disks),
                ip_internal=m.ip_internal,
                ip_external=m.ip_external,
            )
            for m in history
        ]
        return server, items