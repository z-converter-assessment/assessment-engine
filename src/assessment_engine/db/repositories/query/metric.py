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
    _PHYS_IFACE_SQL_FILTER,
    _RATE_PER_DIM_DEFS,
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
                cpu_run_queue=(m.procs_running if m.procs_running is not None else m.sat_cpu_run_queue),
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
                kind=row.kind,
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
                kind=row.kind,
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
                kind=row.kind,
            )
            for row in mu_rows
        ]

        return DashboardRaw(metrics=metrics, disk_io=disk_io, net_io=net_io, mounts=mounts)

    async def latest_saturation(self, server_ids: list[int], since: datetime) -> dict[int, dict]:
        """서버별 실시간 포화 원자료 — 자원 적정성 분류 4축: CPU 실행 큐(gauge) + 디스크(Windows queue gauge /
        Linux await 2행 델타) + 메모리(Windows paging rate gauge / Linux pswpout 델타) + 네트워크(TCP 재전송율 =
        tcp_retrans_segs 델타 / tx_packets 델타). 실시간 포화 지수·압박 카운트·서버 상세 스냅샷용 경량 쿼리.

        since 이후 최신 2행(delta 용) per server/device. collected_at >= since partition pruning(C5),
        now+2m skew 상한. 공유 dashboard 무손상. reset(값-감소) 은 delta<0 -> None/0 가드.
        """
        if not server_ids:
            return {}
        sql = text("""
            WITH m2 AS (
                SELECT server_id, procs_running, sat_cpu_run_queue, sat_disk_queue,
                       pswpout, tcp_retrans_segs, mem_pages_input, collected_at,
                       row_number() OVER (PARTITION BY server_id ORDER BY collected_at DESC) AS rn
                FROM server_metrics
                WHERE server_id = ANY(:sids) AND collected_at >= :since AND collected_at <= now() + interval '2 minutes'
            ),
            m AS (
                SELECT server_id,
                    max(CASE WHEN rn = 1 THEN COALESCE(procs_running, sat_cpu_run_queue) END) AS run_queue,
                    max(CASE WHEN rn = 1 THEN sat_disk_queue END)      AS disk_queue_win,
                    -- Windows 메모리 압박 = Pages Input/sec(하드폴트) rate = 델타/dt. 두 행 다 있어야 산출(sparse/reset->null).
                    CASE WHEN max(CASE WHEN rn = 1 THEN mem_pages_input END) >= max(CASE WHEN rn = 2 THEN mem_pages_input END)
                              AND max(CASE WHEN rn = 1 THEN collected_at END) > max(CASE WHEN rn = 2 THEN collected_at END)
                         THEN (max(CASE WHEN rn = 1 THEN mem_pages_input END) - max(CASE WHEN rn = 2 THEN mem_pages_input END))::float
                              / EXTRACT(EPOCH FROM (max(CASE WHEN rn = 1 THEN collected_at END)
                                                    - max(CASE WHEN rn = 2 THEN collected_at END)))
                    END AS pages_input_rate,
                    max(CASE WHEN rn = 1 THEN pswpout END) - max(CASE WHEN rn = 2 THEN pswpout END) AS pswpout_delta,
                    max(CASE WHEN rn = 1 THEN tcp_retrans_segs END)
                        - max(CASE WHEN rn = 2 THEN tcp_retrans_segs END) AS retrans_delta
                FROM m2 WHERE rn <= 2 GROUP BY server_id
            ),
            d2 AS (
                SELECT server_id, time_reading_ms + time_writing_ms AS t, reads_completed + writes_completed AS ops,
                       row_number() OVER (PARTITION BY server_id, device ORDER BY collected_at DESC) AS rn
                FROM server_disk_io
                WHERE server_id = ANY(:sids) AND collected_at >= :since
                  AND collected_at <= now() + interval '2 minutes' AND kind = 'physical'
            ),
            da AS (
                SELECT server_id,
                    SUM(CASE WHEN rn = 1 THEN t END)   - SUM(CASE WHEN rn = 2 THEN t END)   AS t_delta,
                    SUM(CASE WHEN rn = 1 THEN ops END) - SUM(CASE WHEN rn = 2 THEN ops END) AS ops_delta
                FROM d2 WHERE rn <= 2 GROUP BY server_id
            ),
            n2 AS (
                SELECT server_id, tx_packets,
                       row_number() OVER (PARTITION BY server_id, interface ORDER BY collected_at DESC) AS rn
                FROM server_net_io
                WHERE server_id = ANY(:sids) AND collected_at >= :since
                  AND collected_at <= now() + interval '2 minutes' AND kind IN ('physical', 'bond_master')
            ),
            nt AS (
                SELECT server_id,
                    SUM(CASE WHEN rn = 1 THEN tx_packets END) - SUM(CASE WHEN rn = 2 THEN tx_packets END) AS txp_delta
                FROM n2 WHERE rn <= 2 GROUP BY server_id
            )
            SELECT m.server_id, m.run_queue, m.disk_queue_win, m.pages_input_rate, m.pswpout_delta,
                   CASE WHEN da.ops_delta > 0 AND da.t_delta >= 0 THEN da.t_delta::float / da.ops_delta END AS await_ms,
                   CASE WHEN nt.txp_delta > 0 AND m.retrans_delta >= 0
                        THEN m.retrans_delta::float / nt.txp_delta * 100 END AS retrans_pct
            FROM m LEFT JOIN da ON da.server_id = m.server_id LEFT JOIN nt ON nt.server_id = m.server_id
        """)
        result = await self.session.execute(sql, {"sids": server_ids, "since": since})
        return {
            r.server_id: {
                "run_queue": float(r.run_queue) if r.run_queue is not None else None,
                "disk_queue_win": float(r.disk_queue_win) if r.disk_queue_win is not None else None,
                "await_ms": float(r.await_ms) if r.await_ms is not None else None,
                "pages_input_rate": float(r.pages_input_rate) if r.pages_input_rate is not None else None,
                "pswpout_delta": int(r.pswpout_delta) if r.pswpout_delta is not None else None,
                "retrans_pct": float(r.retrans_pct) if r.retrans_pct is not None else None,
            }
            for r in result
        }

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
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_ts GROUP BY ts ORDER BY ts
            """)
            params["window_start"] = start - bucket_td
            params["jitter_sec"] = BOOT_JITTER_SEC
        elif metric_type == "swap.usage_percent" and collapse:
            # env: Linux swap(오버플로 — 평소 0, 압박 시 상승)와 Windows pagefile(상시 baseline, 항상 사용)은
            # 의미가 달라 os_family 로 분리한다 — 한 선 capacity-weighted 평균은 pagefile baseline 이 함대 스왑처럼
            # 오도(예: Windows 44% + Linux 1.3%). 상세(collapse=False, 1대)는 아래 _ENV_SCALAR_WEIGHTED 단일선 유지.
            sid_sm = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
            sql = text(f"""
                WITH per_ts AS (
                    SELECT sm.collected_at, si.os_family AS dim,
                        SUM(sm.swap_total_kb - sm.swap_free_kb)::float
                          / NULLIF(SUM(sm.swap_total_kb), 0) * 100 AS v
                    FROM {ServerMetrics.__tablename__} sm
                    JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
                    WHERE sm.collected_at >= :start AND sm.collected_at <= :end {sid_sm}
                      AND sm.swap_total_kb IS NOT NULL AND sm.swap_free_kb IS NOT NULL AND sm.swap_total_kb > 0
                    GROUP BY sm.collected_at, si.os_family
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value,
                    dim AS dimension, NULL::text AS kind
                FROM per_ts WHERE v IS NOT NULL GROUP BY ts, dim ORDER BY ts
            """)
        elif metric_type in _ENV_SCALAR_WEIGHTED:
            num, den, guard = _ENV_SCALAR_WEIGHTED[metric_type]
            ratio = f"SUM({num})::float / NULLIF(SUM({den}), 0) * 100"
            # swap 만 den=0(swap 미설정 VM)을 0% line 으로 표시 — den=0 행 제외 시 swapless 서버 차트가
            # row 누락 → empty(운영자 혼란). mem 성분 null(Windows cached/buffers 미측정)은 COALESCE-to-0 로
            # 삼키지 않고 guard(IS NOT NULL)로 제외 → gap 표시(측정 0 vs 미측정 구분, cpu iowait 분기와 대칭, #C2·A1).
            v_expr = f"COALESCE({ratio}, 0)" if metric_type == "swap.usage_percent" else ratio
            sql = text(f"""
                WITH per_ts AS (
                    SELECT collected_at, {v_expr} AS v
                    FROM {ServerMetrics.__tablename__}
                    WHERE collected_at >= :start AND collected_at <= :end {sid} AND {guard}
                    GROUP BY collected_at
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
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
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
            """)
        elif metric_type == "cpu.run_queue":
            # 실행 큐/코어 os-aware — Linux procs_running(R-state) / Windows Processor Queue Length(sat_cpu_run_queue).
            # 분류(cpu_saturated)와 동일 신호. 항상 os_family dimension 반환(Linux/Windows 2선; 서버 상세=단일 OS 1선).
            # capacity-weighted: SUM(run_queue)/SUM(cpu_cores) (load 정규화와 동일 산식). 앵커/윈도우는 :start/:end 계약.
            sid_sm = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
            sql = text(f"""
                WITH per_ts AS (
                    SELECT sm.collected_at, si.os_family AS dim,
                        SUM(COALESCE(sm.procs_running, sm.sat_cpu_run_queue))::float
                          / NULLIF(SUM(si.cpu_cores), 0) AS v
                    FROM {ServerMetrics.__tablename__} sm
                    JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
                    WHERE sm.collected_at >= :start AND sm.collected_at <= :end {sid_sm}
                      AND COALESCE(sm.procs_running, sm.sat_cpu_run_queue) IS NOT NULL AND si.cpu_cores > 0
                    GROUP BY sm.collected_at, si.os_family
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value,
                    dim AS dimension, NULL::text AS kind
                FROM per_ts WHERE v IS NOT NULL GROUP BY ts, dim ORDER BY ts
            """)
        elif metric_type == "disk.queue":
            # Windows Avg Disk Queue Length — server_metrics.sat_disk_queue (per-device max 축약 gauge).
            # Linux 는 iowait 사용이라 null 발행 -> 값 있는 서버(Windows)만 집계. 시점값 = 서버 평균 큐 깊이.
            sql = text(f"""
                WITH per_ts AS (
                    SELECT collected_at, AVG(sat_disk_queue) AS v
                    FROM {ServerMetrics.__tablename__}
                    WHERE collected_at >= :start AND collected_at <= :end {sid}
                      AND sat_disk_queue IS NOT NULL
                    GROUP BY collected_at
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
            """)
        elif metric_type == "disk.io_saturation":
            # 디스크 I/O 포화 os-aware — 정규화 없이 raw 2선(이중 Y축). dimension=os_family:
            #   linux  = await(ms) = Σ(Δ(time_reading+time_writing)) / Σ(Δ(reads+writes)) 물리 device 버킷 델타(CPU jiffies 방식,
            #            boot reset gate). Windows 는 await 미발행이라 linux 만.
            #   windows= Avg Disk Queue Length(sat_disk_queue gauge) 서버 평균. Linux 는 미발행이라 windows 만.
            # 단위가 달라(ms vs 큐 깊이) 정규화 대신 프론트가 os별 축 분리. 앵커/윈도우는 :start/:end 계약.
            sid_dio = "AND dio.server_id = ANY(:server_ids)" if server_ids else ""
            sid_sm = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
            sql = text(f"""
                WITH l_raw AS (
                    SELECT dio.collected_at, dio.server_id, dio.device, dio.boot_time,
                        (COALESCE(dio.time_reading_ms, 0) + COALESCE(dio.time_writing_ms, 0)) AS t,
                        (COALESCE(dio.reads_completed, 0) + COALESCE(dio.writes_completed, 0)) AS ops
                    FROM {ServerDiskIo.__tablename__} dio
                    JOIN {ServerInventory.__tablename__} si ON si.id = dio.server_id
                    WHERE si.os_family = 'linux' AND dio.kind = 'physical'
                      AND dio.collected_at >= :window_start AND dio.collected_at <= :end {sid_dio}
                ),
                l_delta AS (
                    SELECT collected_at, boot_time, LAG(boot_time) OVER w AS prev_boot,
                        t   - LAG(t)   OVER w AS d_t,
                        ops - LAG(ops) OVER w AS d_ops
                    FROM l_raw WINDOW w AS (PARTITION BY server_id, device ORDER BY collected_at)
                ),
                l_ts AS (
                    SELECT collected_at, SUM(d_t)::float / NULLIF(SUM(d_ops), 0) AS v
                    FROM l_delta
                    WHERE collected_at >= :start AND d_ops > 0 AND d_t >= 0
                      AND (boot_time IS NULL OR prev_boot IS NULL
                           OR ABS(EXTRACT(EPOCH FROM (boot_time - prev_boot))) <= :jitter_sec)
                    GROUP BY collected_at
                ),
                w_ts AS (
                    SELECT sm.collected_at, AVG(sm.sat_disk_queue) AS v
                    FROM {ServerMetrics.__tablename__} sm
                    WHERE sm.collected_at >= :start AND sm.collected_at <= :end {sid_sm}
                      AND sm.sat_disk_queue IS NOT NULL
                    GROUP BY sm.collected_at
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value,
                    'linux'::text AS dimension, NULL::text AS kind
                FROM l_ts WHERE v IS NOT NULL GROUP BY ts
                UNION ALL
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value,
                    'windows'::text AS dimension, NULL::text AS kind
                FROM w_ts WHERE v IS NOT NULL GROUP BY ts
                ORDER BY ts
            """)
            params["window_start"] = start - bucket_td
            params["jitter_sec"] = BOOT_JITTER_SEC
        elif metric_type == "net.retrans_percent":
            # TCP 재전송율 % = Σ(Δtcp_retrans) / Σ(Δtx_packets) * 100 — 분류 net_retrans 와 동일 산식(임계 1%).
            # retrans(server_metrics) + tx_packets(server_net_io physical+bond) 교차 테이블 버킷 델타, collected_at 조인.
            # counter reset(재부팅)은 GREATEST(Δ,0)로 흡수(음수 델타=0, boot gate 불요). 양 OS 동일 신호라 단일선.
            sid_sm = "AND server_id = ANY(:server_ids)" if server_ids else ""
            sql = text(f"""
                WITH retrans_ts AS (
                    SELECT collected_at, SUM(d) AS retrans FROM (
                        SELECT collected_at,
                            GREATEST(tcp_retrans_segs - LAG(tcp_retrans_segs)
                                     OVER (PARTITION BY server_id ORDER BY collected_at), 0) AS d
                        FROM {ServerMetrics.__tablename__}
                        WHERE collected_at >= :window_start AND collected_at <= :end {sid_sm}
                    ) x WHERE d IS NOT NULL GROUP BY collected_at
                ),
                txp_ts AS (
                    SELECT collected_at, SUM(d) AS txp FROM (
                        SELECT collected_at,
                            GREATEST(tx_packets - LAG(tx_packets)
                                     OVER (PARTITION BY server_id, interface ORDER BY collected_at), 0) AS d
                        FROM {ServerNetIo.__tablename__}
                        WHERE kind IN ('physical', 'bond_master')
                          AND collected_at >= :window_start AND collected_at <= :end {sid_sm}
                    ) y WHERE d IS NOT NULL GROUP BY collected_at
                ),
                per_ts AS (
                    SELECT r.collected_at, r.retrans::float / NULLIF(t.txp, 0) * 100 AS v
                    FROM retrans_ts r JOIN txp_ts t USING (collected_at)
                    WHERE r.collected_at >= :start
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value,
                    NULL::text AS dimension, NULL::text AS kind
                FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
            """)
            params["window_start"] = start - bucket_td
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
                    SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
                    FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
                """)
            else:
                sql = text(f"""
                    WITH per_ts AS (
                        SELECT collected_at, mount AS dim, MAX(kind) AS kind,
                            SUM(total_bytes - avail_bytes)::float / NULLIF(SUM(total_bytes), 0) * 100 AS v
                        FROM {ServerMountUsage.__tablename__}
                        WHERE collected_at >= :start AND collected_at <= :end {sid}
                          AND total_bytes > 0 AND avail_bytes IS NOT NULL
                          AND (CAST(:dim_filter AS text) IS NULL OR mount = :dim_filter)
                        GROUP BY collected_at, mount
                    )
                    SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, dim AS dimension, MAX(kind) AS kind
                    FROM per_ts WHERE v IS NOT NULL GROUP BY ts, dim ORDER BY ts, dim
                """)
                params["dim_filter"] = dimension
        elif metric_type in _RATE_PER_DIM_DEFS:
            table, dim_col, value_col = _RATE_PER_DIM[metric_type]
            if collapse:
                dev_filter = _PHYS_DISK_SQL_FILTER if dim_col == "device" else _PHYS_IFACE_SQL_FILTER
                dim_sel, dim_grp, out_dim, kind_sel, kind_out = "", "", "NULL::text", "", "NULL::text"
            else:
                dev_filter = f"(CAST(:dim_filter AS text) IS NULL OR {dim_col} = :dim_filter)"
                dim_sel, dim_grp, out_dim, kind_sel, kind_out = ", dim", ", dim", "dim", ", kind", "MAX(kind)"
                params["dim_filter"] = dimension
            sql = text(f"""
                WITH raw AS (
                    SELECT collected_at, server_id, boot_time, {dim_col} AS dim, {value_col} AS cnt{kind_sel}
                    FROM {table}
                    WHERE collected_at >= :window_start AND collected_at <= :end {sid}
                      AND {dev_filter}
                ),
                deltas AS (
                    SELECT collected_at, dim{kind_sel}, boot_time,
                        LAG(boot_time) OVER w AS prev_boot,
                        cnt - LAG(cnt) OVER w AS d_val,
                        EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
                    FROM raw WINDOW w AS (PARTITION BY server_id, dim ORDER BY collected_at)
                ),
                rates AS (
                    SELECT collected_at, dim{kind_sel},
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
                    SELECT collected_at{dim_sel}{kind_sel}, SUM(v) AS v
                    FROM rates WHERE v IS NOT NULL GROUP BY collected_at{dim_grp}{kind_sel}
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, {out_dim} AS dimension, {kind_out} AS kind
                FROM per_ts GROUP BY ts{dim_grp} ORDER BY ts{dim_grp}
            """)
            params["window_start"] = start - bucket_td
            params["jitter_sec"] = BOOT_JITTER_SEC
        else:
            raise AssertionError(f"unsupported metric_type {metric_type!r}")

        if server_ids:
            params["server_ids"] = server_ids
        result = await self.session.execute(sql, params)
        return [
            MetricSeries(collected_at=row.ts, value=row.value, dimension=row.dimension, kind=row.kind)
            for row in result.all()
        ]

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
