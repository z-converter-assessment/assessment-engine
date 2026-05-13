from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from assessment_engine.db.models.server_disk_io import ServerDiskIo
from assessment_engine.db.models.server_inventory import ServerInventory
from assessment_engine.db.models.server_metrics import ServerMetrics
from assessment_engine.db.models.server_mount_usage import ServerMountUsage
from assessment_engine.db.models.server_net_io import ServerNetIo
from assessment_engine.db.repositories.base_query_repository import (
    TIME_RANGE_TD,
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
    DiskUsageWarningRaw,
    EnvironmentUtilizationRaw,
    MetricGapWarningRaw,
    MetricPairRaw,
    MetricSeries,
    MountUsageRaw,
    NetIoRaw,
    NetworkWithIo,
    RebootEvent,
    ReportRowRaw,
    ServerDetail,
    ServerSummary,
    StorageWithUsage,
)

# ─── 시간 매핑 ─────────────────────────────────────────────────────────────
# TimeRange→timedelta는 base_query_repository에서 import (TIME_RANGE_TD) — service와 공유.

# (SQL interval 문자열, Python timedelta) — bucket 단위를 SQL과 Python 양쪽에서 사용
_BUCKET_INFO: dict[str, tuple[str, timedelta]] = {
    "1m":  ("1 minute",   timedelta(minutes=1)),
    "5m":  ("5 minutes",  timedelta(minutes=5)),
    "15m": ("15 minutes", timedelta(minutes=15)),
    "30m": ("30 minutes", timedelta(minutes=30)),
    "1h":  ("1 hour",     timedelta(hours=1)),
    "3h":  ("3 hours",    timedelta(hours=3)),
    "6h":  ("6 hours",    timedelta(hours=6)),
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

# server_mount_usage 가상 mount 필터 — SQL fragment 단일 진실.
# 모든 mount 합산·집계 SQL이 본 fragment를 적용. 변경 시 device_filters._VIRTUAL_MOUNT_PREFIXES와 동기화.
# ServerMountUsage 모델에 fstype 컬럼이 없어 path 기반만 (storage detail mapper는 fstype도 사용 — defense in depth).
# 사용자 입력 0 — 정적 상수만이라 f-string 안전 (CLAUDE.md C5 whitelist).
_VIRTUAL_MOUNT_SQL_FILTER = """
    mount NOT LIKE '/proc%'
    AND mount NOT LIKE '/sys%'
    AND mount NOT LIKE '/dev/pts%'
    AND mount NOT LIKE '/snap%'
    AND mount NOT LIKE '/run/snapd%'
    AND mount NOT LIKE '/mnt/lima-cidata%'
"""


class QueryRepository(BaseQueryRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    # ─── inventory ─────────────────────────────────────────────────────────

    async def resolve_server_id(self, public_id: str) -> int | None:
        result = await self.session.execute(
            select(ServerInventory.id).where(ServerInventory.public_id == public_id)
        )
        return result.scalar_one_or_none()

    async def resolve_server_ids(self, public_ids: list[str]) -> dict[str, int]:
        if not public_ids:
            return {}
        result = await self.session.execute(
            select(ServerInventory.public_id, ServerInventory.id).where(
                ServerInventory.public_id.in_(public_ids)
            )
        )
        return {str(r.public_id): r.id for r in result.all()}

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

    @staticmethod
    def _row_to_server_detail(r: ServerInventory) -> ServerDetail:
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

    async def get_server(self, server_id: int) -> ServerDetail | None:
        result = await self.session.execute(
            select(ServerInventory).where(ServerInventory.id == server_id)
        )
        r = result.scalars().one_or_none()
        return self._row_to_server_detail(r) if r is not None else None

    async def get_servers(self, server_ids: list[int]) -> list[ServerDetail]:
        if not server_ids:
            return []
        result = await self.session.execute(
            select(ServerInventory).where(ServerInventory.id.in_(server_ids))
        )
        return [self._row_to_server_detail(r) for r in result.scalars().all()]

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
                boot_time=row.boot_time,
                agent_started_at=row.agent_started_at,
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
                boot_time=m.boot_time, agent_started_at=m.agent_started_at,
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
                boot_time=row.boot_time,
                agent_started_at=row.agent_started_at,
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
                boot_time=row.boot_time,
                agent_started_at=row.agent_started_at,
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
                boot_time=row.boot_time,
                agent_started_at=row.agent_started_at,
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
        end_dt = end or datetime.now(UTC)
        start = end_dt - TIME_RANGE_TD[time_range]
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
        table·dim_col은 ORM 모델의 정적 attribute로 whitelisted — SQL에 직접 포맷 (C5 예외 — dispatch table whitelist만).

        C5: hypertable partition pruning 의무. 30d 윈도우 — 30d 이상 오프라인 서버는 metrics 조회 의미 약함 + 7d chunk 기준 4~5 chunk만 스캔.
        """
        if n == 1:
            sql = text(f"""
                SELECT *
                FROM (
                    SELECT DISTINCT ON ({dim_col}) *
                    FROM {table}
                    WHERE server_id = :sid AND collected_at >= now() - interval '30 days'
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
                    WHERE server_id = :sid AND collected_at >= now() - interval '30 days'
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

        reset 식별 (calculator와 동일 정책 — CLAUDE.md B1):
        - boot_time != prev_boot → 시스템 재부팅 → NULL (단순 음수가 아닌 진짜 reset)
        - d_total <= 0 또는 d_num < 0 → NULL (옛 데이터 휴리스틱 fallback / wrap-around)
        - 정상: d_num * 100 / d_total
        시간차(dt)는 percent 계산엔 무관 (jiffies 비율이라 자연 정규화).
        """
        sql = text(f"""
            WITH raw AS (
                SELECT collected_at, boot_time,
                    {numerator_expr}    AS num_j,
                    {_CPU_TOTAL_EXPR}   AS total_j
                FROM {ServerMetrics.__tablename__}
                WHERE server_id = :sid
                  AND collected_at >= :window_start
                  AND collected_at <= :end
            ),
            deltas AS (
                SELECT collected_at, boot_time,
                    LAG(boot_time) OVER (ORDER BY collected_at) AS prev_boot,
                    num_j   - LAG(num_j)   OVER (ORDER BY collected_at) AS d_num,
                    total_j - LAG(total_j) OVER (ORDER BY collected_at) AS d_total
                FROM raw
            )
            SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                   {ae}                                        AS value,
                   NULL::text                                  AS dimension
            FROM (
                SELECT collected_at,
                       CASE
                           WHEN boot_time IS NOT NULL AND prev_boot IS NOT NULL AND boot_time != prev_boot THEN NULL
                           WHEN d_total IS NULL OR d_total <= 0 OR d_num < 0 THEN NULL
                           ELSE d_num * 100.0 / d_total
                       END AS v
                FROM deltas
                WHERE collected_at >= :start
            ) sub
            WHERE v IS NOT NULL
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

        reset 식별 우선순위 (calculator와 동일 정책 — CLAUDE.md B1):
        ① dt 검증: dt <= 0 (동일 시점·역행) → NULL. dt 자체는 분모일 뿐 1분/3분 무관 — 실제 시간으로 자연 정규화.
        ② boot_time 검증: boot_time != prev_boot → 시스템 재부팅 → NULL (reset 확정).
        ③ 음수 delta: d_val < 0 → NULL (옛 데이터 휴리스틱 fallback / wrap-around).
        ④ 정상: d_val / dt
        """
        sql = text(f"""
            WITH raw AS (
                SELECT collected_at, boot_time, {dim_col} AS dim, {value_col} AS cnt
                FROM {table}
                WHERE server_id = :sid
                  AND collected_at >= :window_start
                  AND collected_at <= :end
                  AND (CAST(:dim_filter AS text) IS NULL OR {dim_col} = :dim_filter)
            ),
            deltas AS (
                SELECT collected_at, dim, boot_time,
                    LAG(boot_time) OVER (PARTITION BY dim ORDER BY collected_at) AS prev_boot,
                    cnt - LAG(cnt) OVER (PARTITION BY dim ORDER BY collected_at) AS d_val,
                    EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER (PARTITION BY dim ORDER BY collected_at))) AS dt
                FROM raw
            )
            SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                   {ae}                                        AS value,
                   dim                                         AS dimension
            FROM (
                SELECT collected_at, dim,
                       CASE
                           WHEN dt IS NULL OR dt <= 0 THEN NULL
                           WHEN boot_time IS NOT NULL AND prev_boot IS NOT NULL AND boot_time != prev_boot THEN NULL
                           WHEN d_val IS NULL OR d_val < 0 THEN NULL
                           ELSE d_val / dt
                       END AS v
                FROM deltas
                WHERE collected_at >= :start
            ) sub
            WHERE v IS NOT NULL
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

    # ─── Assessment 보고서 집계 (USE Method) ──────────

    async def report_aggregate(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> list[ReportRowRaw]:
        """N서버 x period_days 통계 → ReportRowRaw list. role/recommendation 등 표시 파생은 service에서.

        SQL 구조:
        - cpu_pct CTE: LAG로 jiffies delta → (1 - d_idle/d_total) x 100. boot_time 변경 시 reset 제외.
        - mem_pct CTE: 시점값 (1 - mem_available/mem_total) x 100. swap_used flag 동시 추출.
        - 통계: percentile_cont(0.95) + MAX. server_id별 GROUP BY.
        - server_inventory LEFT JOIN — metric 없는 서버도 행 반환. services JSONB 동시 SELECT (N+1 회피).
        """
        start = end - timedelta(days=period_days)

        sql = text("""
            WITH cpu_deltas AS (
                SELECT server_id,
                    boot_time,
                    LAG(boot_time) OVER (PARTITION BY server_id ORDER BY collected_at) AS prev_boot,
                    cpu_idle - LAG(cpu_idle) OVER (PARTITION BY server_id ORDER BY collected_at) AS d_idle,
                    cpu_iowait - LAG(cpu_iowait) OVER (PARTITION BY server_id ORDER BY collected_at) AS d_iowait,
                    (cpu_user + cpu_nice + cpu_system + cpu_idle + cpu_iowait + cpu_irq + cpu_softirq + cpu_steal)
                      - LAG(cpu_user + cpu_nice + cpu_system + cpu_idle + cpu_iowait + cpu_irq + cpu_softirq + cpu_steal)
                        OVER (PARTITION BY server_id ORDER BY collected_at) AS d_total
                FROM server_metrics
                WHERE server_id = ANY(:sids) AND collected_at >= :start AND collected_at <= :end
            ),
            cpu_pct AS (
                SELECT server_id,
                    CASE WHEN d_total > 0 AND d_idle IS NOT NULL
                         THEN GREATEST(0, (1 - d_idle::float / d_total) * 100)
                    END AS pct,
                    CASE WHEN d_total > 0 AND d_iowait IS NOT NULL
                         THEN GREATEST(0, d_iowait::float / d_total * 100)
                    END AS iowait_pct
                FROM cpu_deltas
                WHERE d_total > 0
                  AND (boot_time IS NULL OR prev_boot IS NULL OR boot_time = prev_boot)
            ),
            cpu_stats AS (
                SELECT server_id,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY pct) AS cpu_p95,
                    MAX(pct) AS cpu_peak,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY iowait_pct) AS iowait_p95,
                    MAX(iowait_pct) AS iowait_peak
                FROM cpu_pct GROUP BY server_id
            ),
            mem_pct AS (
                SELECT server_id,
                    CASE WHEN mem_total_kb > 0 AND mem_available_kb IS NOT NULL
                         THEN (1 - mem_available_kb::float / mem_total_kb) * 100
                    END AS pct,
                    CASE WHEN swap_total_kb > 0 AND swap_free_kb IS NOT NULL
                              AND swap_free_kb < swap_total_kb
                         THEN 1 ELSE 0
                    END AS swap_in_use
                FROM server_metrics
                WHERE server_id = ANY(:sids) AND collected_at >= :start AND collected_at <= :end
            ),
            mem_stats AS (
                SELECT server_id,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY pct) AS mem_p95,
                    MAX(pct) AS mem_peak,
                    MAX(swap_in_use) > 0 AS swap_used
                FROM mem_pct
                WHERE pct IS NOT NULL
                GROUP BY server_id
            ),
            load_stats AS (
                SELECT server_id, MAX(load_15m) AS load_15m_max
                FROM server_metrics
                WHERE server_id = ANY(:sids) AND collected_at >= :start AND collected_at <= :end
                GROUP BY server_id
            )
            SELECT
                s.id            AS server_id,
                s.public_id     AS public_id,
                s.hostname      AS hostname,
                s.os_id         AS os_id,
                s.os_version    AS os_version,
                s.kernel_version AS kernel_version,
                s.ip_internal   AS ip_internal,
                s.services      AS services,
                s.last_seen_at  AS last_seen_at,
                s.cpu_cores     AS cpu_cores,
                s.mem_total_kb  AS mem_total_kb,
                s.disks         AS disks,
                s.boot_time     AS boot_time,
                cs.cpu_p95      AS cpu_p95,
                cs.cpu_peak     AS cpu_peak,
                cs.iowait_p95   AS iowait_p95,
                cs.iowait_peak  AS iowait_peak,
                ms.mem_p95      AS mem_p95,
                ms.mem_peak     AS mem_peak,
                COALESCE(ms.swap_used, false) AS swap_used,
                ls.load_15m_max AS load_15m_max
            FROM server_inventory s
            LEFT JOIN cpu_stats  cs ON cs.server_id = s.id
            LEFT JOIN mem_stats  ms ON ms.server_id = s.id
            LEFT JOIN load_stats ls ON ls.server_id = s.id
            WHERE s.id = ANY(:sids)
            ORDER BY s.hostname
        """)
        result = await self.session.execute(sql, {"sids": server_ids, "start": start, "end": end})

        return [
            ReportRowRaw(
                server_id=r.server_id,
                public_id=str(r.public_id),
                hostname=r.hostname,
                os_id=r.os_id,
                os_version=r.os_version,
                kernel_version=r.kernel_version,
                ip_internal=r.ip_internal,
                services=r.services,
                last_seen_at=r.last_seen_at,
                cpu_p95_pct=r.cpu_p95,
                cpu_peak_pct=r.cpu_peak,
                mem_p95_pct=r.mem_p95,
                mem_peak_pct=r.mem_peak,
                load_15m_max=r.load_15m_max,
                swap_used=bool(r.swap_used),
                iowait_p95_pct=r.iowait_p95,
                iowait_peak_pct=r.iowait_peak,
                cpu_cores=r.cpu_cores,
                mem_total_kb=r.mem_total_kb,
                disks=r.disks,
                boot_time=r.boot_time,
            )
            for r in result.all()
        ]

    async def report_mount_worst(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> dict[int, tuple[str | None, float | None, int | None]]:
        """마운트별 max used_pct + fill_rate 기반 days_until_full 추정. 서버당 최악 1건만 반환.

        SQL 구조:
        - mount_stats CTE: (server_id, mount)별 max used_pct + FIRST/LAST avail_bytes로 fill_rate
        - 서버당 worst = used_pct DESC 첫 행 (used_pct 동률 시 days_until_full ASC)
        """
        start = end - timedelta(days=period_days)

        sql = text(f"""
            WITH usage_max AS (
                -- (server_id, mount)별 max used_pct. 가상 mount 제외 — 단일 진실 _VIRTUAL_MOUNT_SQL_FILTER.
                SELECT server_id, mount,
                    MAX((1 - avail_bytes::float / total_bytes) * 100) AS max_used_pct
                FROM server_mount_usage
                WHERE server_id = ANY(:sids) AND collected_at >= :start AND collected_at <= :end
                  AND total_bytes > 0
                  AND {_VIRTUAL_MOUNT_SQL_FILTER}
                GROUP BY server_id, mount
            ),
            fill_rate AS (
                -- (server_id, mount)별 시작·종료 avail_bytes (FIRST/LAST 윈도우)
                SELECT DISTINCT server_id, mount,
                    FIRST_VALUE(avail_bytes) OVER w AS avail_start,
                    LAST_VALUE(avail_bytes) OVER w AS avail_end
                FROM server_mount_usage
                WHERE server_id = ANY(:sids) AND collected_at >= :start AND collected_at <= :end
                  AND total_bytes > 0
                  AND {_VIRTUAL_MOUNT_SQL_FILTER}
                WINDOW w AS (
                    PARTITION BY server_id, mount ORDER BY collected_at
                    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                )
            ),
            mount_per AS (
                SELECT um.server_id, um.mount, um.max_used_pct,
                    CASE WHEN (fr.avail_start - fr.avail_end) > 0
                              AND :period_days > 0 AND fr.avail_end >= 0
                         THEN GREATEST(0, FLOOR(
                                fr.avail_end / ((fr.avail_start - fr.avail_end)::float / :period_days)
                              ))::int
                    END AS days_until_full
                FROM usage_max um
                LEFT JOIN fill_rate fr ON fr.server_id = um.server_id AND fr.mount = um.mount
            ),
            ranked AS (
                SELECT server_id, mount, max_used_pct, days_until_full,
                    ROW_NUMBER() OVER (PARTITION BY server_id
                                       ORDER BY max_used_pct DESC NULLS LAST,
                                                days_until_full ASC NULLS LAST) AS rk
                FROM mount_per
            )
            SELECT server_id, mount, max_used_pct, days_until_full
            FROM ranked WHERE rk = 1
        """)
        result = await self.session.execute(
            sql, {"sids": server_ids, "start": start, "end": end, "period_days": period_days},
        )
        return {
            r.server_id: (r.mount, r.max_used_pct, r.days_until_full)
            for r in result.all()
        }

    async def report_uptime_stats(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> dict[int, int]:
        """period 안 server_inventory_history의 boot_time DISTINCT count - 1 (=재부팅 횟수).

        SQL: server_inventory_history의 boot_time DISTINCT - 1 (현재 boot_time 포함이라 -1).
        period 안 1회 부팅이면 reboot_count=0, 2회면 1 (=1회 재부팅).
        """
        start = end - timedelta(days=period_days)

        sql = text("""
            SELECT server_id, GREATEST(0, COUNT(DISTINCT boot_time) - 1) AS reboot_count
            FROM server_inventory_history
            WHERE server_id = ANY(:sids) AND collected_at >= :start AND collected_at <= :end
              AND boot_time IS NOT NULL
            GROUP BY server_id
        """)
        result = await self.session.execute(sql, {"sids": server_ids, "start": start, "end": end})
        return {r.server_id: int(r.reboot_count) for r in result.all()}

    async def report_disk_io_baseline(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> dict[int, tuple[int | None, float | None, float | None, float | None, float | None, float | None]]:
        """server_id -> (iops_baseline, throughput_kbps_baseline,
                          iops_p95, iops_peak, throughput_kbps_p95, throughput_kbps_peak).

        - baseline(평균) = SUM(delta) / SUM(dt) — 기존 의미 유지
        - p95/peak = 시점별 (서버, collected_at) device 합산 rate에서 percentile_cont(0.95) + MAX
        - reset 행 제외: dt > 0 AND delta >= 0
        """
        start = end - timedelta(days=period_days)

        sql = text("""
            WITH disk_deltas AS (
                SELECT server_id, device, collected_at,
                    reads_completed - LAG(reads_completed)
                        OVER (PARTITION BY server_id, device ORDER BY collected_at) AS d_reads,
                    writes_completed - LAG(writes_completed)
                        OVER (PARTITION BY server_id, device ORDER BY collected_at) AS d_writes,
                    sectors_read - LAG(sectors_read)
                        OVER (PARTITION BY server_id, device ORDER BY collected_at) AS d_sec_r,
                    sectors_written - LAG(sectors_written)
                        OVER (PARTITION BY server_id, device ORDER BY collected_at) AS d_sec_w,
                    EXTRACT(EPOCH FROM (collected_at - LAG(collected_at)
                        OVER (PARTITION BY server_id, device ORDER BY collected_at))) AS dt
                FROM server_disk_io
                WHERE server_id = ANY(:sids) AND collected_at >= :start AND collected_at <= :end
            ),
            disk_clean AS (
                SELECT server_id, collected_at,
                       (d_reads + d_writes) AS ops,
                       (d_sec_r + d_sec_w) * 512 AS bytes,
                       dt
                FROM disk_deltas
                WHERE dt > 0 AND d_reads >= 0 AND d_writes >= 0
                  AND d_sec_r >= 0 AND d_sec_w >= 0
            ),
            disk_baseline AS (
                SELECT server_id,
                    SUM(ops::float)            AS total_ops,
                    SUM(bytes::float)          AS total_bytes,
                    SUM(dt)                    AS total_seconds
                FROM disk_clean
                GROUP BY server_id
            ),
            disk_rate_per_time AS (
                SELECT server_id, collected_at,
                       SUM(ops::float / dt)             AS server_iops,
                       SUM(bytes::float / dt / 1024)    AS server_kbps
                FROM disk_clean
                GROUP BY server_id, collected_at
            ),
            disk_stats AS (
                SELECT server_id,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY server_iops) AS iops_p95,
                    MAX(server_iops) AS iops_peak,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY server_kbps) AS kbps_p95,
                    MAX(server_kbps) AS kbps_peak
                FROM disk_rate_per_time
                GROUP BY server_id
            )
            SELECT b.server_id,
                CASE WHEN b.total_seconds > 0 THEN b.total_ops / b.total_seconds END AS iops_baseline,
                CASE WHEN b.total_seconds > 0 THEN b.total_bytes / b.total_seconds / 1024 END AS throughput_kbps_baseline,
                s.iops_p95, s.iops_peak, s.kbps_p95, s.kbps_peak
            FROM disk_baseline b
            LEFT JOIN disk_stats s ON s.server_id = b.server_id
        """)
        result = await self.session.execute(sql, {"sids": server_ids, "start": start, "end": end})
        return {
            r.server_id: (
                int(r.iops_baseline) if r.iops_baseline is not None else None,
                float(r.throughput_kbps_baseline) if r.throughput_kbps_baseline is not None else None,
                float(r.iops_p95) if r.iops_p95 is not None else None,
                float(r.iops_peak) if r.iops_peak is not None else None,
                float(r.kbps_p95) if r.kbps_p95 is not None else None,
                float(r.kbps_peak) if r.kbps_peak is not None else None,
            )
            for r in result.all()
        }

    async def report_net_io_baseline(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> dict[int, tuple[float | None, float | None, float | None, float | None, float | None, float | None]]:
        """server_id -> (rx_kbps_baseline, tx_kbps_baseline,
                          rx_kbps_p95, rx_kbps_peak, tx_kbps_p95, tx_kbps_peak).

        시점별 interface 합산 rate에서 percentile_cont(0.95) + MAX. baseline은 SUM/SUM (기존 의미).
        """
        start = end - timedelta(days=period_days)

        sql = text("""
            WITH net_deltas AS (
                SELECT server_id, interface, collected_at,
                    rx_bytes - LAG(rx_bytes)
                        OVER (PARTITION BY server_id, interface ORDER BY collected_at) AS d_rx,
                    tx_bytes - LAG(tx_bytes)
                        OVER (PARTITION BY server_id, interface ORDER BY collected_at) AS d_tx,
                    EXTRACT(EPOCH FROM (collected_at - LAG(collected_at)
                        OVER (PARTITION BY server_id, interface ORDER BY collected_at))) AS dt
                FROM server_net_io
                WHERE server_id = ANY(:sids) AND collected_at >= :start AND collected_at <= :end
            ),
            net_clean AS (
                SELECT server_id, collected_at, d_rx, d_tx, dt
                FROM net_deltas
                WHERE dt > 0 AND d_rx >= 0 AND d_tx >= 0
            ),
            net_baseline AS (
                SELECT server_id,
                    SUM(d_rx::float) AS total_rx_bytes,
                    SUM(d_tx::float) AS total_tx_bytes,
                    SUM(dt)          AS total_seconds
                FROM net_clean
                GROUP BY server_id
            ),
            net_rate_per_time AS (
                SELECT server_id, collected_at,
                       SUM(d_rx::float / dt / 1024) AS server_rx_kbps,
                       SUM(d_tx::float / dt / 1024) AS server_tx_kbps
                FROM net_clean
                GROUP BY server_id, collected_at
            ),
            net_stats AS (
                SELECT server_id,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY server_rx_kbps) AS rx_p95,
                    MAX(server_rx_kbps) AS rx_peak,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY server_tx_kbps) AS tx_p95,
                    MAX(server_tx_kbps) AS tx_peak
                FROM net_rate_per_time
                GROUP BY server_id
            )
            SELECT b.server_id,
                CASE WHEN b.total_seconds > 0 THEN b.total_rx_bytes / b.total_seconds / 1024 END AS rx_kbps_baseline,
                CASE WHEN b.total_seconds > 0 THEN b.total_tx_bytes / b.total_seconds / 1024 END AS tx_kbps_baseline,
                s.rx_p95, s.rx_peak, s.tx_p95, s.tx_peak
            FROM net_baseline b
            LEFT JOIN net_stats s ON s.server_id = b.server_id
        """)
        result = await self.session.execute(sql, {"sids": server_ids, "start": start, "end": end})
        return {
            r.server_id: (
                float(r.rx_kbps_baseline) if r.rx_kbps_baseline is not None else None,
                float(r.tx_kbps_baseline) if r.tx_kbps_baseline is not None else None,
                float(r.rx_p95) if r.rx_p95 is not None else None,
                float(r.rx_peak) if r.rx_peak is not None else None,
                float(r.tx_p95) if r.tx_p95 is not None else None,
                float(r.tx_peak) if r.tx_peak is not None else None,
            )
            for r in result.all()
        }

    # ─── reboot / agent restart 이벤트 (차트 vertical marker용) ────────────

    async def reboot_events(
        self,
        server_id: int,
        start: datetime,
        end: datetime,
    ) -> list[RebootEvent]:
        """server_inventory_history에서 boot_time / agent_started_at 변경 시점 추출.

        history는 변경 trigger 시 행이 추가되므로 행 자체가 변경 이벤트. LAG 비교로
        boot_time / agent_started_at 변경만 필터 (services / listen_ports만 변경된 행은 제외).

        range 시작 직전 1행도 LAG 베이스로 포함 — start로 자르면 첫 변경의 prev 정보 없어
        분류 불가. start보다 이른 행은 결과에서 제외, LAG 계산용으로만 사용.

        NULL-safe 비교는 `IS DISTINCT FROM` (PostgreSQL) — 일반 != 는 NULL 비교 시 NULL 반환.
        """
        sql = text("""
            WITH base AS (
                SELECT collected_at, boot_time, agent_started_at,
                    LAG(boot_time)        OVER (ORDER BY collected_at) AS prev_boot,
                    LAG(agent_started_at) OVER (ORDER BY collected_at) AS prev_agent
                FROM server_inventory_history
                WHERE server_id = :sid
                  AND collected_at <= :end
            )
            SELECT collected_at, boot_time, agent_started_at,
                CASE
                    WHEN prev_boot IS NULL                            THEN 'reboot'
                    WHEN boot_time IS DISTINCT FROM prev_boot         THEN 'reboot'
                    WHEN agent_started_at IS DISTINCT FROM prev_agent THEN 'restart'
                    ELSE NULL
                END AS kind
            FROM base
            WHERE collected_at >= :start
              AND (
                  prev_boot IS NULL
                  OR boot_time        IS DISTINCT FROM prev_boot
                  OR agent_started_at IS DISTINCT FROM prev_agent
              )
            ORDER BY collected_at
        """)
        result = await self.session.execute(
            sql,
            {"sid": server_id, "start": start, "end": end},
        )
        return [
            RebootEvent(
                collected_at=row.collected_at,
                boot_time=row.boot_time,
                agent_started_at=row.agent_started_at,
                kind=row.kind,
            )
            for row in result.all()
            if row.kind is not None
        ]

    # ─── 주의 신호 (목록 화면 상단 — risk_top 보완) ─────────────────────────

    async def list_server_ids(self, limit: int = 1000) -> list[int]:
        result = await self.session.execute(
            select(ServerInventory.id).order_by(ServerInventory.id.asc()).limit(limit)
        )
        return [r for r in result.scalars().all()]

    async def disk_usage_warnings(
        self,
        threshold_pct: float,
        limit: int,
    ) -> list[DiskUsageWarningRaw]:
        """전체 mount 중 latest 사용률 임계 초과만 단일 SQL.

        - mount당 최신 1행 (PARTITION BY server_id, mount ORDER BY collected_at DESC)
        - 7d partition pruning — attention은 "현재 시점 단기 신호" 의도. 7d 이상 안 갱신된 mount는 stale (metric_gap이 별도 담당)
        - 가상 mount 제외:
          (a) `total_bytes > 0 AND avail_bytes IS NOT NULL` — 가상 fs는 보통 둘 중 하나 NULL/0
          (b) mount path NOT LIKE 가상 prefix — `device_filters._VIRTUAL_MOUNT_PREFIXES`와 일관.
              ServerMountUsage에 fstype 컬럼이 없어 SQL 단에선 path 기반만 가능. fstype 기반 정밀
              필터는 storage detail mapper 측에서 수행 (defense in depth).
        """
        sql = text(f"""
            WITH mount_latest AS (
                SELECT server_id, mount, total_bytes, avail_bytes, collected_at,
                    ROW_NUMBER() OVER (PARTITION BY server_id, mount ORDER BY collected_at DESC) AS rn
                FROM server_mount_usage
                WHERE collected_at >= now() - interval '7 days'
                  AND {_VIRTUAL_MOUNT_SQL_FILTER}
            )
            SELECT s.public_id AS public_id,
                   s.hostname  AS hostname,
                   m.mount     AS mount,
                   m.total_bytes AS total_bytes,
                   m.avail_bytes AS avail_bytes,
                   m.collected_at AS last_metric_at
            FROM mount_latest m
            JOIN server_inventory s ON s.id = m.server_id
            WHERE m.rn = 1
              AND m.total_bytes > 0
              AND m.avail_bytes IS NOT NULL
              AND (1 - m.avail_bytes::float / m.total_bytes) >= :threshold
            ORDER BY (1 - m.avail_bytes::float / m.total_bytes) DESC
            LIMIT :limit
        """)
        result = await self.session.execute(
            sql,
            {"threshold": threshold_pct / 100.0, "limit": limit},
        )
        return [
            DiskUsageWarningRaw(
                public_id=str(r.public_id),
                hostname=r.hostname,
                mount=r.mount,
                total_bytes=r.total_bytes,
                avail_bytes=r.avail_bytes,
                last_metric_at=r.last_metric_at,
            )
            for r in result.all()
        ]

    async def metric_gap_warnings(
        self,
        gap_minutes: int,
        recent_hours: int,
        limit: int,
    ) -> list[MetricGapWarningRaw]:
        """metric 발행 갭 — '한때 살아있다 끊김' 패턴.

        - last_metric_at < now() - gap_minutes (현재 끊김)
        - last_metric_at > now() - recent_hours (한때는 살아있음 — 완전 dead 서버 제외)
        - partition pruning: recent_hours를 동적 binding + LEAST 168h(7d) cap.
          호출자 실수로 큰 값 넘겨도 자동 cap — 7d 이상은 metric_gap 의미 없음 (다른 신호 영역).
        """
        sql = text("""
            WITH metric_max AS (
                SELECT server_id, MAX(collected_at) AS last_metric_at
                FROM server_metrics
                WHERE collected_at >= now() - (LEAST(:recent_h, 168) * interval '1 hour')
                GROUP BY server_id
            )
            SELECT s.public_id AS public_id,
                   s.hostname  AS hostname,
                   mm.last_metric_at AS last_metric_at
            FROM server_inventory s
            JOIN metric_max mm ON mm.server_id = s.id
            WHERE mm.last_metric_at < now() - (:gap_min * interval '1 minute')
              AND mm.last_metric_at > now() - (:recent_h * interval '1 hour')
            ORDER BY mm.last_metric_at ASC
            LIMIT :limit
        """)
        result = await self.session.execute(
            sql,
            {"gap_min": gap_minutes, "recent_h": recent_hours, "limit": limit},
        )
        return [
            MetricGapWarningRaw(
                public_id=str(r.public_id),
                hostname=r.hostname,
                last_metric_at=r.last_metric_at,
            )
            for r in result.all()
        ]

    async def environment_utilization(
        self,
        period_days: int = 1,
    ) -> EnvironmentUtilizationRaw:
        """환경 전체 서버 N일 평균 활용률.

        - cpu_avg: 모든 서버, 모든 인접 시점 LAG delta 평균
        - mem_avg: 모든 서버, 모든 시점 (1 - avail/total) 평균
        - disk_avg: mount별 평균 → 서버별 max → 서버 간 평균
        - sample_size: 기간 내 metric 발행 서버 distinct count
        partition pruning 의무 (C5). period_days <= 30 cap (DB scan 보호).
        """
        capped = max(1, min(period_days, 30))
        sql = text(f"""
            WITH cpu_series AS (
                SELECT server_id, collected_at,
                       (COALESCE(cpu_user,0)+COALESCE(cpu_nice,0)+COALESCE(cpu_system,0)
                        +COALESCE(cpu_iowait,0)+COALESCE(cpu_irq,0)+COALESCE(cpu_softirq,0)
                        +COALESCE(cpu_steal,0)) AS busy,
                       (COALESCE(cpu_user,0)+COALESCE(cpu_nice,0)+COALESCE(cpu_system,0)
                        +COALESCE(cpu_iowait,0)+COALESCE(cpu_irq,0)+COALESCE(cpu_softirq,0)
                        +COALESCE(cpu_steal,0)+COALESCE(cpu_idle,0)) AS total
                FROM server_metrics
                WHERE collected_at >= now() - (:days * interval '1 day')
            ),
            cpu_pairs AS (
                SELECT server_id,
                       busy, total,
                       LAG(busy)  OVER (PARTITION BY server_id ORDER BY collected_at) AS prev_busy,
                       LAG(total) OVER (PARTITION BY server_id ORDER BY collected_at) AS prev_total
                FROM cpu_series
            ),
            cpu_pct AS (
                SELECT server_id,
                       CASE WHEN total > prev_total AND (total - prev_total) > 0
                            THEN ((busy - prev_busy)::float / (total - prev_total)) * 100
                       END AS pct
                FROM cpu_pairs
                WHERE prev_total IS NOT NULL
            ),
            mem_pct AS (
                SELECT server_id,
                       CASE WHEN mem_total_kb > 0 AND mem_available_kb IS NOT NULL
                            THEN (1 - mem_available_kb::float / mem_total_kb) * 100
                       END AS pct
                FROM server_metrics
                WHERE collected_at >= now() - (:days * interval '1 day')
            ),
            disk_per_mount AS (
                -- mount별 평균 사용률. 가상 mount 제외 — 단일 진실 _VIRTUAL_MOUNT_SQL_FILTER.
                SELECT server_id, mount,
                       AVG(CASE WHEN total_bytes > 0 AND avail_bytes IS NOT NULL
                                THEN ((total_bytes - avail_bytes)::float / total_bytes) * 100
                           END) AS avg_pct
                FROM server_mount_usage
                WHERE collected_at >= now() - (:days * interval '1 day')
                  AND {_VIRTUAL_MOUNT_SQL_FILTER}
                GROUP BY server_id, mount
            ),
            disk_max AS (
                SELECT server_id, MAX(avg_pct) AS pct
                FROM disk_per_mount
                WHERE avg_pct IS NOT NULL
                GROUP BY server_id
            )
            SELECT
                (SELECT AVG(pct) FROM cpu_pct  WHERE pct IS NOT NULL) AS cpu_avg,
                (SELECT AVG(pct) FROM mem_pct  WHERE pct IS NOT NULL) AS mem_avg,
                (SELECT AVG(pct) FROM disk_max WHERE pct IS NOT NULL) AS disk_avg,
                (SELECT COUNT(DISTINCT server_id) FROM server_metrics
                 WHERE collected_at >= now() - (:days * interval '1 day')) AS sample_size
        """)
        result = await self.session.execute(sql, {"days": capped})
        row = result.one()
        return EnvironmentUtilizationRaw(
            cpu_avg_pct=float(row.cpu_avg) if row.cpu_avg is not None else None,
            mem_avg_pct=float(row.mem_avg) if row.mem_avg is not None else None,
            disk_avg_pct=float(row.disk_avg) if row.disk_avg is not None else None,
            sample_size=int(row.sample_size or 0),
        )