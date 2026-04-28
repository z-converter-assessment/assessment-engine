from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.server_inventory import ServerInventory
from db.models.server_metrics import ServerMetrics
from db.repositories.base_query_repository import BaseQueryRepository
from db.repositories.outbound import (
    CollectionStatusResponse,
    DashboardRaw,
    DiskIoRaw,
    MetricPairRaw,
    MetricSeriesResponse,
    MountUsageRaw,
    NetIoRaw,
    NetworkWithIoResponse,
    ServerListItemResponse,
    ServerResponse,
    StorageWithUsageResponse,
)

_TIME_RANGE: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h":  timedelta(hours=1),
    "6h":  timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d":  timedelta(days=7),
}

# 모두 whitelisted — SQL에 직접 포맷해도 안전
_BUCKET: dict[str, str] = {
    "5m": "5 minutes",
    "1h": "1 hour",
    "1d": "1 day",
}

# chart helper에서 raw CTE window start를 Python에서 계산해 파라미터로 전달
# (asyncpg가 :start - interval '...' 표현의 타입을 추론하지 못함)
_BUCKET_TD: dict[str, timedelta] = {
    "5 minutes": timedelta(minutes=5),
    "1 hour":    timedelta(hours=1),
    "1 day":     timedelta(days=1),
}

_AGG: dict[str, str] = {
    "avg": "avg(v)",
    "max": "max(v)",
    "p95": "percentile_cont(0.95) WITHIN GROUP (ORDER BY v)",
}


class QueryRepository(BaseQueryRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    # -------------------------------------------------------------------------
    # inventory
    # -------------------------------------------------------------------------

    async def list_servers(
        self,
        page: int,
        limit: int,
        search: str | None,
        is_online: bool | None,
    ) -> list[ServerListItemResponse]:
        last_metric_sq = (
            select(
                ServerMetrics.server_id,
                func.max(ServerMetrics.collected_at).label("last_metric_at"),
            )
            .group_by(ServerMetrics.server_id)
            .subquery()
        )

        last_seen_expr = func.coalesce(
            last_metric_sq.c.last_metric_at,
            ServerInventory.last_seen_at,
        )

        stmt = (
            select(ServerInventory, last_seen_expr.label("last_seen_at"))
            .outerjoin(last_metric_sq, last_metric_sq.c.server_id == ServerInventory.id)
            .order_by(last_seen_expr.desc().nullslast())
        )
        if search:
            stmt = stmt.where(ServerInventory.hostname.ilike(f"%{search}%"))
        stmt = stmt.offset((page - 1) * limit).limit(limit)

        result = await self.session.execute(stmt)
        return [
            ServerListItemResponse(
                id=r.ServerInventory.id,
                machine_id=r.ServerInventory.machine_id,
                hostname=r.ServerInventory.hostname,
                os_id=r.ServerInventory.os_id,
                os_version=r.ServerInventory.os_version,
                cpu_cores=r.ServerInventory.cpu_cores,
                mem_total_kb=r.ServerInventory.mem_total_kb,
                last_seen_at=r.last_seen_at,
            )
            for r in result.all()
        ]

    async def get_server(self, server_id: int) -> ServerResponse | None:
        result = await self.session.execute(
            select(ServerInventory).where(ServerInventory.id == server_id)
        )
        r = result.scalar_one_or_none()
        if not r:
            return None
        return ServerResponse(
            id=r.id,
            machine_id=r.machine_id,
            hostname=r.hostname,
            agent_version=r.agent_version,
            os_id=r.os_id,
            os_version=r.os_version,
            os_codename=r.os_codename,
            kernel_version=r.kernel_version,
            cpu_cores=r.cpu_cores,
            cpu_model=r.cpu_model,
            mem_total_kb=r.mem_total_kb,
            swap_total_kb=r.swap_total_kb,
            boot_time=r.boot_time,
            ip_internal=r.ip_internal or [],
            ip_external=r.ip_external,
            disks=r.disks or [],
            mounts=r.mounts or [],
            last_seen_at=r.last_seen_at,
        )

    async def get_storage(self, server_id: int) -> StorageWithUsageResponse | None:
        inv_result = await self.session.execute(
            select(
                ServerInventory.id,
                ServerInventory.hostname,
                ServerInventory.disks,
                ServerInventory.mounts,
            ).where(ServerInventory.id == server_id)
        )
        r = inv_result.one_or_none()
        if not r:
            return None

        mu_result = await self.session.execute(
            text("""
                SELECT DISTINCT ON (mount)
                    mount, total_bytes, avail_bytes, free_bytes
                FROM server_mount_usage
                WHERE server_id = :sid
                ORDER BY mount, collected_at DESC
            """),
            {"sid": server_id},
        )
        mount_usage = [
            MountUsageRaw(
                mount=row.mount,
                total_bytes=row.total_bytes,
                avail_bytes=row.avail_bytes,
                free_bytes=row.free_bytes,
            )
            for row in mu_result.all()
        ]

        return StorageWithUsageResponse(
            server_id=r.id,
            hostname=r.hostname,
            disks=r.disks or [],
            inventory_mounts=r.mounts or [],
            mount_usage=mount_usage,
        )

    async def get_network(self, server_id: int) -> NetworkWithIoResponse | None:
        inv_result = await self.session.execute(
            select(
                ServerInventory.id,
                ServerInventory.hostname,
                ServerInventory.ip_internal,
                ServerInventory.ip_external,
            ).where(ServerInventory.id == server_id)
        )
        r = inv_result.one_or_none()
        if not r:
            return None

        n_result = await self.session.execute(
            text("""
                SELECT interface, collected_at,
                       rx_bytes, tx_bytes, rx_packets, tx_packets, rx_errors, tx_errors
                FROM (
                    SELECT *,
                        ROW_NUMBER() OVER (PARTITION BY interface ORDER BY collected_at DESC) AS rn
                    FROM server_net_io
                    WHERE server_id = :sid
                ) t WHERE rn <= 2
                ORDER BY interface, collected_at DESC
            """),
            {"sid": server_id},
        )
        net_io = [
            NetIoRaw(
                interface=row.interface,
                collected_at=row.collected_at,
                rx_bytes=row.rx_bytes,
                tx_bytes=row.tx_bytes,
                rx_packets=row.rx_packets,
                tx_packets=row.tx_packets,
                rx_errors=row.rx_errors,
                tx_errors=row.tx_errors,
            )
            for row in n_result.all()
        ]

        return NetworkWithIoResponse(
            server_id=r.id,
            hostname=r.hostname,
            ip_internal=r.ip_internal or [],
            ip_external=r.ip_external,
            net_io=net_io,
        )

    # -------------------------------------------------------------------------
    # collection status
    # -------------------------------------------------------------------------

    async def get_collection_status(self, server_id: int) -> list[CollectionStatusResponse]:
        inv_result = await self.session.execute(
            select(ServerInventory.last_seen_at).where(ServerInventory.id == server_id)
        )
        metric_result = await self.session.execute(
            select(func.max(ServerMetrics.collected_at)).where(
                ServerMetrics.server_id == server_id
            )
        )
        return [
            CollectionStatusResponse(
                last_metric_at=metric_result.scalar_one_or_none(),
                last_inventory_at=inv_result.scalar_one_or_none(),
            )
        ]

    # -------------------------------------------------------------------------
    # metrics dashboard (delta 계산용 2행 페어 반환)
    # -------------------------------------------------------------------------

    async def latest_dashboard(self, server_id: int) -> DashboardRaw | None:
        exists = await self.session.execute(
            select(ServerInventory.id).where(ServerInventory.id == server_id)
        )
        if not exists.scalar_one_or_none():
            return None

        # 최신 2행 server_metrics
        m_result = await self.session.execute(
            select(ServerMetrics)
            .where(ServerMetrics.server_id == server_id)
            .order_by(ServerMetrics.collected_at.desc())
            .limit(2)
        )
        metrics = [
            MetricPairRaw(
                collected_at=m.collected_at,
                cpu_user=m.cpu_user, cpu_nice=m.cpu_nice, cpu_system=m.cpu_system,
                cpu_idle=m.cpu_idle, cpu_iowait=m.cpu_iowait, cpu_irq=m.cpu_irq,
                cpu_softirq=m.cpu_softirq, cpu_steal=m.cpu_steal,
                mem_total_kb=m.mem_total_kb, mem_free_kb=m.mem_free_kb,
                mem_available_kb=m.mem_available_kb, mem_buffers_kb=m.mem_buffers_kb,
                mem_cached_kb=m.mem_cached_kb, swap_total_kb=m.swap_total_kb,
                swap_free_kb=m.swap_free_kb,
                load_1m=m.load_1m, load_5m=m.load_5m, load_15m=m.load_15m,
            )
            for m in m_result.scalars().all()
        ]

        # 디바이스당 최신 2행 disk_io
        d_result = await self.session.execute(
            text("""
                SELECT device, collected_at,
                       reads_completed, writes_completed, sectors_read, sectors_written
                FROM (
                    SELECT *,
                        ROW_NUMBER() OVER (PARTITION BY device ORDER BY collected_at DESC) AS rn
                    FROM server_disk_io
                    WHERE server_id = :sid
                ) t WHERE rn <= 2
                ORDER BY device, collected_at DESC
            """),
            {"sid": server_id},
        )
        disk_io = [
            DiskIoRaw(
                device=row.device,
                collected_at=row.collected_at,
                reads_completed=row.reads_completed,
                writes_completed=row.writes_completed,
                sectors_read=row.sectors_read,
                sectors_written=row.sectors_written,
            )
            for row in d_result.all()
        ]

        # 인터페이스당 최신 2행 net_io
        n_result = await self.session.execute(
            text("""
                SELECT interface, collected_at,
                       rx_bytes, tx_bytes, rx_packets, tx_packets, rx_errors, tx_errors
                FROM (
                    SELECT *,
                        ROW_NUMBER() OVER (PARTITION BY interface ORDER BY collected_at DESC) AS rn
                    FROM server_net_io
                    WHERE server_id = :sid
                ) t WHERE rn <= 2
                ORDER BY interface, collected_at DESC
            """),
            {"sid": server_id},
        )
        net_io = [
            NetIoRaw(
                interface=row.interface,
                collected_at=row.collected_at,
                rx_bytes=row.rx_bytes,
                tx_bytes=row.tx_bytes,
                rx_packets=row.rx_packets,
                tx_packets=row.tx_packets,
                rx_errors=row.rx_errors,
                tx_errors=row.tx_errors,
            )
            for row in n_result.all()
        ]

        # 마운트당 최신 1행 mount_usage
        mu_result = await self.session.execute(
            text("""
                SELECT DISTINCT ON (mount)
                    mount, total_bytes, avail_bytes, free_bytes
                FROM server_mount_usage
                WHERE server_id = :sid
                ORDER BY mount, collected_at DESC
            """),
            {"sid": server_id},
        )
        mounts = [
            MountUsageRaw(
                mount=row.mount,
                total_bytes=row.total_bytes,
                avail_bytes=row.avail_bytes,
                free_bytes=row.free_bytes,
            )
            for row in mu_result.all()
        ]

        return DashboardRaw(metrics=metrics, disk_io=disk_io, net_io=net_io, mounts=mounts)

    # -------------------------------------------------------------------------
    # metric series
    # -------------------------------------------------------------------------

    async def metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeriesResponse]:
        stmt = (
            select(ServerMetrics.collected_at)
            .where(ServerMetrics.server_id == server_id)
            .order_by(ServerMetrics.collected_at.desc())
            .limit(limit)
        )
        if cursor:
            stmt = stmt.where(ServerMetrics.collected_at < cursor)
        result = await self.session.execute(stmt)
        return [
            MetricSeriesResponse(collected_at=ts, value=None, dimension=None)
            for ts in result.scalars().all()
        ]

    async def metric_chart(
        self,
        server_id: int,
        metric_type: str,
        dimension: str | None,
        time_range: str,
        bucket: str,
        agg: str,
    ) -> list[MetricSeriesResponse]:
        if time_range not in _TIME_RANGE or bucket not in _BUCKET or agg not in _AGG:
            return []

        start = datetime.now(timezone.utc) - _TIME_RANGE[time_range]
        bi = _BUCKET[bucket]
        ae = _AGG[agg]

        if metric_type == "cpu.usage_percent":
            rows = await self._chart_cpu(server_id, start, bi, ae)
        elif metric_type in ("disk.read_iops", "disk.write_iops"):
            col = "reads_completed" if metric_type == "disk.read_iops" else "writes_completed"
            rows = await self._chart_disk_iops(server_id, start, bi, ae, dimension, col)
        elif metric_type in ("net.rx_bytes_per_sec", "net.tx_bytes_per_sec"):
            col = "rx_bytes" if metric_type == "net.rx_bytes_per_sec" else "tx_bytes"
            rows = await self._chart_net(server_id, start, bi, ae, dimension, col)
        elif metric_type == "fs.usage_percent":
            rows = await self._chart_fs(server_id, start, bi, ae, dimension)
        else:
            return []

        return [
            MetricSeriesResponse(collected_at=r.ts, value=r.value, dimension=r.dimension)
            for r in rows
        ]

    # -------------------------------------------------------------------------
    # chart helpers  (bi, ae = whitelisted — safe to format into SQL)
    # -------------------------------------------------------------------------

    async def _chart_cpu(self, server_id: int, start: datetime, bi: str, ae: str):
        sql = text(f"""
            WITH raw AS (
                SELECT collected_at,
                    cpu_user+cpu_nice+cpu_system+cpu_iowait+cpu_irq+cpu_softirq+cpu_steal   AS active_j,
                    cpu_user+cpu_nice+cpu_system+cpu_idle+cpu_iowait+cpu_irq+cpu_softirq+cpu_steal AS total_j
                FROM server_metrics
                WHERE server_id = :sid
                  AND collected_at >= :window_start
            ),
            deltas AS (
                SELECT collected_at,
                    GREATEST(0, active_j - LAG(active_j) OVER (ORDER BY collected_at)) AS d_active,
                    GREATEST(0, total_j  - LAG(total_j)  OVER (ORDER BY collected_at)) AS d_total
                FROM raw
            )
            SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                   {ae}                                        AS value,
                   NULL::text                                  AS dimension
            FROM (
                SELECT collected_at,
                       CASE WHEN d_total > 0 THEN d_active * 100.0 / d_total END AS v
                FROM deltas
                WHERE collected_at >= :start AND d_total > 0
            ) sub
            GROUP BY ts
            ORDER BY ts
        """)
        result = await self.session.execute(sql, {"sid": server_id, "start": start, "window_start": start - _BUCKET_TD[bi]})
        return result.all()

    async def _chart_disk_iops(
        self, server_id: int, start: datetime, bi: str, ae: str,
        dimension: str | None, col: str,
    ):
        sql = text(f"""
            WITH raw AS (
                SELECT collected_at, device, {col} AS cnt
                FROM server_disk_io
                WHERE server_id = :sid
                  AND collected_at >= :window_start
                  AND (:dim::text IS NULL OR device = :dim)
            ),
            deltas AS (
                SELECT collected_at, device,
                    GREATEST(0, cnt - LAG(cnt) OVER (PARTITION BY device ORDER BY collected_at)) AS d_val,
                    EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER (PARTITION BY device ORDER BY collected_at))) AS dt
                FROM raw
            )
            SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                   {ae}                                        AS value,
                   device                                      AS dimension
            FROM (
                SELECT collected_at, device,
                       CASE WHEN dt > 0 THEN d_val / dt END AS v
                FROM deltas
                WHERE collected_at >= :start AND dt > 0
            ) sub
            GROUP BY ts, device
            ORDER BY ts, device
        """)
        result = await self.session.execute(sql, {"sid": server_id, "start": start, "window_start": start - _BUCKET_TD[bi], "dim": dimension})
        return result.all()

    async def _chart_net(
        self, server_id: int, start: datetime, bi: str, ae: str,
        dimension: str | None, col: str,
    ):
        sql = text(f"""
            WITH raw AS (
                SELECT collected_at, interface, {col} AS cnt
                FROM server_net_io
                WHERE server_id = :sid
                  AND collected_at >= :window_start
                  AND (:dim::text IS NULL OR interface = :dim)
            ),
            deltas AS (
                SELECT collected_at, interface,
                    GREATEST(0, cnt - LAG(cnt) OVER (PARTITION BY interface ORDER BY collected_at)) AS d_val,
                    EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER (PARTITION BY interface ORDER BY collected_at))) AS dt
                FROM raw
            )
            SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                   {ae}                                        AS value,
                   interface                                   AS dimension
            FROM (
                SELECT collected_at, interface,
                       CASE WHEN dt > 0 THEN d_val / dt END AS v
                FROM deltas
                WHERE collected_at >= :start AND dt > 0
            ) sub
            GROUP BY ts, interface
            ORDER BY ts, interface
        """)
        result = await self.session.execute(sql, {"sid": server_id, "start": start, "window_start": start - _BUCKET_TD[bi], "dim": dimension})
        return result.all()

    async def _chart_fs(
        self, server_id: int, start: datetime, bi: str, ae: str, dimension: str | None,
    ):
        sql = text(f"""
            SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                   {ae}                                        AS value,
                   mount                                       AS dimension
            FROM (
                SELECT collected_at, mount,
                       CASE WHEN total_bytes > 0
                            THEN (total_bytes - avail_bytes)::float / total_bytes * 100
                       END AS v
                FROM server_mount_usage
                WHERE server_id = :sid
                  AND collected_at >= :start
                  AND (:dim::text IS NULL OR mount = :dim)
            ) sub
            GROUP BY ts, mount
            ORDER BY ts, mount
        """)
        result = await self.session.execute(sql, {"sid": server_id, "start": start, "dim": dimension})
        return result.all()