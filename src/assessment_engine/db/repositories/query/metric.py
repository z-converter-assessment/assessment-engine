"""Metric chart 도메인 concrete — dashboard snapshot · 시계열 cursor · 차트 dispatch · reboot marker."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from assessment_engine.boot_time import BOOT_TIME_JITTER_TOLERANCE
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
    _ENV_SCALAR_WEIGHTED,
    _PHYS_DISK_SQL_FILTER,
    _RATE_PER_DIM_DEFS,
    _SCALAR_VALUE_EXPR,
    _VIRTUAL_IFACE_SQL_FILTER,
    _VIRTUAL_MOUNT_SQL_FILTER,
    TIME_RANGE_TD,
    AggFunc,
    BucketSize,
    MetricType,
    TimeRange,
)

# boot_time 지터 허용치(초) — boot_time.BOOT_TIME_JITTER_TOLERANCE 단일 진실에서 파생. SQL bound param 으로 주입.
_BOOT_JITTER_SEC = int(BOOT_TIME_JITTER_TOLERANCE.total_seconds())

# table 매핑 — types.py 가 ORM import 안 하므로 본 모듈에서 ORM __tablename__ 결합.
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
    # 시계열 cursor pagination 윈도우 — partition pruning 의무 하한 (#C5).
    # 30일이면 backward scroll N 페이지 안정적으로 커버 (cursor 마다 cursor - 30d 동적).
    _METRIC_SNAPSHOTS_WINDOW = timedelta(days=30)

    async def latest_dashboard(self, server_id: int) -> DashboardRaw | None:
        exists = await self.session.execute(select(ServerInventory.id).where(ServerInventory.id == server_id))
        if not exists.scalar_one_or_none():
            return None

        # server_metrics는 dimension 없음. 단순 최신 2행.
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
        # cursor 가 있으면 그 시점부터 30일 뒤로, 없으면 현재로부터 30일.
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
        end_dt = end or datetime.now(UTC)
        start = end_dt - TIME_RANGE_TD[time_range]
        bi, bucket_td = _BUCKET_INFO[bucket]
        ae = _AGG[agg]

        # router Literal(MetricType)로 metric_type가 whitelist 되어 있어 dispatch는 단순 lookup.
        if metric_type in _CPU_NUMERATOR:
            return await self._chart_cpu_delta(
                server_id,
                start,
                end_dt,
                bi,
                ae,
                bucket_td,
                _CPU_NUMERATOR[metric_type],
            )
        if metric_type in _SCALAR_VALUE_EXPR:
            return await self._chart_scalar(
                server_id,
                start,
                end_dt,
                bi,
                ae,
                _SCALAR_VALUE_EXPR[metric_type],
            )
        if metric_type in _RATE_PER_DIM:
            table, dim_col, value_col = _RATE_PER_DIM[metric_type]
            return await self._chart_rate_per_dimension(
                server_id,
                start,
                end_dt,
                bi,
                ae,
                bucket_td,
                table,
                dim_col,
                value_col,
                dimension,
            )
        if metric_type == "fs.usage_percent":
            return await self._chart_fs(server_id, start, end_dt, bi, ae, dimension)

        raise AssertionError(f"unreachable: unknown metric_type {metric_type!r}")

    async def environment_metric_trend(
        self,
        metric_type: str,
        start: datetime,
        end: datetime,
        bi: str,
        bucket_td: timedelta,
        server_ids: list[int] | None = None,
    ) -> list[MetricSeries]:
        """환경 시점별 capacity-weighted 시계열 — 대시보드·환경 보고서·선택 보고서 추이 차트 공용.

        capacity-weighted (자원 총량 가중): 버킷 안 전 서버·전 시점 Σused/Σtotal — 환경 평균 활용률
        카드(environment_utilization)와 동일 가중 (카드는 윈도우 전체 1값, 본 함수는 버킷별 시계열).
        서버 동등가중(서버별 라인 평균) 아님 — 거대 VM 이 큰 비중.
        cpu.usage_percent: 버킷별 Σd_num/Σd_total (서버별 LAG delta, boot reset 제외 _chart_cpu_delta 동일).
        mem.usage_percent: 버킷별 Σ(mem_total_kb - mem_available_kb)/Σ mem_total_kb.
        disk.usage_percent: 버킷별 Σ(total_bytes - avail_bytes)/Σ total_bytes (가상 mount 제외).
        server_ids: None 이면 전체 환경, 주어지면 그 N대 한정 (selection 보고서).
        """
        # selection N대 한정 술어 (None 이면 전체).
        sid = "AND server_id = ANY(:server_ids)" if server_ids else ""
        if metric_type in _CPU_NUMERATOR:
            num = _CPU_NUMERATOR[metric_type]
            sql = text(f"""
                WITH raw AS (
                    SELECT collected_at, server_id, boot_time,
                        {num}               AS num_j,
                        {_CPU_TOTAL_EXPR}   AS total_j
                    FROM {ServerMetrics.__tablename__}
                    WHERE collected_at >= :window_start AND collected_at <= :end {sid}
                ),
                deltas AS (
                    SELECT collected_at, boot_time,
                        LAG(boot_time) OVER (PARTITION BY server_id ORDER BY collected_at) AS prev_boot,
                        num_j   - LAG(num_j)   OVER (PARTITION BY server_id ORDER BY collected_at) AS d_num,
                        total_j - LAG(total_j) OVER (PARTITION BY server_id ORDER BY collected_at) AS d_total
                    FROM raw
                ),
                valid AS (
                    -- 유효 delta = d_total>0 AND d_num>=0 AND boot reset 아님 (시점값 아닌 raw delta 보존).
                    SELECT collected_at, d_num, d_total
                    FROM deltas
                    WHERE collected_at >= :start
                      AND d_total IS NOT NULL AND d_total > 0 AND d_num >= 0
                      AND (boot_time IS NULL OR prev_boot IS NULL
                           OR ABS(EXTRACT(EPOCH FROM (boot_time - prev_boot))) <= :jitter_sec)
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                    CASE WHEN SUM(d_total) > 0 THEN SUM(d_num) * 100.0 / SUM(d_total) END AS value,
                    NULL::text AS dimension
                FROM valid
                GROUP BY ts ORDER BY ts
            """)
            params = {
                "start": start,
                "end": end,
                "window_start": start - bucket_td,
                "jitter_sec": _BOOT_JITTER_SEC,
            }
        elif metric_type in ("disk.usage_percent", "fs.usage_percent"):
            # 디스크/파일시스템 = 버킷별 전 mount Σ(total-avail)/Σtotal (가상 제외) — capacity-weighted.
            sql = text(f"""
                SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                    CASE WHEN SUM(total_bytes) > 0
                         THEN SUM(total_bytes - avail_bytes)::float / SUM(total_bytes) * 100 END AS value,
                    NULL::text AS dimension
                FROM server_mount_usage
                WHERE collected_at >= :start AND collected_at <= :end
                  AND {_VIRTUAL_MOUNT_SQL_FILTER} {sid}
                  AND total_bytes > 0 AND avail_bytes IS NOT NULL
                GROUP BY ts ORDER BY ts
            """)
            params = {"start": start, "end": end}
        elif metric_type in _ENV_SCALAR_WEIGHTED:
            # 메모리 구성·스왑 = 버킷별 Σnumerator/Σdenominator — capacity-weighted (큰 서버 큰 비중).
            num, den = _ENV_SCALAR_WEIGHTED[metric_type]
            sql = text(f"""
                SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                    CASE WHEN SUM({den}) > 0 THEN SUM({num})::float / SUM({den}) * 100 END AS value,
                    NULL::text AS dimension
                FROM {ServerMetrics.__tablename__}
                WHERE collected_at >= :start AND collected_at <= :end {sid}
                  AND {den} > 0
                GROUP BY ts ORDER BY ts
            """)
            params = {"start": start, "end": end}
        elif metric_type == "load.15m":
            # 코어당 로드 = 버킷별 Σload_15m / Σcpu_cores (capacity-weighted, 코어 정규화).
            # 절대 load 동등평균은 코어 수 다른 서버 혼재 시 왜곡(코어 많은 거대 서버의 큰 load 가 평균을 끌어올림) —
            # 코어 정규화로 "코어당 run queue"(1.0 = 포화) 의미 통일.
            # cpu_cores 는 server_inventory JOIN (server_metrics 미보유).
            sid_sm = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
            sql = text(f"""
                SELECT time_bucket(interval '{bi}', sm.collected_at) AS ts,
                    CASE WHEN SUM(si.cpu_cores) > 0 THEN SUM(sm.load_15m) / SUM(si.cpu_cores) END AS value,
                    NULL::text AS dimension
                FROM {ServerMetrics.__tablename__} sm
                JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
                WHERE sm.collected_at >= :start AND sm.collected_at <= :end {sid_sm}
                  AND sm.load_15m IS NOT NULL AND si.cpu_cores > 0
                GROUP BY ts ORDER BY ts
            """)
            params = {"start": start, "end": end}
        elif metric_type in _RATE_PER_DIM_DEFS:
            # 누적 카운터 rate 합산 = 시점별 전 서버·전 device(물리/비가상) rate 합 -> 버킷 avg.
            # boot reset·dt<=0·음수 delta 가드는 _chart_rate_per_dimension 동일 정책 (#C1). disk=물리만, net=비가상만.
            table, dim_col, value_col = _RATE_PER_DIM[metric_type]
            dev_filter = _PHYS_DISK_SQL_FILTER if dim_col == "device" else _VIRTUAL_IFACE_SQL_FILTER
            sql = text(f"""
                WITH raw AS (
                    SELECT collected_at, server_id, boot_time, {dim_col} AS dim, {value_col} AS cnt
                    FROM {table}
                    WHERE collected_at >= :window_start AND collected_at <= :end {sid}
                      AND {dev_filter}
                ),
                deltas AS (
                    SELECT collected_at, boot_time,
                        LAG(boot_time) OVER w AS prev_boot,
                        cnt - LAG(cnt) OVER w AS d_val,
                        EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
                    FROM raw
                    WINDOW w AS (PARTITION BY server_id, dim ORDER BY collected_at)
                ),
                rates AS (
                    SELECT collected_at,
                        CASE
                            WHEN dt IS NULL OR dt <= 0 THEN NULL
                            WHEN boot_time IS NOT NULL AND prev_boot IS NOT NULL
                                 AND ABS(EXTRACT(EPOCH FROM (boot_time - prev_boot))) > :jitter_sec THEN NULL
                            WHEN d_val IS NULL OR d_val < 0 THEN NULL
                            ELSE d_val / dt
                        END AS v
                    FROM deltas
                    WHERE collected_at >= :start
                ),
                per_ts AS (
                    SELECT collected_at, SUM(v) AS env_rate
                    FROM rates WHERE v IS NOT NULL
                    GROUP BY collected_at
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                    avg(env_rate) AS value,
                    NULL::text AS dimension
                FROM per_ts
                GROUP BY ts ORDER BY ts
            """)
            params = {
                "start": start,
                "end": end,
                "window_start": start - bucket_td,
                "jitter_sec": _BOOT_JITTER_SEC,
            }
        else:
            raise AssertionError(f"unsupported environment metric_type {metric_type!r}")
        if server_ids:
            params["server_ids"] = server_ids
        result = await self.session.execute(sql, params)
        return [MetricSeries(collected_at=row.ts, value=row.value, dimension=row.dimension) for row in result.all()]

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

        reset 식별 (calculator와 동일 정책 — CLAUDE.md #C1):
        - boot_time 차이 > 5초 → 시스템 재부팅 → NULL (±1초 측정 지터 흡수, 단순 음수가 아닌 진짜 reset)
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
                           WHEN boot_time IS NOT NULL AND prev_boot IS NOT NULL
                                AND ABS(EXTRACT(EPOCH FROM (boot_time - prev_boot))) > :jitter_sec THEN NULL
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
            {
                "sid": server_id,
                "start": start,
                "end": end,
                "window_start": start - bucket_td,
                "jitter_sec": _BOOT_JITTER_SEC,
            },
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

        reset 식별 우선순위 (calculator와 동일 정책 — CLAUDE.md #C1):
        ① dt 검증: dt <= 0 (동일 시점·역행) → NULL. dt 자체는 분모일 뿐 1분/3분 무관 — 실제 시간으로 자연 정규화.
        ② boot_time 검증: boot_time 차이 > 5초 → 시스템 재부팅 → NULL (±1초 측정 지터 흡수, reset 확정).
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
                    EXTRACT(EPOCH FROM (collected_at
                        - LAG(collected_at) OVER (PARTITION BY dim ORDER BY collected_at))) AS dt
                FROM raw
            )
            SELECT time_bucket(interval '{bi}', collected_at) AS ts,
                   {ae}                                        AS value,
                   dim                                         AS dimension
            FROM (
                SELECT collected_at, dim,
                       CASE
                           WHEN dt IS NULL OR dt <= 0 THEN NULL
                           WHEN boot_time IS NOT NULL AND prev_boot IS NOT NULL
                                AND ABS(EXTRACT(EPOCH FROM (boot_time - prev_boot))) > :jitter_sec THEN NULL
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
                "jitter_sec": _BOOT_JITTER_SEC,
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
                "jitter_sec": _BOOT_JITTER_SEC,
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
