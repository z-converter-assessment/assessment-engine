from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from assessment_engine.db.models.server_disk_io import ServerDiskIo
from assessment_engine.db.models.server_inventory import ServerInventory
from assessment_engine.db.models.server_metrics import ServerMetrics
from assessment_engine.db.models.server_mount_usage import ServerMountUsage
from assessment_engine.db.models.server_net_io import ServerNetIo
from assessment_engine.db.repositories.base_query_repository import (
    AggFunc,
    BaseQueryRepository,
    BucketSize,
    MetricType,
    TimeRange,
)
from assessment_engine.db.repositories.outbound import (
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


# ─── 시간 매핑 ─────────────────────────────────────────────────────────────

_TIME_RANGE: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h":  timedelta(hours=1),
    "6h":  timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d":  timedelta(days=7),
    "30d": timedelta(days=30),
}

# (SQL interval 문자열, Python timedelta) — bucket 단위를 SQL과 Python 양쪽에서 사용
_BUCKET_INFO: dict[str, tuple[str, timedelta]] = {
    "1m":  ("1 minute",   timedelta(minutes=1)),
    "5m":  ("5 minutes",  timedelta(minutes=5)),
    "15m": ("15 minutes", timedelta(minutes=15)),
    "30m": ("30 minutes", timedelta(minutes=30)),
    "1h":  ("1 hour",     timedelta(hours=1)),
    "3h":  ("3 hours",    timedelta(hours=3)),
    "12h": ("12 hours",   timedelta(hours=12)),
    "1d":  ("1 day",      timedelta(days=1)),
}

_AGG: dict[str, str] = {
    "avg": "avg(v)",
    "max": "max(v)",
    "p95": "percentile_cont(0.95) WITHIN GROUP (ORDER BY v)",
}

# ─── chart dispatch 매핑 (router Literal로 whitelist된 metric_type만 도달) ─

# CPU 누적 jiffies. delta로 % 계산 (LAG 기반). active/component 모두 분자만 다름.
_CPU_TOTAL_EXPR = "cpu_user+cpu_nice+cpu_system+cpu_idle+cpu_iowait+cpu_irq+cpu_softirq+cpu_steal"
_CPU_NUMERATOR: dict[str, str] = {
    "cpu.usage_percent":  "cpu_user+cpu_nice+cpu_system+cpu_iowait+cpu_irq+cpu_softirq+cpu_steal",
    "cpu.user_percent":   "cpu_user",
    "cpu.system_percent": "cpu_system",
    "cpu.iowait_percent": "cpu_iowait",
}

# 시점 값. dimension 없음. value_expr는 server_metrics 컬럼/식.
_SCALAR_VALUE_EXPR: dict[str, str] = {
    "load.1m":  "load_1m",
    "load.5m":  "load_5m",
    "load.15m": "load_15m",
    "mem.usage_percent":     "CASE WHEN mem_total_kb > 0 THEN (mem_total_kb - mem_available_kb)::float / mem_total_kb * 100 END",
    "mem.available_percent": "CASE WHEN mem_total_kb > 0 THEN mem_available_kb::float / mem_total_kb * 100 END",
    "mem.cached_percent":    "CASE WHEN mem_total_kb > 0 THEN mem_cached_kb::float  / mem_total_kb * 100 END",
    "mem.buffers_percent":   "CASE WHEN mem_total_kb > 0 THEN mem_buffers_kb::float / mem_total_kb * 100 END",
    "swap.usage_percent":    "CASE WHEN swap_total_kb > 0 THEN (swap_total_kb - swap_free_kb)::float / swap_total_kb * 100 END",
}

# (table, dim_col, value_col) 튜플 — disk/net rate per dimension
_RATE_PER_DIM: dict[str, tuple[str, str, str]] = {
    "disk.read_iops":        (ServerDiskIo.__tablename__, "device",    "reads_completed"),
    "disk.write_iops":       (ServerDiskIo.__tablename__, "device",    "writes_completed"),
    "net.rx_bytes_per_sec":  (ServerNetIo.__tablename__,  "interface", "rx_bytes"),
    "net.tx_bytes_per_sec":  (ServerNetIo.__tablename__,  "interface", "tx_bytes"),
}


class QueryRepository(BaseQueryRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    # ─── inventory ─────────────────────────────────────────────────────────

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
        # mounts/listen_ports/kernel_version 등 큰 JSONB·텍스트는 list 화면 미사용 — 명시 SELECT.
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
                ServerInventory.last_seen_at,
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
                last_seen_at=r.last_seen_at,
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

        rows = await self._latest_per_dimension(ServerMountUsage.__tablename__, "mount", server_id, n=1)
        mount_usage = [
            MountUsageRaw(
                mount=row.mount,
                total_bytes=row.total_bytes,
                avail_bytes=row.avail_bytes,
                free_bytes=row.free_bytes,
                collected_at=row.collected_at,
            )
            for row in rows
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

        rows = await self._latest_per_dimension(ServerNetIo.__tablename__, "interface", server_id, n=2)
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
            for row in rows
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

    # ─── collection status ────────────────────────────────────────────────

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

    # ─── dashboard (delta 계산용 raw N행) ─────────────────────────────────

    async def latest_dashboard(self, server_id: int) -> DashboardRaw | None:
        exists = await self.session.execute(
            select(ServerInventory.id).where(ServerInventory.id == server_id)
        )
        if not exists.scalar_one_or_none():
            return None

        # server_metrics는 dimension 없음. 단순 최신 2행.
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

        d_rows = await self._latest_per_dimension(ServerDiskIo.__tablename__, "device", server_id, n=2)
        disk_io = [
            DiskIoRaw(
                device=row.device,
                collected_at=row.collected_at,
                reads_completed=row.reads_completed,
                writes_completed=row.writes_completed,
                sectors_read=row.sectors_read,
                sectors_written=row.sectors_written,
            )
            for row in d_rows
        ]

        n_rows = await self._latest_per_dimension(ServerNetIo.__tablename__, "interface", server_id, n=2)
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
            for row in n_rows
        ]

        mu_rows = await self._latest_per_dimension(ServerMountUsage.__tablename__, "mount", server_id, n=1)
        mounts = [
            MountUsageRaw(
                mount=row.mount,
                total_bytes=row.total_bytes,
                avail_bytes=row.avail_bytes,
                free_bytes=row.free_bytes,
                collected_at=row.collected_at,
            )
            for row in mu_rows
        ]

        return DashboardRaw(metrics=metrics, disk_io=disk_io, net_io=net_io, mounts=mounts)

    # ─── series ───────────────────────────────────────────────────────────

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
        start = end_dt - _TIME_RANGE[time_range]
        bi, bucket_td = _BUCKET_INFO[bucket]
        ae = _AGG[agg]

        # router Literal(MetricType)로 metric_type가 whitelist 되어 있어 dispatch는 단순 lookup.
        if metric_type in _CPU_NUMERATOR:
            return await self._chart_cpu_delta(
                server_id, start, end_dt, bi, ae, bucket_td, _CPU_NUMERATOR[metric_type],
            )
        if metric_type in _SCALAR_VALUE_EXPR:
            return await self._chart_scalar(
                server_id, start, end_dt, bi, ae, _SCALAR_VALUE_EXPR[metric_type],
            )
        if metric_type in _RATE_PER_DIM:
            table, dim_col, value_col = _RATE_PER_DIM[metric_type]
            return await self._chart_rate_per_dimension(
                server_id, start, end_dt, bi, ae, bucket_td,
                table, dim_col, value_col, dimension,
            )
        if metric_type == "fs.usage_percent":
            return await self._chart_fs(server_id, start, end_dt, bi, ae, dimension)

        raise AssertionError(f"unreachable: unknown metric_type {metric_type!r}")

    # ─── private helpers ──────────────────────────────────────────────────

    async def _latest_per_dimension(
        self,
        table: str,
        dim_col: str,
        server_id: int,
        n: int,
    ) -> list[Any]:
        """{table}에서 (server_id 한정) {dim_col}별 최신 n행 반환.

        n=1: DISTINCT ON (가장 단순), n>=2: PARTITION BY + ROW_NUMBER.
        table·dim_col은 ORM 모델의 정적 attribute로 whitelisted — SQL에 직접 포맷.
        """
        if n == 1:
            sql = text(f"""
                SELECT *
                FROM (
                    SELECT DISTINCT ON ({dim_col}) *
                    FROM {table}
                    WHERE server_id = :sid
                    ORDER BY {dim_col}, collected_at DESC
                ) s
                ORDER BY {dim_col}
            """)
            params: dict[str, Any] = {"sid": server_id}
        else:
            sql = text(f"""
                SELECT *
                FROM (
                    SELECT *,
                        ROW_NUMBER() OVER (PARTITION BY {dim_col} ORDER BY collected_at DESC) AS rn
                    FROM {table}
                    WHERE server_id = :sid
                ) t
                WHERE rn <= :n
                ORDER BY {dim_col}, collected_at DESC
            """)
            params = {"sid": server_id, "n": n}
        result = await self.session.execute(sql, params)
        return result.all()

    async def _chart_cpu_delta(
        self,
        server_id: int,
        start: datetime,
        end: datetime,
        bi: str,
        ae: str,
        bucket_td: timedelta,
        numerator_expr: str,
    ) -> list[MetricSeries]:
        """LAG 기반 CPU jiffies delta. numerator_expr만 다른 cpu_active / cpu_user / cpu_system / cpu_iowait 통합.

        window_start = start - bucket_td (LAG 시 첫 행의 d_total/d_active 계산을 위해 한 버킷 앞 데이터 필요).
        """
        sql = text(f"""
            WITH raw AS (
                SELECT collected_at,
                    {numerator_expr}    AS num_j,
                    {_CPU_TOTAL_EXPR}   AS total_j
                FROM {ServerMetrics.__tablename__}
                WHERE server_id = :sid
                  AND collected_at >= :window_start
                  AND collected_at <= :end
            ),
            deltas AS (
                SELECT collected_at,
                    GREATEST(0, num_j   - LAG(num_j)   OVER (ORDER BY collected_at)) AS d_num,
                    GREATEST(0, total_j - LAG(total_j) OVER (ORDER BY collected_at)) AS d_total
                FROM raw
            )
            SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                   {ae}                                        AS value,
                   NULL::text                                  AS dimension
            FROM (
                SELECT collected_at,
                       CASE WHEN d_total > 0 THEN d_num * 100.0 / d_total END AS v
                FROM deltas
                WHERE collected_at >= :start AND d_total > 0
            ) sub
            GROUP BY ts
            ORDER BY ts
        """)
        result = await self.session.execute(
            sql,
            {"sid": server_id, "start": start, "end": end, "window_start": start - bucket_td},
        )
        return [MetricSeries(collected_at=row.ts, value=row.value, dimension=row.dimension) for row in result.all()]

    async def _chart_scalar(
        self,
        server_id: int,
        start: datetime,
        end: datetime,
        bi: str,
        ae: str,
        value_expr: str,
    ) -> list[MetricSeries]:
        """server_metrics에서 dimension 없는 시점 값 (load/mem/swap %).

        value_expr는 _SCALAR_VALUE_EXPR로 whitelist된 SQL 식 (단순 컬럼 또는 CASE WHEN).
        """
        sql = text(f"""
            SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                   {ae}                                        AS value,
                   NULL::text                                  AS dimension
            FROM (
                SELECT collected_at, {value_expr} AS v
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
        return [MetricSeries(collected_at=row.ts, value=row.value, dimension=row.dimension) for row in result.all()]

    async def _chart_rate_per_dimension(
        self,
        server_id: int,
        start: datetime,
        end: datetime,
        bi: str,
        ae: str,
        bucket_td: timedelta,
        table: str,
        dim_col: str,
        value_col: str,
        dimension: str | None,
    ) -> list[MetricSeries]:
        """LAG 기반 누적 카운터의 시간당 변화율. disk_io / net_io 통합.

        - disk: reads_completed/writes_completed → IOPS (count/sec)
        - net: rx_bytes/tx_bytes → bytes/sec

        table·dim_col·value_col은 _RATE_PER_DIM dispatch 매핑으로 whitelist.
        dimension이 있으면 그 dimension만 필터.
        """
        sql = text(f"""
            WITH raw AS (
                SELECT collected_at, {dim_col} AS dim, {value_col} AS cnt
                FROM {table}
                WHERE server_id = :sid
                  AND collected_at >= :window_start
                  AND collected_at <= :end
                  AND (CAST(:dim_filter AS text) IS NULL OR {dim_col} = :dim_filter)
            ),
            deltas AS (
                SELECT collected_at, dim,
                    cnt - LAG(cnt) OVER (PARTITION BY dim ORDER BY collected_at) AS d_val,
                    EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER (PARTITION BY dim ORDER BY collected_at))) AS dt
                FROM raw
            )
            SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                   {ae}                                        AS value,
                   dim                                         AS dimension
            FROM (
                SELECT collected_at, dim,
                       CASE WHEN dt > 0 AND d_val >= 0 THEN d_val / dt END AS v
                FROM deltas
                WHERE collected_at >= :start AND dt > 0 AND d_val >= 0
            ) sub
            GROUP BY ts, dim
            ORDER BY ts, dim
        """)
        result = await self.session.execute(
            sql,
            {
                "sid": server_id,
                "start": start,
                "end": end,
                "window_start": start - bucket_td,
                "dim_filter": dimension,
            },
        )
        return [MetricSeries(collected_at=row.ts, value=row.value, dimension=row.dimension) for row in result.all()]

    async def _chart_fs(
        self,
        server_id: int,
        start: datetime,
        end: datetime,
        bi: str,
        ae: str,
        dimension: str | None,
    ) -> list[MetricSeries]:
        """server_mount_usage의 시점 사용률 % (mount당). LAG 불필요 — total/avail이 시점 값."""
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
                  AND (CAST(:dim_filter AS text) IS NULL OR mount = :dim_filter)
            ) sub
            GROUP BY ts, mount
            ORDER BY ts, mount
        """)
        result = await self.session.execute(
            sql,
            {"sid": server_id, "start": start, "end": end, "dim_filter": dimension},
        )
        return [MetricSeries(collected_at=row.ts, value=row.value, dimension=row.dimension) for row in result.all()]