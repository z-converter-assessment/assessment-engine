from datetime import datetime, timezone, timedelta
from typing import Optional

from web.api.view_models import ServerItem, MetricHistoryItem
from db.repositories.base_query_repository import ServerDTO, ServerMetricDTO, BaseQueryRepository

_KST = timezone(timedelta(hours=9))


def _fmt(dt: datetime) -> str:
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M:%S")


def _to_server_item(s: ServerDTO, m: ServerMetricDTO) -> ServerItem:
    return ServerItem(
        id=s.id,
        hostname=s.hostname,
        cpu_cores=s.cpu_cores,
        mem_total_mb=s.mem_total_mb,
        updated_at=_fmt(s.updated_at),
        cpu_user_pct=m.cpu_user_pct,
        mem_used_mb=m.mem_used_mb,
        load_1m=m.load_1m,
    )


def _to_history_item(m: ServerMetricDTO) -> MetricHistoryItem:
    return MetricHistoryItem(
        collected_at=_fmt(m.collected_at),
        cpu_user_pct=m.cpu_user_pct,
        cpu_system_pct=m.cpu_system_pct,
        cpu_iowait_pct=m.cpu_iowait_pct,
        mem_used_mb=m.mem_used_mb,
        swap_used_mb=m.swap_used_mb,
        load_1m=m.load_1m,
        disk_usage=m.disk_usage,
    )


class QueryService:
    def __init__(self, repo: BaseQueryRepository):
        self.repo = repo

    async def list_servers(self) -> list[ServerItem]:
        servers = await self.repo.list_all()
        result = []
        for s in servers:
            m = await self.repo.latest_metric(s.id)
            if m:
                result.append(_to_server_item(s, m))
        return result

    async def get_server(self, server_id: int) -> Optional[ServerItem]:
        server = await self.repo.get_by_id(server_id)
        if not server:
            return None
        m = await self.repo.latest_metric(server_id)
        if not m:
            return None
        return _to_server_item(server, m)

    async def get_history(
        self, server_id: int
    ) -> Optional[tuple[ServerDTO, list[MetricHistoryItem]]]:
        server = await self.repo.get_by_id(server_id)
        if not server:
            return None
        history = await self.repo.metric_history(server_id)
        return server, [_to_history_item(m) for m in history]