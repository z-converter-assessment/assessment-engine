"""Metric chart 도메인 concrete — dashboard snapshot · 시계열 cursor · 차트 dispatch · reboot marker."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from assessment_engine.db.dtos.outbound import (
    DashboardRaw,
    DiskIoRaw,
    MetricPairRaw,
    MetricSeries,
    MountUsageRaw,
    NetIoRaw,
    RebootEvent,
)
from assessment_engine.db.models.server_disk_io import ServerDiskIo
from assessment_engine.db.models.server_inventory import ServerInventory
from assessment_engine.db.models.server_metrics import ServerMetrics
from assessment_engine.db.models.server_mount_usage import ServerMountUsage
from assessment_engine.db.models.server_net_io import ServerNetIo
from assessment_engine.db.repositories.query._base import _BaseQueryMixin
from assessment_engine.db.repositories.query.base_metric import BaseMetricQueryRepository
from assessment_engine.db.repositories.query.types import (
    _AGG,
    _BUCKET_INFO,
    _CPU_NUMERATOR,
    _CPU_TOTAL_EXPR,
    _DATA_VOLUME_SQL_FILTER,
    _ENV_SCALAR_WEIGHTED,
    _PHYS_DISK_SQL_FILTER,
    _RATE_PER_DIM_DEFS,
    _VIRTUAL_IFACE_SQL_FILTER,
    BOOT_JITTER_SEC,
    TIME_RANGE_TD,
    AggFunc,
    BucketSize,
    MetricType,
    TimeRange,
)

# table 매핑 — types.py 가 ORM import 안 하므로 여기서 __tablename__ 결합.
_RATE_PER_DIM: dict[str, tuple[str, str, str]] = {
    "disk.read_iops": (
        ServerDiskIo.__tablename__,
        _RATE_PER_DIM_DEFS["disk.read_iops"][0],
        _RATE_PER_DIM_DEFS["disk.read_iops"][1],
    ),
    "disk.write_iops": (
        ServerDiskIo.__tablename__,
        _RATE_PER_DIM_DEFS["disk.write_iops"][0],
        _RATE_PER_DIM_DEFS["disk.write_iops"][1],
    ),
    "disk.read_kbps": (
        ServerDiskIo.__tablename__,
        _RATE_PER_DIM_DEFS["disk.read_kbps"][0],
        _RATE_PER_DIM_DEFS["disk.read_kbps"][1],
    ),
    "disk.write_kbps": (
        ServerDiskIo.__tablename__,
        _RATE_PER_DIM_DEFS["disk.write_kbps"][0],
        _RATE_PER_DIM_DEFS["disk.write_kbps"][1],
    ),
    "net.rx_bytes_per_sec": (
        ServerNetIo.__tablename__,
        _RATE_PER_DIM_DEFS["net.rx_bytes_per_sec"][0],
        _RATE_PER_DIM_DEFS["net.rx_bytes_per_sec"][1],
    ),
    "net.tx_bytes_per_sec": (
        ServerNetIo.__tablename__,
        _RATE_PER_DIM_DEFS["net.tx_bytes_per_sec"][0],
        _RATE_PER_DIM_DEFS["net.tx_bytes_per_sec"][1],
    ),
    "net.rx_packets_per_sec": (
        ServerNetIo.__tablename__,
        _RATE_PER_DIM_DEFS["net.rx_packets_per_sec"][0],
        _RATE_PER_DIM_DEFS["net.rx_packets_per_sec"][1],
    ),
    "net.tx_packets_per_sec": (
        ServerNetIo.__tablename__,
        _RATE_PER_DIM_DEFS["net.tx_packets_per_sec"][0],
        _RATE_PER_DIM_DEFS["net.tx_packets_per_sec"][1],
    ),
}


class MetricQueryRepository(_BaseQueryMixin, BaseMetricQueryRepository):
    # cursor pagination 윈도우 — C5 partition pruning 하한. cursor 마다 cursor-30d 동적.
    _METRIC_SNAPSHOTS_WINDOW = timedelta(days=30)

    async def latest_dashboard(self, server_id: int) -> DashboardRaw | None:
        exists = await self.session.execute(select(ServerInventory.id).where(ServerInventory.id == server_id))
        if not exists.scalar_one_or_none():
            return None

        # 미래 timestamp 방어 — 시계 어긋난 agent 의 미래 collected_at 행이 "가짜 최신"으로 잡혀
        # CPU delta(연속 2행)를 깨뜨리는 것 차단 (now()+skew 상한, _base._FUTURE_SKEW_SQL 와 동일 정책).
        m_result = await self.session.execute(
            select(ServerMetrics)
            .where(
                ServerMetrics.server_id == server_id,
                ServerMetrics.collected_at <= text("now() + interval '2 minutes'"),
            )
            .order_by(ServerMetrics.collected_at.desc())
            .limit(2)
        )
        metrics = [
            MetricPairRaw(
                collected_at=m.collected_at,
                cpu_user=m.cpu_user,
                cpu_nice=m.cpu_nice,
                cpu_system=m.cpu_system,
                cpu_idle=m.cpu_idle,
                cpu_iowait=m.cpu_iowait,
                cpu_irq=m.cpu_irq,
                cpu_softirq=m.cpu_softirq,
                cpu_steal=m.cpu_steal,
                mem_total_kb=m.mem_total_kb,
                mem_free_kb=m.mem_free_kb,
                mem_available_kb=m.mem_available_kb,
                mem_buffers_kb=m.mem_buffers_kb,
                mem_cached_kb=m.mem_cached_kb,
                swap_total_kb=m.swap_total_kb,
                swap_free_kb=m.swap_free_kb,
                load_1m=m.load_1m,
                load_5m=m.load_5m,
                load_15m=m.load_15m,
                boot_time=m.boot_time,
                agent_started_at=m.agent_started_at,
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

    async def metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeries]:
        upper = cursor if cursor else datetime.now(UTC)
        lower = upper - self._METRIC_SNAPSHOTS_WINDOW
        stmt = (
            select(ServerMetrics.collected_at)
            .where(
                ServerMetrics.server_id == server_id,
                ServerMetrics.collected_at >= lower,
            )
            .order_by(ServerMetrics.collected_at.desc())
            .limit(limit)
        )
        if cursor:
            stmt = stmt.where(ServerMetrics.collected_at < cursor)
        result = await self.session.execute(stmt)
        return [MetricSeries(collected_at=ts, value=None, dimension=None) for ts in result.scalars().all()]

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
        # 서버 상세 차트 = metric_trend(collapse=False, server_ids=[1대]) 위임 — 합산 대상이 1서버뿐이라
        # 시점값=그 서버값 -> 환경/선택과 동일 산식, dimension 보존.
        end_dt = end or datetime.now(UTC)
        start = end_dt - TIME_RANGE_TD[time_range]
        bi, bucket_td = _BUCKET_INFO[bucket]
        return await self.metric_trend(
            metric_type,
            start,
            end_dt,
            bi,
            bucket_td,
            server_ids=[server_id],
            agg=agg,
            dimension=dimension,
            collapse=False,
        )

    async def metric_trend(
        self,
        metric_type: str,
        start: datetime,
        end: datetime,
        bi: str,
        bucket_td: timedelta,
        server_ids: list[int] | None = None,
        agg: str = "avg",
        dimension: str | None = None,
        collapse: bool = True,
    ) -> list[MetricSeries]:
        """통일 시계열 — 각 collected_at 마다 환경값 1개 -> time_bucket agg(avg/max/p95).

        시점값 = 활용률 sum(num)/sum(den), 처리량 sum(rate), 로드 sum(load_15m)/sum(cpu_cores).
        그 시점 데이터 있는 서버만 포함(데이터 유무가 곧 온라인 필터). server_ids=None 전체·[1대]=서버 상세·[N]=선택.
        collapse=False 면 device/iface/mount dimension 보존(멀티라인), True 면 합산 단일선(환경).
        """
        ae = _AGG[agg]
        sid = "AND server_id = ANY(:server_ids)" if server_ids else ""
        params: dict = {"start": start, "end": end}
        _load_cols = {"load.1m": "load_1m", "load.5m": "load_5m", "load.15m": "load_15m"}

        if metric_type in _CPU_NUMERATOR:
            num = _CPU_NUMERATOR[metric_type]
            sql = text(f"""
                WITH raw AS (
                    SELECT collected_at, server_id, boot_time,
                        {num} AS num_j, {_CPU_TOTAL_EXPR} AS total_j
                    FROM {ServerMetrics.__tablename__}
                    WHERE collected_at >= :window_start AND collected_at <= :end {sid}
                ),
                deltas AS (
                    SELECT collected_at, boot_time,
                        LAG(boot_time) OVER w AS prev_boot,
                        num_j   - LAG(num_j)   OVER w AS d_num,
                        total_j - LAG(total_j) OVER w AS d_total
                    FROM raw WINDOW w AS (PARTITION BY server_id ORDER BY collected_at)
                ),
                valid AS (
                    SELECT collected_at, d_num, d_total FROM deltas
                    WHERE collected_at >= :start AND d_total > 0 AND d_num >= 0
                      AND (boot_time IS NULL OR prev_boot IS NULL
                           OR ABS(EXTRACT(EPOCH FROM (boot_time - prev_boot))) <= :jitter_sec)
                ),
                per_ts AS (
                    SELECT collected_at, SUM(d_num) * 100.0 / SUM(d_total) AS v
                    FROM valid GROUP BY collected_at HAVING SUM(d_total) > 0
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension
                FROM per_ts GROUP BY ts ORDER BY ts
            """)
            params["window_start"] = start - bucket_td
            params["jitter_sec"] = BOOT_JITTER_SEC
        elif metric_type in _ENV_SCALAR_WEIGHTED:
            num, den = _ENV_SCALAR_WEIGHTED[metric_type]
            # swap_total=0(swap 미설정 VM)도 0-line 표시 — den=0 행 제외 시 swapless 서버
            # 차트가 row 누락 → empty 표시(운영자 혼란)라 COALESCE 로 0% 환산.
            # mem 은 mem_total>0 이라 영향 없음 (NULLIF 가드만 작동).
            sql = text(f"""
                WITH per_ts AS (
                    SELECT collected_at,
                        COALESCE(SUM({num})::float / NULLIF(SUM({den}), 0) * 100, 0) AS v
                    FROM {ServerMetrics.__tablename__}
                    WHERE collected_at >= :start AND collected_at <= :end {sid}
                    GROUP BY collected_at
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension
                FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
            """)
        elif metric_type in _load_cols:
            load_col = _load_cols[metric_type]
            sid_sm = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
            sql = text(f"""
                WITH per_ts AS (
                    SELECT sm.collected_at, SUM(sm.{load_col}) / NULLIF(SUM(si.cpu_cores), 0) AS v
                    FROM {ServerMetrics.__tablename__} sm
                    JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
                    WHERE sm.collected_at >= :start AND sm.collected_at <= :end {sid_sm}
                      AND sm.{load_col} IS NOT NULL AND si.cpu_cores > 0
                    GROUP BY sm.collected_at
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension
                FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
            """)
        elif metric_type in ("disk.usage_percent", "fs.usage_percent"):
            if collapse:
                sql = text(f"""
                    WITH per_ts AS (
                        SELECT collected_at,
                            SUM(total_bytes - avail_bytes)::float / NULLIF(SUM(total_bytes), 0) * 100 AS v
                        FROM {ServerMountUsage.__tablename__}
                        WHERE collected_at >= :start AND collected_at <= :end {sid}
                          AND {_DATA_VOLUME_SQL_FILTER}
                          AND total_bytes > 0 AND avail_bytes IS NOT NULL
                        GROUP BY collected_at
                    )
                    SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension
                    FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
                """)
            else:
                sql = text(f"""
                    WITH per_ts AS (
                        SELECT collected_at, mount AS dim,
                            SUM(total_bytes - avail_bytes)::float / NULLIF(SUM(total_bytes), 0) * 100 AS v
                        FROM {ServerMountUsage.__tablename__}
                        WHERE collected_at >= :start AND collected_at <= :end {sid}
                          AND total_bytes > 0 AND avail_bytes IS NOT NULL
                          AND (CAST(:dim_filter AS text) IS NULL OR mount = :dim_filter)
                        GROUP BY collected_at, mount
                    )
                    SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, dim AS dimension
                    FROM per_ts WHERE v IS NOT NULL GROUP BY ts, dim ORDER BY ts, dim
                """)
                params["dim_filter"] = dimension
        elif metric_type in _RATE_PER_DIM_DEFS:
            table, dim_col, value_col = _RATE_PER_DIM[metric_type]
            if collapse:
                dev_filter = _PHYS_DISK_SQL_FILTER if dim_col == "device" else _VIRTUAL_IFACE_SQL_FILTER
                dim_sel, dim_grp, out_dim = "", "", "NULL::text"
            else:
                dev_filter = f"(CAST(:dim_filter AS text) IS NULL OR {dim_col} = :dim_filter)"
                dim_sel, dim_grp, out_dim = ", dim", ", dim", "dim"
                params["dim_filter"] = dimension
            sql = text(f"""
                WITH raw AS (
                    SELECT collected_at, server_id, boot_time, {dim_col} AS dim, {value_col} AS cnt
                    FROM {table}
                    WHERE collected_at >= :window_start AND collected_at <= :end {sid}
                      AND {dev_filter}
                ),
                deltas AS (
                    SELECT collected_at, dim, boot_time,
                        LAG(boot_time) OVER w AS prev_boot,
                        cnt - LAG(cnt) OVER w AS d_val,
                        EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
                    FROM raw WINDOW w AS (PARTITION BY server_id, dim ORDER BY collected_at)
                ),
                rates AS (
                    SELECT collected_at, dim,
                        CASE
                            WHEN dt IS NULL OR dt <= 0 THEN NULL
                            WHEN boot_time IS NOT NULL AND prev_boot IS NOT NULL
                                 AND ABS(EXTRACT(EPOCH FROM (boot_time - prev_boot))) > :jitter_sec THEN NULL
                            WHEN d_val IS NULL OR d_val < 0 THEN NULL
                            ELSE d_val / dt
                        END AS v
                    FROM deltas WHERE collected_at >= :start
                ),
                per_ts AS (
                    SELECT collected_at{dim_sel}, SUM(v) AS v
                    FROM rates WHERE v IS NOT NULL GROUP BY collected_at{dim_grp}
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, {out_dim} AS dimension
                FROM per_ts GROUP BY ts{dim_grp} ORDER BY ts{dim_grp}
            """)
            params["window_start"] = start - bucket_td
            params["jitter_sec"] = BOOT_JITTER_SEC
        else:
            raise AssertionError(f"unsupported metric_type {metric_type!r}")

        if server_ids:
            params["server_ids"] = server_ids
        result = await self.session.execute(sql, params)
        return [MetricSeries(collected_at=row.ts, value=row.value, dimension=row.dimension) for row in result.all()]

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
                  AND collected_at >= :buffer_start  -- C5 partition pruning + LAG 베이스 buffer
            )
            SELECT collected_at, boot_time, agent_started_at,
                CASE
                    WHEN prev_boot IS NULL                            THEN 'reboot'
                    WHEN ABS(EXTRACT(EPOCH FROM (boot_time - prev_boot))) > :jitter_sec THEN 'reboot'  -- ±지터 흡수
                    WHEN agent_started_at IS DISTINCT FROM prev_agent THEN 'restart'
                    ELSE NULL
                END AS kind
            FROM base
            WHERE collected_at >= :start
              AND (
                  prev_boot IS NULL
                  OR ABS(EXTRACT(EPOCH FROM (boot_time - prev_boot))) > :jitter_sec  -- ±지터 흡수
                  OR agent_started_at IS DISTINCT FROM prev_agent
              )
            ORDER BY collected_at
        """)
        result = await self.session.execute(
            sql,
            {
                "sid": server_id,
                "start": start,
                "end": end,
                # LAG 베이스 buffer — start 직전 30일 (그 안에 prev_boot 행 잡힘).
                "buffer_start": start - timedelta(days=30),
                "jitter_sec": BOOT_JITTER_SEC,
            },
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
