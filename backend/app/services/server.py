from __future__ import annotations
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.api.schemas import DiskInfo, MetricHistoryItem, ServerDetail, ServerListItem
from app.domain.server import ServerMetricDomain
from app.repositories.interface import IServerRepository

_KST = timezone(timedelta(hours=9))


def _fmt(dt: datetime) -> str:
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M:%S")


class ServerService:
    def __init__(self, repo: IServerRepository):
        self.repo = repo

    def _real_disks(self, disks: Optional[list]) -> list[DiskInfo]:
        if not disks:
            return []
        return [DiskInfo(name=d["name"], size=d["size"]) for d in disks if d.get("size", "0B") != "0B"]

    def _format_metric(self, m: Optional[ServerMetricDomain]) -> tuple[Optional[int], Optional[int], list[DiskInfo], list[str], list[str]]:
        if not m:
            return None, None, [], [], []
        return (
            m.nproc,
            m.mem_total_mb,
            self._real_disks(m.disks),
            m.ip_internal or [],
            m.ip_external or [],
        )

    async def list_servers(self) -> list[ServerListItem]:
        servers = await self.repo.list_all()
        result = []
        for s in servers:
            m = await self.repo.latest_metric(s.id)
            nproc, mem, disks, ip_in, ip_ex = self._format_metric(m)
            result.append(ServerListItem(
                id=s.id,
                hostname=s.hostname,
                updated_at=_fmt(s.updated_at),
                nproc=nproc,
                mem_total_mb=mem,
                disks=disks,
                ip_internal=ip_in,
                ip_external=ip_ex,
            ))
        return result

    async def get_server(self, server_id: uuid.UUID) -> Optional[ServerDetail]:
        server = await self.repo.get_by_id(server_id)
        if not server:
            return None
        m = await self.repo.latest_metric(server_id)
        nproc, mem, disks, ip_in, ip_ex = self._format_metric(m)
        return ServerDetail(
            id=server.id,
            hostname=server.hostname,
            updated_at=_fmt(server.updated_at),
            nproc=nproc,
            mem_total_mb=mem,
            disks=disks,
            ip_internal=ip_in,
            ip_external=ip_ex,
        )

    async def get_history(self, server_id: uuid.UUID) -> tuple[Optional[ServerDetail], list[MetricHistoryItem]]:
        server = await self.repo.get_by_id(server_id)
        if not server:
            return None, []
        history = await self.repo.metric_history(server_id)
        items = [
            MetricHistoryItem(
                recorded_at=_fmt(m.recorded_at),
                nproc=m.nproc,
                mem_total_mb=m.mem_total_mb,
                disks=self._real_disks(m.disks),
                ip_internal=m.ip_internal or [],
                ip_external=m.ip_external or [],
            )
            for m in history
        ]
        return ServerDetail(
            id=server.id,
            hostname=server.hostname,
            updated_at=_fmt(server.updated_at),
            nproc=None,
            mem_total_mb=None,
        ), items