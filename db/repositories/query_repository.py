from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.server_disk_io import ServerDiskIo
from db.models.server_inventory import ServerInventory
from db.models.server_metrics import ServerMetrics
from db.models.server_mount_usage import ServerMountUsage
from db.models.server_net_io import ServerNetIo
from db.repositories.base_query_repository import (
    AggFunc,
    BaseQueryRepository,
    BucketSize,
    MetricType,
    TimeRange,
)
from db.repositories.outbound import (
    CollectionStatus,
    DashboardRaw,
    DiskIoRaw,
    MetricPairRaw,
    MetricSeries,
    MountUsageRaw,
    NetIoRaw,
    NetworkWithIo,
    ServerSummary,
    ServerDetail,
    StorageWithUsage,
)

_TIME_RANGE: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h":  timedelta(hours=1),
    "6h":  timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d":  timedelta(days=7),
    "30d": timedelta(days=30),
}

# 모두 whitelisted — SQL에 직접 포맷해도 안전
_BUCKET: dict[str, str] = {
    "1m":  "1 minute",
    "5m":  "5 minutes",
    "15m": "15 minutes",
    "30m": "30 minutes",
    "1h":  "1 hour",
    "3h":  "3 hours",
    "12h": "12 hours",
    "1d":  "1 day",
}

# chart helper에서 raw CTE window start를 Python에서 계산해 파라미터로 전달
# (asyncpg가 :start - interval '...' 표현의 타입을 추론하지 못함)
_BUCKET_TD: dict[str, timedelta] = {
    "1 minute":   timedelta(minutes=1),
    "5 minutes":  timedelta(minutes=5),
    "15 minutes": timedelta(minutes=15),
    "30 minutes": timedelta(minutes=30),
    "1 hour":     timedelta(hours=1),
    "3 hours":    timedelta(hours=3),
    "12 hours":   timedelta(hours=12),
    "1 day":      timedelta(days=1),
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

    async def resolve_server_id(self, public_id: str) -> int | None:
        result = await self.session.execute(
            select(ServerInventory.id).where(ServerInventory.public_id == public_id)
        )
        return result.scalar_one_or_none()

    async def list_servers(
        self,
        page: int,
        limit: int,
        search: str | None,
    ) -> list[ServerSummary]:
        stmt = (
            select(
                ServerInventory.id,
                ServerInventory.public_id,
                ServerInventory.machine_id,
                ServerInventory.hostname,
                ServerInventory.os_id,
                ServerInventory.os_version,
                ServerInventory.cpu_cores,
                ServerInventory.mem_total_kb,
                ServerInventory.ip_external,
                ServerInventory.disks,
                ServerInventory.services,
            )
            .order_by(ServerInventory.hostname.asc())
        )
        if search:
            stmt = stmt.where(ServerInventory.hostname.ilike(f"%{search}%"))
        stmt = stmt.offset((page - 1) * limit).limit(limit)

        result = await self.session.execute(stmt)
        return [
            ServerSummary(
                id=r.id,
                public_id=r.public_id,
                machine_id=r.machine_id,
                hostname=r.hostname,
                os_id=r.os_id,
                os_version=r.os_version,
                cpu_cores=r.cpu_cores,
                mem_total_kb=r.mem_total_kb,
                ip_external=r.ip_external,
                disks=r.disks or [],
                services=r.services,
            )
            for r in result.all()
        ]

    async def get_server(self, server_id: int) -> ServerDetail | None:
        result = await self.session.execute(
            select(ServerInventory).where(ServerInventory.id == server_id)
        )
        r = result.scalars().one_or_none()
        if r is None:
            return None
        return ServerDetail(
            id=r.id,
            public_id=r.public_id,
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
            services=r.services,
            listen_ports=r.listen_ports or [],
            last_seen_at=r.last_seen_at,
        )

    async def get_storage(self, server_id: int) -> StorageWithUsage | None:
        inv_result = await self.session.execute(
            select(
                ServerInventory.id,
                ServerInventory.public_id,
                ServerInventory.hostname,
                ServerInventory.disks,
                ServerInventory.mounts,
                ServerInventory.last_seen_at,
            ).where(ServerInventory.id == server_id)
        )
        r = inv_result.one_or_none()
        if not r:
            return None

        mu_result = await self.session.execute(
            text(f"""
                SELECT DISTINCT ON (mount)
                    mount, total_bytes, avail_bytes, free_bytes, collected_at
                FROM {ServerMountUsage.__tablename__}
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
                collected_at=row.collected_at,
            )
            for row in mu_result.all()
        ]

        return StorageWithUsage(
            server_id=r.id,
            public_id=r.public_id,
            hostname=r.hostname,
            disks=r.disks or [],
            inventory_mounts=r.mounts or [],
            mount_usage=mount_usage,
            inventory_at=r.last_seen_at,
        )

    async def get_network(self, server_id: int) -> NetworkWithIo | None:
        inv_result = await self.session.execute(
            select(
                ServerInventory.id,
                ServerInventory.public_id,
                ServerInventory.hostname,
                ServerInventory.ip_internal,
                ServerInventory.ip_external,
                ServerInventory.last_seen_at,
            ).where(ServerInventory.id == server_id)
        )
        r = inv_result.one_or_none()
        if not r:
            return None

        n_result = await self.session.execute(
            text(f"""
                SELECT interface, collected_at,
                       rx_bytes, tx_bytes, rx_packets, tx_packets, rx_errors, tx_errors
                FROM (
                    SELECT *,
                        ROW_NUMBER() OVER (PARTITION BY interface ORDER BY collected_at DESC) AS rn
                    FROM {ServerNetIo.__tablename__}
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

        return NetworkWithIo(
            server_id=r.id,
            public_id=r.public_id,
            hostname=r.hostname,
            ip_internal=r.ip_internal or [],
            ip_external=r.ip_external,
            net_io=net_io,
            inventory_at=r.last_seen_at,
        )

    # -------------------------------------------------------------------------
    # collection status
    # -------------------------------------------------------------------------

    async def get_collection_status(self, server_id: int) -> CollectionStatus | None:
        inv_result = await self.session.execute(
            select(ServerInventory.last_seen_at).where(ServerInventory.id == server_id)
        )
        row = inv_result.scalar_one_or_none()
        if row is None:
            return None
        metric_result = await self.session.execute(
            select(func.max(ServerMetrics.collected_at)).where(
                ServerMetrics.server_id == server_id
            )
        )
        return CollectionStatus(
            last_metric_at=metric_result.scalar_one_or_none(),
            last_inventory_at=row,
        )

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
            text(f"""
                SELECT device, collected_at,
                       reads_completed, writes_completed, sectors_read, sectors_written
                FROM (
                    SELECT *,
                        ROW_NUMBER() OVER (PARTITION BY device ORDER BY collected_at DESC) AS rn
                    FROM {ServerDiskIo.__tablename__}
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
            text(f"""
                SELECT interface, collected_at,
                       rx_bytes, tx_bytes, rx_packets, tx_packets, rx_errors, tx_errors
                FROM (
                    SELECT *,
                        ROW_NUMBER() OVER (PARTITION BY interface ORDER BY collected_at DESC) AS rn
                    FROM {ServerNetIo.__tablename__}
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
            text(f"""
                SELECT DISTINCT ON (mount)
                    mount, total_bytes, avail_bytes, free_bytes, collected_at
                FROM {ServerMountUsage.__tablename__}
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
                collected_at=row.collected_at,
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
    ) -> list[MetricSeries]:
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
            MetricSeries(collected_at=ts, value=None, dimension=None)
            for ts in result.scalars().all()
        ]

    async def metric_chart(
        self,
        server_id: int,
        metric_type: MetricType,
        dimension: str | None,
        time_range: TimeRange,
        bucket: BucketSize,
        agg: AggFunc,
        end: datetime | None = None,
    ) -> list[MetricSeries]:
        end_dt = end or datetime.now(timezone.utc)
        start  = end_dt - _TIME_RANGE[time_range]
        bi = _BUCKET[bucket]
        ae = _AGG[agg]

        if metric_type == "cpu.usage_percent":
            rows = await self._chart_cpu(server_id, start, end_dt, bi, ae)
        elif metric_type in ("cpu.user_percent", "cpu.system_percent", "cpu.iowait_percent"):
            col_map = {
                "cpu.user_percent":   "cpu_user",
                "cpu.system_percent": "cpu_system",
                "cpu.iowait_percent": "cpu_iowait",
            }
            rows = await self._chart_cpu_component(server_id, start, end_dt, bi, ae, col_map[metric_type])
        elif metric_type in ("load.1m", "load.5m", "load.15m"):
            col_map = {"load.1m": "load_1m", "load.5m": "load_5m", "load.15m": "load_15m"}
            rows = await self._chart_load(server_id, start, end_dt, bi, ae, col_map[metric_type])
        elif metric_type in ("mem.usage_percent", "mem.available_percent", "mem.cached_percent", "mem.buffers_percent", "swap.usage_percent"):
            rows = await self._chart_mem(server_id, start, end_dt, bi, ae, metric_type)
        elif metric_type in ("disk.read_iops", "disk.write_iops"):
            col = "reads_completed" if metric_type == "disk.read_iops" else "writes_completed"
            rows = await self._chart_disk_iops(server_id, start, end_dt, bi, ae, dimension, col)
        elif metric_type in ("net.rx_bytes_per_sec", "net.tx_bytes_per_sec"):
            col = "rx_bytes" if metric_type == "net.rx_bytes_per_sec" else "tx_bytes"
            rows = await self._chart_net(server_id, start, end_dt, bi, ae, dimension, col)
        elif metric_type == "fs.usage_percent":
            rows = await self._chart_fs(server_id, start, end_dt, bi, ae, dimension)
        else:
            return []

        return [
            MetricSeries(collected_at=row.ts, value=row.value, dimension=row.dimension)
            for row in rows
        ]

    # -------------------------------------------------------------------------
    # chart helpers  (bi, ae = whitelisted — safe to format into SQL)
    # -------------------------------------------------------------------------

    async def _chart_cpu(self, server_id: int, start: datetime, end: datetime, bi: str, ae: str):
        sql = text(f"""
            WITH raw AS (
                SELECT collected_at,
                    cpu_user+cpu_nice+cpu_system+cpu_iowait+cpu_irq+cpu_softirq+cpu_steal   AS active_j,
                    cpu_user+cpu_nice+cpu_system+cpu_idle+cpu_iowait+cpu_irq+cpu_softirq+cpu_steal AS total_j
                FROM {ServerMetrics.__tablename__}
                WHERE server_id = :sid
                  AND collected_at >= :window_start
                  AND collected_at <= :end
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
        result = await self.session.execute(sql, {"sid": server_id, "start": start, "end": end, "window_start": start - _BUCKET_TD[bi]})
        return result.all()

    async def _chart_disk_iops(
        self, server_id: int, start: datetime, end: datetime, bi: str, ae: str,
        dimension: str | None, col: str,
    ):
        sql = text(f"""
            WITH raw AS (
                SELECT collected_at, device, {col} AS cnt
                FROM {ServerDiskIo.__tablename__}
                WHERE server_id = :sid
                  AND collected_at >= :window_start
                  AND collected_at <= :end
                  AND (CAST(:dim AS text) IS NULL OR device = :dim)
            ),
            deltas AS (
                SELECT collected_at, device,
                    cnt - LAG(cnt) OVER (PARTITION BY device ORDER BY collected_at) AS d_val,
                    EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER (PARTITION BY device ORDER BY collected_at))) AS dt
                FROM raw
            )
            SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                   {ae}                                        AS value,
                   device                                      AS dimension
            FROM (
                SELECT collected_at, device,
                       CASE WHEN dt > 0 AND d_val >= 0 THEN d_val / dt END AS v
                FROM deltas
                WHERE collected_at >= :start AND dt > 0 AND d_val >= 0
            ) sub
            GROUP BY ts, device
            ORDER BY ts, device
        """)
        result = await self.session.execute(sql, {"sid": server_id, "start": start, "end": end, "window_start": start - _BUCKET_TD[bi], "dim": dimension})
        return result.all()

    async def _chart_net(
        self, server_id: int, start: datetime, end: datetime, bi: str, ae: str,
        dimension: str | None, col: str,
    ):
        sql = text(f"""
            WITH raw AS (
                SELECT collected_at, interface, {col} AS cnt
                FROM {ServerNetIo.__tablename__}
                WHERE server_id = :sid
                  AND collected_at >= :window_start
                  AND collected_at <= :end
                  AND (CAST(:dim AS text) IS NULL OR interface = :dim)
            ),
            deltas AS (
                SELECT collected_at, interface,
                    cnt - LAG(cnt) OVER (PARTITION BY interface ORDER BY collected_at) AS d_val,
                    EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER (PARTITION BY interface ORDER BY collected_at))) AS dt
                FROM raw
            )
            SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                   {ae}                                        AS value,
                   interface                                   AS dimension
            FROM (
                SELECT collected_at, interface,
                       CASE WHEN dt > 0 AND d_val >= 0 THEN d_val / dt END AS v
                FROM deltas
                WHERE collected_at >= :start AND dt > 0 AND d_val >= 0
            ) sub
            GROUP BY ts, interface
            ORDER BY ts, interface
        """)
        result = await self.session.execute(sql, {"sid": server_id, "start": start, "end": end, "window_start": start - _BUCKET_TD[bi], "dim": dimension})
        return result.all()

    async def _chart_fs(
        self, server_id: int, start: datetime, end: datetime, bi: str, ae: str, dimension: str | None,
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
                FROM {ServerMountUsage.__tablename__}
                WHERE server_id = :sid
                  AND collected_at >= :start
                  AND collected_at <= :end
                  AND (CAST(:dim AS text) IS NULL OR mount = :dim)
            ) sub
            GROUP BY ts, mount
            ORDER BY ts, mount
        """)
        result = await self.session.execute(sql, {"sid": server_id, "start": start, "end": end, "dim": dimension})
        return result.all()

    async def _chart_cpu_component(
        self, server_id: int, start: datetime, end: datetime, bi: str, ae: str, col: str,
    ):
        sql = text(f"""
            WITH raw AS (
                SELECT collected_at,
                    {col}                                                                           AS comp_j,
                    cpu_user+cpu_nice+cpu_system+cpu_idle+cpu_iowait+cpu_irq+cpu_softirq+cpu_steal AS total_j
                FROM {ServerMetrics.__tablename__}
                WHERE server_id = :sid
                  AND collected_at >= :window_start
                  AND collected_at <= :end
            ),
            deltas AS (
                SELECT collected_at,
                    GREATEST(0, comp_j  - LAG(comp_j)  OVER (ORDER BY collected_at)) AS d_comp,
                    GREATEST(0, total_j - LAG(total_j) OVER (ORDER BY collected_at)) AS d_total
                FROM raw
            )
            SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                   {ae}                                        AS value,
                   NULL::text                                  AS dimension
            FROM (
                SELECT collected_at,
                       CASE WHEN d_total > 0 THEN d_comp * 100.0 / d_total END AS v
                FROM deltas
                WHERE collected_at >= :start AND d_total > 0
            ) sub
            GROUP BY ts
            ORDER BY ts
        """)
        result = await self.session.execute(
            sql,
            {"sid": server_id, "start": start, "end": end, "window_start": start - _BUCKET_TD[bi]},
        )
        return result.all()

    async def _chart_load(
        self, server_id: int, start: datetime, end: datetime, bi: str, ae: str, col: str,
    ):
        sql = text(f"""
            SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                   {ae}                                        AS value,
                   NULL::text                                  AS dimension
            FROM (
                SELECT collected_at, {col} AS v
                FROM {ServerMetrics.__tablename__}
                WHERE server_id = :sid
                  AND collected_at >= :start
                  AND collected_at <= :end
                  AND {col} IS NOT NULL
            ) sub
            GROUP BY ts
            ORDER BY ts
        """)
        result = await self.session.execute(sql, {"sid": server_id, "start": start, "end": end})
        return result.all()

    async def _chart_mem(
        self, server_id: int, start: datetime, end: datetime, bi: str, ae: str, metric_type: str,
    ):
        expr_map = {
            "mem.usage_percent":     "CASE WHEN mem_total_kb > 0 THEN (mem_total_kb - mem_available_kb)::float / mem_total_kb * 100 END",
            "mem.available_percent": "CASE WHEN mem_total_kb > 0 THEN mem_available_kb::float / mem_total_kb * 100 END",
            "mem.cached_percent":    "CASE WHEN mem_total_kb > 0 THEN mem_cached_kb::float  / mem_total_kb * 100 END",
            "mem.buffers_percent":   "CASE WHEN mem_total_kb > 0 THEN mem_buffers_kb::float / mem_total_kb * 100 END",
            "swap.usage_percent":    "CASE WHEN swap_total_kb > 0 THEN (swap_total_kb - swap_free_kb)::float / swap_total_kb * 100 END",
        }
        expr = expr_map[metric_type]
        sql = text(f"""
            SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                   {ae}                                        AS value,
                   NULL::text                                  AS dimension
            FROM (
                SELECT collected_at, {expr} AS v
                FROM {ServerMetrics.__tablename__}
                WHERE server_id = :sid
                  AND collected_at >= :start
                  AND collected_at <= :end
            ) sub
            WHERE v IS NOT NULL
            GROUP BY ts
            ORDER BY ts
        """)
        result = await self.session.execute(sql, {"sid": server_id, "start": start, "end": end})
        return result.all()