"""Metric chart 도메인 concrete — dashboard snapshot · 시계열 cursor · 차트 dispatch · reboot marker.

단위 s/By, device_id/iface_id/mountpoint 안정키. child 시계열(disk_io/net_io)은 boot_time 미보유 ->
rate/CPU reset 은 boot gate 없이 GREATEST(delta,0)/d_total>0 로 흡수.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, get_args

from sqlalchemy import select, text
from sqlalchemy.sql.elements import TextClause

from assessment_engine.db.dtos.outbound import (
    CpuCoreRaw,
    DashboardRaw,
    DiskIoRaw,
    ErrorFleetRaw,
    FleetErrorRaw,
    MetricPairRaw,
    MetricSeries,
    MountUsageRaw,
    NetIoRaw,
    RebootEvent,
    SaturationRaw,
)
from assessment_engine.db.models.server_cpu_core import ServerCpuCore
from assessment_engine.db.models.server_disk_io import ServerDiskIo
from assessment_engine.db.models.server_filesystem import ServerFilesystem
from assessment_engine.db.models.server_inventory import ServerInventory
from assessment_engine.db.models.server_metrics import ServerMetrics
from assessment_engine.db.models.server_net_io import ServerNetIo
from assessment_engine.db.repositories.query._base import _BaseQueryMixin
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
    EnvironmentMetricType,
    MetricType,
    TimeRange,
)
from assessment_engine.domain import right_sizing

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject


_RATE_PER_DIM: dict[str, tuple[str, str, str]] = {
    k: (
        (ServerDiskIo if k.startswith("disk.") else ServerNetIo).__tablename__,
        _RATE_PER_DIM_DEFS[k][0],
        _RATE_PER_DIM_DEFS[k][1],
    )
    for k in _RATE_PER_DIM_DEFS
}


@dataclass(frozen=True, slots=True)
class _TrendCtx:
    """`get_metric_trend` 한 호출의 입력.

    `bi`(time_bucket interval 문자열)와 `bucket_td`(같은 폭의 timedelta)를 둘 다 싣는 것은 절반 넘는
    builder 가 `start - bucket_td` 로 delta 계산용 선행 버킷을 붙이기 때문이다.
    """

    metric_type: MetricType | EnvironmentMetricType
    bi: str
    bucket_td: timedelta
    ae: str
    sid: str
    start: datetime
    end: datetime
    server_ids: list[int] | None
    dimension: str | None
    collapse: bool


type _TrendBuilder = Callable[[_TrendCtx], tuple[TextClause, JsonObject]]


def _trend_cpu_utilization(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, ae, sid = ctx.bi, ctx.ae, ctx.sid
    bucket_td, start, metric_type = ctx.bucket_td, ctx.start, ctx.metric_type
    params: JsonObject = {"start": ctx.start, "end": ctx.end}
    num = _CPU_NUMERATOR[metric_type]
    sql = text(f"""
        WITH raw AS (
            SELECT collected_at, server_id,
                {num} AS num_s, {_CPU_TOTAL_EXPR} AS total_s
            FROM {ServerMetrics.__tablename__}
            WHERE collected_at >= :window_start AND collected_at <= :end {sid}
        ),
        deltas AS (
            SELECT collected_at,
                num_s   - LAG(num_s)   OVER w AS d_num,
                total_s - LAG(total_s) OVER w AS d_total
            FROM raw WINDOW w AS (PARTITION BY server_id ORDER BY collected_at)
        ),
        valid AS (
            SELECT collected_at, d_num, d_total FROM deltas
            WHERE collected_at >= :start AND d_total > 0 AND d_num >= 0
        ),
        per_ts AS (
            SELECT collected_at, SUM(d_num) * 100.0 / SUM(d_total) AS v
            FROM valid GROUP BY collected_at HAVING SUM(d_total) > 0
        )
        SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
        FROM per_ts GROUP BY ts ORDER BY ts
    """)
    params["window_start"] = start - bucket_td
    return sql, params


def _trend_env_scalar_weighted(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, ae, sid = ctx.bi, ctx.ae, ctx.sid
    metric_type = ctx.metric_type
    params: JsonObject = {"start": ctx.start, "end": ctx.end}
    num, den, guard = _ENV_SCALAR_WEIGHTED[metric_type]
    ratio = f"SUM({num})::float / NULLIF(SUM({den}), 0) * 100"
    sql = text(f"""
        WITH per_ts AS (
            SELECT collected_at, {ratio} AS v
            FROM {ServerMetrics.__tablename__}
            WHERE collected_at >= :start AND collected_at <= :end {sid} AND {guard}
            GROUP BY collected_at
        )
        SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
        FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
    """)
    return sql, params


def _trend_cpu_run_queue(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, ae, server_ids = ctx.bi, ctx.ae, ctx.server_ids
    params: JsonObject = {"start": ctx.start, "end": ctx.end}
    # cpu_run_queue 는 os-aware 단일 컬럼 — Linux procs_running / Windows Processor Queue.
    sid_sm = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
    sql = text(f"""
        WITH per_ts AS (
            SELECT sm.collected_at, si.os_family AS dim,
                SUM(sm.cpu_run_queue)::float / NULLIF(SUM(si.cpu_cores), 0) AS v
            FROM {ServerMetrics.__tablename__} sm
            JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
            WHERE sm.collected_at >= :start AND sm.collected_at <= :end {sid_sm}
              AND sm.cpu_run_queue IS NOT NULL AND si.cpu_cores > 0
            GROUP BY sm.collected_at, si.os_family
        )
        SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, dim AS dimension, NULL::text AS kind
        FROM per_ts WHERE v IS NOT NULL GROUP BY ts, dim ORDER BY ts
    """)
    return sql, params


def _trend_cpu_high_utilization_hosts(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, bucket_td, start = ctx.bi, ctx.bucket_td, ctx.start
    server_ids = ctx.server_ids
    params: JsonObject = {"start": ctx.start, "end": ctx.end}
    sid = "AND server_id = ANY(:server_ids)" if server_ids else ""
    num = _CPU_NUMERATOR["cpu.usage_percent"]
    params["window_start"] = start - bucket_td
    params["cpu_under_pct"] = right_sizing.CPU_UNDER_PCT
    sql = text(f"""
        WITH raw AS (
            SELECT collected_at, server_id,
                {num} AS num_s, {_CPU_TOTAL_EXPR} AS total_s
            FROM {ServerMetrics.__tablename__}
            WHERE collected_at >= :window_start AND collected_at <= :end {sid}
        ),
        deltas AS (
            SELECT collected_at, server_id,
                num_s - LAG(num_s) OVER w AS d_num,
                total_s - LAG(total_s) OVER w AS d_total
            FROM raw WINDOW w AS (PARTITION BY server_id ORDER BY collected_at)
        ),
        flags AS (
            SELECT collected_at, server_id,
                d_num * 100.0 / d_total >= :cpu_under_pct AS crossed
            FROM deltas
            WHERE collected_at >= :start AND d_total > 0 AND d_num >= 0
        ),
        per_bucket AS (
            SELECT time_bucket(interval '{bi}', collected_at) AS ts, server_id, bool_or(crossed) AS ever
            FROM flags GROUP BY ts, server_id
        )
        SELECT ts, COUNT(*) FILTER (WHERE ever) AS value, NULL::text AS dimension, NULL::text AS kind
        FROM per_bucket GROUP BY ts ORDER BY ts
    """)
    return sql, params


def _trend_cpu_saturation(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, server_ids = ctx.bi, ctx.server_ids
    params: JsonObject = {"start": ctx.start, "end": ctx.end}
    sid_sm = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
    params["procs_running_threshold"] = right_sizing.PROCS_RUNNING_PER_CORE_SATURATION
    params["cpu_run_queue_threshold"] = right_sizing.CPU_RUN_QUEUE_PER_CORE_SATURATION
    sql = text(f"""
        WITH flags AS (
            SELECT sm.collected_at,
                (sm.cpu_run_queue::float / si.cpu_cores)
                / CASE WHEN si.os_family = 'windows' THEN (:cpu_run_queue_threshold)::float
                       ELSE (:procs_running_threshold)::float END >= 1.0 AS crossed
            FROM {ServerMetrics.__tablename__} sm
            JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
            WHERE sm.collected_at >= :start AND sm.collected_at <= :end {sid_sm}
              AND sm.cpu_run_queue IS NOT NULL AND si.cpu_cores > 0
        ),
        per_bucket AS (
            SELECT time_bucket(interval '{bi}', collected_at) AS ts, bool_or(crossed) AS ever
            FROM flags GROUP BY ts
        )
        SELECT ts, (CASE WHEN ever THEN 1.0 ELSE 0.0 END) AS value, NULL::text AS dimension, NULL::text AS kind
        FROM per_bucket ORDER BY ts
    """)
    return sql, params


def _trend_cpu_blocked(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, ae, server_ids = ctx.bi, ctx.ae, ctx.server_ids
    params: JsonObject = {"start": ctx.start, "end": ctx.end}
    # D-state 블록 gauge — Linux 전용(Windows 는 cpu_blocked 가 null 이라 자연 제외). 실행 큐와 달리 코어
    # 정규화 없이 원자값 그대로 — 실시간 스냅샷과 단위를 맞춘다.
    sid_sm = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
    sql = text(f"""
        WITH per_ts AS (
            SELECT sm.collected_at, si.os_family AS dim, AVG(sm.cpu_blocked) AS v
            FROM {ServerMetrics.__tablename__} sm
            JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
            WHERE sm.collected_at >= :start AND sm.collected_at <= :end {sid_sm}
              AND sm.cpu_blocked IS NOT NULL
            GROUP BY sm.collected_at, si.os_family
        )
        SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, dim AS dimension, NULL::text AS kind
        FROM per_ts WHERE v IS NOT NULL GROUP BY ts, dim ORDER BY ts
    """)
    return sql, params


def _trend_disk_io_saturation(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, ae, bucket_td = ctx.bi, ctx.ae, ctx.bucket_td
    start, server_ids = ctx.start, ctx.server_ids
    params: JsonObject = {"start": ctx.start, "end": ctx.end}

    sid_dio = "AND server_id = ANY(:server_ids)" if server_ids else ""
    sql = text(f"""
        WITH l_raw AS (
            SELECT collected_at, server_id, device_id,
                (COALESCE(op_read_time_s,0) + COALESCE(op_write_time_s,0)) AS t,
                (COALESCE(ops_read,0) + COALESCE(ops_write,0)) AS ops,
                COALESCE(io_time_s,0) AS iot
            FROM {ServerDiskIo.__tablename__}
            WHERE collected_at >= :window_start AND collected_at <= :end {sid_dio} AND {_PHYS_DISK_SQL_FILTER}
        ),
        l_delta AS (
            SELECT collected_at,
                GREATEST(t   - LAG(t)   OVER w, 0) AS d_t,
                GREATEST(ops - LAG(ops) OVER w, 0) AS d_ops,
                GREATEST(iot - LAG(iot) OVER w, 0) AS d_iot,
                EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS d_wall
            FROM l_raw WINDOW w AS (PARTITION BY server_id, device_id ORDER BY collected_at)
        ),
        per_dev AS (
            SELECT collected_at,
                CASE WHEN d_ops > 0 AND d_wall > 0 AND d_iot / d_wall >= :diskio_util_min
                     THEN d_t::float / d_ops * 1000 END AS await_ms
            FROM l_delta WHERE collected_at >= :start
        ),
        per_ts AS (
            SELECT collected_at, MAX(await_ms) AS v FROM per_dev GROUP BY collected_at
        )
        SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
        FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
    """)
    params["window_start"] = start - bucket_td
    params["diskio_util_min"] = right_sizing.DISKIO_UTIL_MIN
    return sql, params


def _trend_disk_saturation_hosts(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, bucket_td, start = ctx.bi, ctx.bucket_td, ctx.start
    server_ids = ctx.server_ids
    params: JsonObject = {"start": ctx.start, "end": ctx.end}

    sid_dio = "AND server_id = ANY(:server_ids)" if server_ids else ""
    params["window_start"] = start - bucket_td
    params["diskio_util_min"] = right_sizing.DISKIO_UTIL_MIN
    params["diskio_await_ms"] = right_sizing.DISKIO_AWAIT_MS
    sql = text(f"""
        WITH l_raw AS (
            SELECT collected_at, server_id, device_id,
                (COALESCE(op_read_time_s,0) + COALESCE(op_write_time_s,0)) AS t,
                (COALESCE(ops_read,0) + COALESCE(ops_write,0)) AS ops,
                COALESCE(io_time_s,0) AS iot
            FROM {ServerDiskIo.__tablename__}
            WHERE collected_at >= :window_start AND collected_at <= :end {sid_dio} AND {_PHYS_DISK_SQL_FILTER}
        ),
        l_delta AS (
            SELECT collected_at, server_id,
                t   - LAG(t)   OVER w AS d_t,
                ops - LAG(ops) OVER w AS d_ops,
                iot - LAG(iot) OVER w AS d_iot,
                EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS d_wall
            FROM l_raw WINDOW w AS (PARTITION BY server_id, device_id ORDER BY collected_at)
        ),
        per_dev AS (
            SELECT collected_at, server_id,
                CASE WHEN d_ops > 0 AND d_wall > 0 AND d_iot >= 0 AND d_iot <= d_wall
                          AND d_iot / d_wall >= :diskio_util_min
                     THEN d_t::float / d_ops * 1000 END AS await_ms
            FROM l_delta WHERE collected_at >= :start
        ),
        flags AS (
            SELECT collected_at, server_id,
                bool_or(await_ms > (:diskio_await_ms)::float) AS crossed
            FROM per_dev GROUP BY collected_at, server_id
        ),
        per_bucket AS (
            SELECT time_bucket(interval '{bi}', collected_at) AS ts, server_id, bool_or(crossed) AS ever
            FROM flags GROUP BY ts, server_id
        )
        SELECT ts, COUNT(*) FILTER (WHERE ever) AS value, NULL::text AS dimension, NULL::text AS kind
        FROM per_bucket GROUP BY ts ORDER BY ts
    """)
    return sql, params


def _trend_disk_saturation(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, bucket_td, start = ctx.bi, ctx.bucket_td, ctx.start
    server_ids = ctx.server_ids
    params: JsonObject = {"start": ctx.start, "end": ctx.end}
    # 서버 상세 이진 0/1 — disk.saturation_hosts 와 같은 원자료·임계·카운터 신뢰 조건을 1대로 축소.

    sid_dio = "AND server_id = ANY(:server_ids)" if server_ids else ""
    params["window_start"] = start - bucket_td
    params["diskio_util_min"] = right_sizing.DISKIO_UTIL_MIN
    params["diskio_await_ms"] = right_sizing.DISKIO_AWAIT_MS
    sql = text(f"""
        WITH l_raw AS (
            SELECT collected_at, server_id, device_id,
                (COALESCE(op_read_time_s,0) + COALESCE(op_write_time_s,0)) AS t,
                (COALESCE(ops_read,0) + COALESCE(ops_write,0)) AS ops,
                COALESCE(io_time_s,0) AS iot
            FROM {ServerDiskIo.__tablename__}
            WHERE collected_at >= :window_start AND collected_at <= :end {sid_dio} AND {_PHYS_DISK_SQL_FILTER}
        ),
        l_delta AS (
            SELECT collected_at,
                t   - LAG(t)   OVER w AS d_t,
                ops - LAG(ops) OVER w AS d_ops,
                iot - LAG(iot) OVER w AS d_iot,
                EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS d_wall
            FROM l_raw WINDOW w AS (PARTITION BY server_id, device_id ORDER BY collected_at)
        ),
        per_dev AS (
            SELECT collected_at,
                CASE WHEN d_ops > 0 AND d_wall > 0 AND d_iot >= 0 AND d_iot <= d_wall
                          AND d_iot / d_wall >= :diskio_util_min
                     THEN d_t::float / d_ops * 1000 END AS await_ms
            FROM l_delta WHERE collected_at >= :start
        ),
        flags AS (
            SELECT collected_at, bool_or(await_ms > (:diskio_await_ms)::float) AS crossed
            FROM per_dev GROUP BY collected_at
        ),
        per_bucket AS (
            SELECT time_bucket(interval '{bi}', collected_at) AS ts, bool_or(crossed) AS ever
            FROM flags GROUP BY ts
        )
        SELECT ts, (CASE WHEN ever THEN 1.0 ELSE 0.0 END) AS value, NULL::text AS dimension, NULL::text AS kind
        FROM per_bucket ORDER BY ts
    """)
    return sql, params


def _trend_net_retrans_percent(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, ae, bucket_td = ctx.bi, ctx.ae, ctx.bucket_td
    start, server_ids = ctx.start, ctx.server_ids
    params: JsonObject = {"start": ctx.start, "end": ctx.end}
    sid_sm = "AND server_id = ANY(:server_ids)" if server_ids else ""
    sql = text(f"""
        WITH retrans_ts AS (
            SELECT collected_at, SUM(d) AS retrans FROM (
                SELECT collected_at,
                    GREATEST(net_tcp_retransmits - LAG(net_tcp_retransmits)
                             OVER (PARTITION BY server_id ORDER BY collected_at), 0) AS d
                FROM {ServerMetrics.__tablename__}
                WHERE collected_at >= :window_start AND collected_at <= :end {sid_sm}
            ) x WHERE d IS NOT NULL GROUP BY collected_at
        ),
        txp_ts AS (
            SELECT collected_at, SUM(d) AS txp FROM (
                SELECT collected_at,
                    GREATEST(tx_packets - LAG(tx_packets)
                             OVER (PARTITION BY server_id, iface_id ORDER BY collected_at), 0) AS d
                FROM {ServerNetIo.__tablename__}
                WHERE {_PHYS_IFACE_SQL_FILTER}
                  AND collected_at >= :window_start AND collected_at <= :end {sid_sm}
            ) y WHERE d IS NOT NULL GROUP BY collected_at
        ),
        per_ts AS (
            SELECT r.collected_at, r.retrans::float / NULLIF(t.txp, 0) * 100 AS v
            FROM retrans_ts r JOIN txp_ts t USING (collected_at)
            WHERE r.collected_at >= :start
        )
        SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
        FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
    """)
    params["window_start"] = start - bucket_td
    return sql, params


def _trend_net_drop_percent(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, ae, bucket_td = ctx.bi, ctx.ae, ctx.bucket_td
    start, server_ids = ctx.start, ctx.server_ids
    params: JsonObject = {"start": ctx.start, "end": ctx.end}

    sid_nd = "AND server_id = ANY(:server_ids)" if server_ids else ""
    sql = text(f"""
        WITH raw AS (
            SELECT collected_at, server_id, iface_id, rx_dropped, tx_dropped, rx_packets, tx_packets
            FROM {ServerNetIo.__tablename__}
            WHERE {_PHYS_IFACE_SQL_FILTER}
              AND collected_at >= :window_start AND collected_at <= :end {sid_nd}
        ),
        deltas AS (
            SELECT collected_at,
                GREATEST(rx_dropped - LAG(rx_dropped) OVER w, 0) AS d_rxd,
                GREATEST(tx_dropped - LAG(tx_dropped) OVER w, 0) AS d_txd,
                GREATEST(rx_packets - LAG(rx_packets) OVER w, 0) AS d_rxp,
                GREATEST(tx_packets - LAG(tx_packets) OVER w, 0) AS d_txp
            FROM raw WINDOW w AS (PARTITION BY server_id, iface_id ORDER BY collected_at)
        ),
        per_ts AS (
            SELECT collected_at, SUM(d_rxd) + SUM(d_txd) AS drop_sum, SUM(d_rxp) + SUM(d_txp) AS pkt_sum
            FROM deltas WHERE collected_at >= :start GROUP BY collected_at
        ),
        rate_ts AS (
            SELECT collected_at, drop_sum::float / NULLIF(pkt_sum, 0) * 100 AS v FROM per_ts
        )
        SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
        FROM rate_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
    """)
    params["window_start"] = start - bucket_td
    return sql, params


def _trend_net_congested(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, bucket_td, start = ctx.bi, ctx.bucket_td, ctx.start
    server_ids = ctx.server_ids
    params: JsonObject = {"start": ctx.start, "end": ctx.end}
    # 서버 상세 이진 0/1 — net.congested_hosts 와 같은 원자료·임계·OR 판정을 1대로 축소.
    sid_nc = "AND server_id = ANY(:server_ids)" if server_ids else ""
    params["window_start"] = start - bucket_td
    params["min_traffic_kbps"] = right_sizing.NET_MIN_TRAFFIC_KBPS
    params["retrans_threshold"] = right_sizing.NET_RETRANS_PCT
    params["drop_threshold"] = right_sizing.NET_DROP_PCT
    params["conntrack_threshold"] = right_sizing.CONNTRACK_SATURATION_RATIO
    sql = text(f"""
        WITH tcp_raw AS (
            SELECT collected_at, server_id, net_tcp_retransmits AS retrans,
                net_conntrack_usage, net_conntrack_limit
            FROM {ServerMetrics.__tablename__}
            WHERE collected_at >= :window_start AND collected_at <= :end {sid_nc}
        ),
        tcp_deltas AS (
            SELECT collected_at, server_id,
                GREATEST(retrans - LAG(retrans) OVER w, 0) AS d_retrans,
                CASE WHEN net_conntrack_limit > 0
                     THEN net_conntrack_usage::float / net_conntrack_limit END AS conntrack_ratio
            FROM tcp_raw WINDOW w AS (PARTITION BY server_id ORDER BY collected_at)
        ),
        net_raw AS (
            SELECT collected_at, server_id, iface_id,
                tx_packets, rx_packets, rx_dropped, tx_dropped, rx_bytes, tx_bytes
            FROM {ServerNetIo.__tablename__}
            WHERE {_PHYS_IFACE_SQL_FILTER}
              AND collected_at >= :window_start AND collected_at <= :end {sid_nc}
        ),
        iface_deltas AS (
            SELECT collected_at, server_id,
                GREATEST(tx_packets - LAG(tx_packets) OVER w, 0) AS d_txp,
                GREATEST(rx_packets - LAG(rx_packets) OVER w, 0) AS d_rxp,
                GREATEST(rx_dropped - LAG(rx_dropped) OVER w, 0) AS d_rxd,
                GREATEST(tx_dropped - LAG(tx_dropped) OVER w, 0) AS d_txd,
                GREATEST(rx_bytes - LAG(rx_bytes) OVER w, 0) AS d_rxb,
                GREATEST(tx_bytes - LAG(tx_bytes) OVER w, 0) AS d_txb,
                EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
            FROM net_raw WINDOW w AS (PARTITION BY server_id, iface_id ORDER BY collected_at)
        ),
        net_deltas AS (
            SELECT collected_at, server_id,
                SUM(d_txp) AS d_txp,
                SUM(d_rxd) + SUM(d_txd) AS d_drop,
                SUM(d_rxp) + SUM(d_txp) AS d_pkt,
                (SUM(d_rxb) + SUM(d_txb)) / 1024.0 / NULLIF(MAX(dt), 0) AS kbytes_per_s
            FROM iface_deltas GROUP BY collected_at, server_id
        ),
        joined AS (
            SELECT n.collected_at, n.d_txp, n.d_drop, n.d_pkt, n.kbytes_per_s,
                t.d_retrans, t.conntrack_ratio
            FROM net_deltas n
            LEFT JOIN tcp_deltas t USING (collected_at, server_id)
            WHERE n.collected_at >= :start
        ),
        flags AS (
            SELECT collected_at,
                (kbytes_per_s IS NULL OR kbytes_per_s >= (:min_traffic_kbps)::float) AS has_traffic,
                d_retrans::float / NULLIF(d_txp, 0) * 100 AS retrans_pct,
                d_drop::float / NULLIF(d_pkt, 0) * 100 AS drop_pct,
                conntrack_ratio
            FROM joined
        ),
        crossed AS (
            SELECT collected_at,
                (has_traffic AND (
                    (retrans_pct IS NOT NULL AND retrans_pct > (:retrans_threshold)::float)
                    OR (drop_pct IS NOT NULL AND drop_pct > (:drop_threshold)::float)
                ))
                OR (conntrack_ratio IS NOT NULL AND conntrack_ratio >= (:conntrack_threshold)::float) AS congested
            FROM flags
        ),
        per_bucket AS (
            SELECT time_bucket(interval '{bi}', collected_at) AS ts, bool_or(congested) AS ever
            FROM crossed GROUP BY ts
        )
        SELECT ts, (CASE WHEN ever THEN 1.0 ELSE 0.0 END) AS value, NULL::text AS dimension, NULL::text AS kind
        FROM per_bucket ORDER BY ts
    """)
    return sql, params


def _trend_net_congested_hosts(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, bucket_td, start = ctx.bi, ctx.bucket_td, ctx.start
    server_ids = ctx.server_ids
    params: JsonObject = {"start": ctx.start, "end": ctx.end}

    # `docs/reference/db/repositories.md` "차트 집계" 표가 갖는다 — 여기는 그 판정의 SQL 이식이다.

    sid_nc = "AND server_id = ANY(:server_ids)" if server_ids else ""
    params["window_start"] = start - bucket_td
    params["min_traffic_kbps"] = right_sizing.NET_MIN_TRAFFIC_KBPS
    params["retrans_threshold"] = right_sizing.NET_RETRANS_PCT
    params["drop_threshold"] = right_sizing.NET_DROP_PCT
    params["conntrack_threshold"] = right_sizing.CONNTRACK_SATURATION_RATIO
    sql = text(f"""
        WITH tcp_raw AS (
            SELECT collected_at, server_id, net_tcp_retransmits AS retrans,
                net_conntrack_usage, net_conntrack_limit
            FROM {ServerMetrics.__tablename__}
            WHERE collected_at >= :window_start AND collected_at <= :end {sid_nc}
        ),
        tcp_deltas AS (
            SELECT collected_at, server_id,
                GREATEST(retrans - LAG(retrans) OVER w, 0) AS d_retrans,
                CASE WHEN net_conntrack_limit > 0
                     THEN net_conntrack_usage::float / net_conntrack_limit END AS conntrack_ratio
            FROM tcp_raw WINDOW w AS (PARTITION BY server_id ORDER BY collected_at)
        ),
        net_raw AS (
            SELECT collected_at, server_id, iface_id,
                tx_packets, rx_packets, rx_dropped, tx_dropped, rx_bytes, tx_bytes
            FROM {ServerNetIo.__tablename__}
            WHERE {_PHYS_IFACE_SQL_FILTER}
              AND collected_at >= :window_start AND collected_at <= :end {sid_nc}
        ),
        iface_deltas AS (
            -- iface 별로 delta 를 먼저 구해야 한 iface 의 counter reset 이 GREATEST(delta,0) 에 걸려도
            -- 다른 iface 의 정상 증가분과 섞이지 않는다(net.retrans_percent·net.drop_percent 와 동일
            -- per-iface-then-sum 순서 — server 레벨 SUM 을 먼저 하면 reset 이 합계 안에 묻혀버림).
            SELECT collected_at, server_id,
                GREATEST(tx_packets - LAG(tx_packets) OVER w, 0) AS d_txp,
                GREATEST(rx_packets - LAG(rx_packets) OVER w, 0) AS d_rxp,
                GREATEST(rx_dropped - LAG(rx_dropped) OVER w, 0) AS d_rxd,
                GREATEST(tx_dropped - LAG(tx_dropped) OVER w, 0) AS d_txd,
                GREATEST(rx_bytes - LAG(rx_bytes) OVER w, 0) AS d_rxb,
                GREATEST(tx_bytes - LAG(tx_bytes) OVER w, 0) AS d_txb,
                EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
            FROM net_raw WINDOW w AS (PARTITION BY server_id, iface_id ORDER BY collected_at)
        ),
        net_deltas AS (
            SELECT collected_at, server_id,
                SUM(d_txp) AS d_txp,
                SUM(d_rxd) + SUM(d_txd) AS d_drop,
                SUM(d_rxp) + SUM(d_txp) AS d_pkt,
                (SUM(d_rxb) + SUM(d_txb)) / 1024.0 / NULLIF(MAX(dt), 0) AS kbytes_per_s
            FROM iface_deltas GROUP BY collected_at, server_id
        ),
        joined AS (
            SELECT n.collected_at, n.server_id, n.d_txp, n.d_drop, n.d_pkt, n.kbytes_per_s,
                t.d_retrans, t.conntrack_ratio
            FROM net_deltas n
            LEFT JOIN tcp_deltas t USING (collected_at, server_id)
            WHERE n.collected_at >= :start
        ),
        flags AS (
            SELECT collected_at, server_id,
                (kbytes_per_s IS NULL OR kbytes_per_s >= (:min_traffic_kbps)::float) AS has_traffic,
                d_retrans::float / NULLIF(d_txp, 0) * 100 AS retrans_pct,
                d_drop::float / NULLIF(d_pkt, 0) * 100 AS drop_pct,
                conntrack_ratio
            FROM joined
        ),
        crossed AS (
            SELECT collected_at, server_id,
                (has_traffic AND (
                    (retrans_pct IS NOT NULL AND retrans_pct > (:retrans_threshold)::float)
                    OR (drop_pct IS NOT NULL AND drop_pct > (:drop_threshold)::float)
                ))
                OR (conntrack_ratio IS NOT NULL AND conntrack_ratio >= (:conntrack_threshold)::float) AS congested
            FROM flags
        ),
        per_bucket AS (
            SELECT time_bucket(interval '{bi}', collected_at) AS ts, server_id, bool_or(congested) AS ever
            FROM crossed GROUP BY ts, server_id
        )
        SELECT ts, COUNT(*) FILTER (WHERE ever) AS value, NULL::text AS dimension, NULL::text AS kind
        FROM per_bucket GROUP BY ts ORDER BY ts
    """)
    return sql, params


def _trend_psi(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, ae, bucket_td = ctx.bi, ctx.ae, ctx.bucket_td
    start, server_ids, metric_type = ctx.start, ctx.server_ids, ctx.metric_type
    params: JsonObject = {"start": ctx.start, "end": ctx.end}

    # Linux 4.20+ 에만 행이 있어 미지원 OS(Windows)는 빈 결과 -> 차트 empty state.
    psi_resource = {"cpu.psi": "cpu", "mem.psi": "memory", "disk.psi": "io"}[metric_type]
    sid_psi = "AND server_id = ANY(:server_ids)" if server_ids else ""
    sql = text(f"""
        WITH l_raw AS (
            SELECT collected_at, server_id, stall_time_s AS s
            FROM server_pressure
            WHERE resource = :psi_resource AND scope = 'some'
              AND collected_at >= :window_start AND collected_at <= :end {sid_psi}
        ),
        l_delta AS (
            SELECT collected_at,
                GREATEST(s - LAG(s) OVER w, 0) AS d_s,
                EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
            FROM l_raw WINDOW w AS (PARTITION BY server_id ORDER BY collected_at)
        ),
        per_ts AS (
            SELECT collected_at, SUM(d_s)::float / NULLIF(SUM(dt), 0) * 100 AS v
            FROM l_delta WHERE collected_at >= :start AND dt > 0
            GROUP BY collected_at
        )
        SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
        FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
    """)
    params["window_start"] = start - bucket_td
    params["psi_resource"] = psi_resource
    return sql, params


def _trend_mem_paging_pressure(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, bucket_td, start = ctx.bi, ctx.bucket_td, ctx.start
    server_ids = ctx.server_ids
    params: JsonObject = {"start": ctx.start, "end": ctx.end}

    # Linux 하드폴트는 절대 rate 가 디스크 속도에 좌우돼 보편 임계를 못 잡아 "> 0" 존재 판정이다 — Windows

    # 컬럼이 os-aware 인 것은 agent 가 Windows 에서 paging.operations 를 direction=in 만 발행해 paging_major
    # 가 항상 NULL 이기 때문이다 (Linux=paging_major / Windows=paging_in, get_report_aggregate 와 동일 소스).
    sid_mp = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
    params["window_start"] = start - bucket_td
    params["win_paging_threshold"] = right_sizing.WIN_PAGES_INPUT_SATURATION
    sql = text(f"""
        WITH raw AS (
            SELECT sm.collected_at, sm.server_id, si.os_family,
                   CASE WHEN si.os_family = 'windows' THEN sm.paging_in ELSE sm.paging_major END AS p
            FROM {ServerMetrics.__tablename__} sm
            JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
            WHERE sm.collected_at >= :window_start AND sm.collected_at <= :end {sid_mp}
              AND (CASE WHEN si.os_family = 'windows' THEN sm.paging_in ELSE sm.paging_major END) IS NOT NULL
        ),
        deltas AS (
            SELECT collected_at, os_family,
                GREATEST(p - LAG(p) OVER w, 0) AS d_p,
                EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
            FROM raw WINDOW w AS (PARTITION BY server_id ORDER BY collected_at)
        ),
        rates AS (
            SELECT collected_at, os_family, d_p::float / dt AS rate
            FROM deltas WHERE collected_at >= :start AND dt > 0
        ),
        flags AS (
            SELECT collected_at,
                (os_family = 'windows' AND rate >= (:win_paging_threshold)::float)
                OR (os_family != 'windows' AND rate > 0) AS crossed
            FROM rates
        ),
        per_bucket AS (
            SELECT time_bucket(interval '{bi}', collected_at) AS ts, bool_or(crossed) AS ever
            FROM flags GROUP BY ts
        )
        SELECT ts, (CASE WHEN ever THEN 1.0 ELSE 0.0 END) AS value, NULL::text AS dimension, NULL::text AS kind
        FROM per_bucket ORDER BY ts
    """)
    return sql, params


def _trend_mem_paging_pressure_hosts(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, bucket_td, start = ctx.bi, ctx.bucket_td, ctx.start
    server_ids = ctx.server_ids
    params: JsonObject = {"start": ctx.start, "end": ctx.end}
    # mem.paging_pressure와 같은 원자료와 OS별 임계값을 서버별로 적용해

    sid_sm = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
    params["window_start"] = start - bucket_td
    params["win_paging_threshold"] = right_sizing.WIN_PAGES_INPUT_SATURATION
    sql = text(f"""
        WITH raw AS (
            SELECT sm.collected_at, sm.server_id, si.os_family,
                   CASE WHEN si.os_family = 'windows' THEN sm.paging_in ELSE sm.paging_major END AS p
            FROM {ServerMetrics.__tablename__} sm
            JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
            WHERE sm.collected_at >= :window_start AND sm.collected_at <= :end {sid_sm}
              AND (CASE WHEN si.os_family = 'windows' THEN sm.paging_in ELSE sm.paging_major END) IS NOT NULL
        ),
        deltas AS (
            SELECT collected_at, server_id, os_family,
                GREATEST(p - LAG(p) OVER w, 0) AS d_p,
                EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
            FROM raw WINDOW w AS (PARTITION BY server_id ORDER BY collected_at)
        ),
        rates AS (
            SELECT collected_at, server_id, os_family, d_p::float / dt AS rate
            FROM deltas WHERE collected_at >= :start AND dt > 0
        ),
        flags AS (
            SELECT collected_at, server_id,
                (os_family = 'windows' AND rate >= (:win_paging_threshold)::float)
                OR (os_family != 'windows' AND rate > 0) AS crossed
            FROM rates
        ),
        per_bucket AS (
            SELECT time_bucket(interval '{bi}', collected_at) AS ts, server_id, bool_or(crossed) AS ever
            FROM flags GROUP BY ts, server_id
        )
        SELECT ts, COUNT(*) FILTER (WHERE ever) AS value, NULL::text AS dimension, NULL::text AS kind
        FROM per_bucket GROUP BY ts ORDER BY ts
    """)
    return sql, params


def _trend_fs_usage_percent(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, ae, sid = ctx.bi, ctx.ae, ctx.sid
    bucket_td, start, server_ids = ctx.bucket_td, ctx.start, ctx.server_ids
    dimension, collapse = ctx.dimension, ctx.collapse
    params: JsonObject = {"start": ctx.start, "end": ctx.end}
    if collapse:
        sid_fs = "AND server_id = ANY(:server_ids)" if server_ids else ""
        sid_fs_sf = "AND sf.server_id = ANY(:server_ids)" if server_ids else ""
        params["window_start"] = start - bucket_td
        sql = text(f"""
            WITH targets AS (
                SELECT DISTINCT server_id, mountpoint
                FROM {ServerFilesystem.__tablename__}
                WHERE collected_at >= :window_start AND collected_at <= :end {sid_fs}
                  AND {_DATA_VOLUME_SQL_FILTER}
            ),
            buckets AS (
                SELECT generate_series(
                    time_bucket(interval '{bi}', (:start)::timestamptz),
                    time_bucket(interval '{bi}', (:end)::timestamptz),
                    interval '{bi}'
                ) AS ts
            ),
            per_bucket AS (
                SELECT b.ts, t.server_id, t.mountpoint, lv.used AS used, lv.free AS free
                FROM buckets b
                CROSS JOIN targets t
                LEFT JOIN LATERAL (
                    SELECT sf.used_bytes AS used, sf.free_bytes AS free
                    FROM {ServerFilesystem.__tablename__} sf
                    WHERE sf.server_id = t.server_id AND sf.mountpoint = t.mountpoint
                      AND sf.collected_at >= :window_start
                      AND sf.collected_at < b.ts + interval '{bi}' {sid_fs_sf}
                      AND sf.used_bytes IS NOT NULL AND sf.free_bytes IS NOT NULL
                    ORDER BY sf.collected_at DESC
                    LIMIT 1
                ) lv ON true
            )
            SELECT ts,
                SUM(used)::float / NULLIF(SUM(used + free), 0) * 100 AS value,
                NULL::text AS dimension, NULL::text AS kind
            FROM per_bucket GROUP BY ts HAVING SUM(used + free) > 0 ORDER BY ts
        """)
    else:
        sql = text(f"""
            WITH per_ts AS (
                SELECT collected_at, mountpoint AS dim,
                    SUM(used_bytes)::float / NULLIF(SUM(used_bytes + free_bytes), 0) * 100 AS v
                FROM {ServerFilesystem.__tablename__}
                WHERE collected_at >= :start AND collected_at <= :end {sid}
                  AND {_DATA_VOLUME_SQL_FILTER}
                  AND used_bytes IS NOT NULL AND free_bytes IS NOT NULL AND (used_bytes + free_bytes) > 0
                  AND (CAST(:dim_filter AS text) IS NULL OR mountpoint = :dim_filter)
                GROUP BY collected_at, mountpoint
            )
            SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, dim AS dimension, NULL::text AS kind
            FROM per_ts WHERE v IS NOT NULL GROUP BY ts, dim ORDER BY ts, dim
        """)
        params["dim_filter"] = dimension
    return sql, params


def _trend_rate_per_dim(ctx: _TrendCtx) -> tuple[TextClause, JsonObject]:
    bi, ae, sid = ctx.bi, ctx.ae, ctx.sid
    bucket_td, start, dimension = ctx.bucket_td, ctx.start, ctx.dimension
    collapse, metric_type = ctx.collapse, ctx.metric_type
    params: JsonObject = {"start": ctx.start, "end": ctx.end}
    table, dim_col, value_col = _RATE_PER_DIM[metric_type]

    phys_filter = _PHYS_DISK_SQL_FILTER if dim_col == "device_id" else _PHYS_IFACE_SQL_FILTER
    if collapse:
        dev_filter = phys_filter
        tail = f"""
            per_sd AS (
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, server_id, dim, avg(v) AS sv
                FROM rates WHERE v IS NOT NULL GROUP BY 1, server_id, dim
            )
            SELECT ts, SUM(sv) AS value, NULL::text AS dimension, NULL::text AS kind
            FROM per_sd GROUP BY ts ORDER BY ts
        """
    else:
        dev_filter = f"{phys_filter} AND (CAST(:dim_filter AS text) IS NULL OR {dim_col} = :dim_filter)"
        params["dim_filter"] = dimension

        # id 를 그대로 두면 MAC 이 노출돼 사람이 못 읽는다. 매칭 실패는 COALESCE 로 raw dim 폴백.
        if dim_col == "device_id":
            name_join = """
                LEFT JOIN LATERAL (
                    SELECT elem->>'name' AS name
                    FROM server_inventory si_dn, jsonb_array_elements(si_dn.block_devices) elem
                    WHERE si_dn.id = per_ts.server_id
                      AND ((elem->>'id_type') || ':' || (elem->>'id') = per_ts.dim
                           OR 'name:' || (elem->>'name') = per_ts.dim)
                      AND (elem->>'type') = 'disk'
                    LIMIT 1
                ) dn ON true
            """
        else:
            name_join = """
                LEFT JOIN LATERAL (
                    SELECT elem->>'name' AS name
                    FROM server_inventory si_dn, jsonb_array_elements(si_dn.net_interfaces) elem
                    WHERE si_dn.id = per_ts.server_id
                      AND (elem->>'id_type') || ':' || (elem->>'id') = per_ts.dim
                      AND (elem->>'kind') IN ('physical', 'bond_master')
                    LIMIT 1
                ) dn ON true
            """
        tail = f"""
            per_ts AS (
                SELECT collected_at, server_id, dim, SUM(v) AS v
                FROM rates WHERE v IS NOT NULL GROUP BY collected_at, server_id, dim
            )
            SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value,
                COALESCE(dn.name, dim) AS dimension, NULL::text AS kind
            FROM per_ts
            {name_join}
            GROUP BY ts, dim, dn.name ORDER BY ts, dim
        """
    sql = text(f"""
        WITH raw AS (
            SELECT collected_at, server_id, {dim_col} AS dim, {value_col} AS cnt
            FROM {table}
            WHERE collected_at >= :window_start AND collected_at <= :end {sid} AND {dev_filter}
        ),
        deltas AS (
            SELECT collected_at, server_id, dim,
                GREATEST(cnt - LAG(cnt) OVER w, 0) AS d_val,
                EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
            FROM raw WINDOW w AS (PARTITION BY server_id, dim ORDER BY collected_at)
        ),
        rates AS (
            SELECT collected_at, server_id, dim,
                CASE WHEN dt IS NULL OR dt <= 0 OR d_val IS NULL THEN NULL ELSE d_val / dt END AS v
            FROM deltas WHERE collected_at >= :start
        ),
        {tail}
    """)
    params["window_start"] = start - bucket_td
    return sql, params


# 덮이면 서버 상세 차트가 환경용 SQL 로 그려지는데 키 집합 비교는 통과한다.
_TREND_PAIRS: list[tuple[MetricType | EnvironmentMetricType, _TrendBuilder]] = [
    *((k, _trend_cpu_utilization) for k in _CPU_NUMERATOR),
    *((k, _trend_env_scalar_weighted) for k in _ENV_SCALAR_WEIGHTED),
    ("cpu.run_queue", _trend_cpu_run_queue),
    ("cpu.high_utilization_hosts", _trend_cpu_high_utilization_hosts),
    ("cpu.saturation", _trend_cpu_saturation),
    ("cpu.blocked", _trend_cpu_blocked),
    ("disk.io_saturation", _trend_disk_io_saturation),
    ("disk.saturation_hosts", _trend_disk_saturation_hosts),
    ("disk.saturation", _trend_disk_saturation),
    ("net.retrans_percent", _trend_net_retrans_percent),
    ("net.drop_percent", _trend_net_drop_percent),
    ("net.congested", _trend_net_congested),
    ("net.congested_hosts", _trend_net_congested_hosts),
    ("cpu.psi", _trend_psi),
    ("mem.psi", _trend_psi),
    ("disk.psi", _trend_psi),
    ("mem.paging_pressure", _trend_mem_paging_pressure),
    ("mem.paging_pressure_hosts", _trend_mem_paging_pressure_hosts),
    ("fs.usage_percent", _trend_fs_usage_percent),
    *((k, _trend_rate_per_dim) for k in _RATE_PER_DIM_DEFS),
]

_TREND_BUILDERS: Mapping[MetricType | EnvironmentMetricType, _TrendBuilder] = dict(_TREND_PAIRS)


_TREND_KEYS: frozenset[str] = frozenset(get_args(MetricType.__value__)) | frozenset(
    get_args(EnvironmentMetricType.__value__)
)

if len(_TREND_PAIRS) != len(_TREND_BUILDERS):
    raise AssertionError("get_metric_trend dispatch 에 중복 키가 있다 — 뒤 항목이 앞을 덮는다")
_REGISTERED: frozenset[str] = frozenset(_TREND_BUILDERS)
if _REGISTERED != _TREND_KEYS:
    raise AssertionError(f"get_metric_trend dispatch 누락·초과: {_REGISTERED ^ _TREND_KEYS}")


class SqlMetricQueryRepository(_BaseQueryMixin):
    _METRIC_SNAPSHOTS_WINDOW = timedelta(days=30)

    async def get_latest_dashboard(self, server_id: int) -> DashboardRaw | None:
        inv = await self.session.execute(
            select(
                ServerInventory.os_family,
                ServerInventory.kernel_version,
                ServerInventory.block_devices,
                ServerInventory.net_interfaces,
            ).where(ServerInventory.id == server_id)
        )
        inv_row = inv.first()
        if inv_row is None:
            return None
        # 넷 다 nullable — kernel_version 은 PSI 지원(Linux 4.20+) 판정, block_devices/net_interfaces 는 물리 필터 입력.
        os_family = inv_row[0]
        kernel_version = inv_row[1]
        block_devices = inv_row[2]
        net_interfaces = inv_row[3]

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
                cpu_user_s=m.cpu_user_s,
                cpu_nice_s=m.cpu_nice_s,
                cpu_system_s=m.cpu_system_s,
                cpu_idle_s=m.cpu_idle_s,
                cpu_iowait_s=m.cpu_iowait_s,
                cpu_irq_s=m.cpu_irq_s,
                cpu_softirq_s=m.cpu_softirq_s,
                cpu_steal_s=m.cpu_steal_s,
                mem_limit_bytes=m.mem_limit_bytes,
                mem_free_bytes=m.mem_free_bytes,
                mem_available_bytes=m.mem_available_bytes,
                mem_buffered_bytes=m.mem_buffered_bytes,
                mem_cached_bytes=m.mem_cached_bytes,
                mem_used_bytes=m.mem_used_bytes,
                cpu_run_queue=m.cpu_run_queue,
                cpu_logical_count=m.cpu_logical_count,
                cpu_blocked=m.cpu_blocked,
                boot_time=m.boot_time,
                agent_started_at=m.agent_started_at,
            )
            for m in m_result.scalars().all()
        ]

        d_rows = await self._latest_per_dimension(ServerDiskIo, ServerDiskIo.device_id, server_id, n=2)
        disk_io = [
            DiskIoRaw(
                device_id=row.device_id,
                device_name=row.device_name,
                collected_at=row.collected_at,
                io_read_bytes=row.io_read_bytes,
                io_write_bytes=row.io_write_bytes,
                ops_read=row.ops_read,
                ops_write=row.ops_write,
                op_read_time_s=row.op_read_time_s,
                op_write_time_s=row.op_write_time_s,
                io_time_s=row.io_time_s,
                pending_ops=row.pending_ops,
            )
            for row in d_rows
        ]

        c_rows = await self._latest_per_dimension(ServerCpuCore, ServerCpuCore.core_id, server_id, n=2)
        cpu_cores = [
            CpuCoreRaw(
                core_id=row.core_id,
                collected_at=row.collected_at,
                cpu_user_s=row.cpu_user_s,
                cpu_nice_s=row.cpu_nice_s,
                cpu_system_s=row.cpu_system_s,
                cpu_idle_s=row.cpu_idle_s,
                cpu_iowait_s=row.cpu_iowait_s,
                cpu_irq_s=row.cpu_irq_s,
                cpu_softirq_s=row.cpu_softirq_s,
                cpu_steal_s=row.cpu_steal_s,
            )
            for row in c_rows
        ]

        n_rows = await self._latest_per_dimension(ServerNetIo, ServerNetIo.iface_id, server_id, n=2)
        net_io = [
            NetIoRaw(
                iface_id=row.iface_id,
                iface_name=row.iface_name,
                collected_at=row.collected_at,
                rx_bytes=row.rx_bytes,
                tx_bytes=row.tx_bytes,
                rx_packets=row.rx_packets,
                tx_packets=row.tx_packets,
                rx_errors=row.rx_errors,
                tx_errors=row.tx_errors,
                rx_dropped=row.rx_dropped,
                tx_dropped=row.tx_dropped,
                link_speed_bps=row.link_speed_bps,
            )
            for row in n_rows
        ]

        fs_rows = await self._latest_per_dimension(ServerFilesystem, ServerFilesystem.mountpoint, server_id, n=1)
        filesystems = [
            MountUsageRaw(
                mountpoint=row.mountpoint,
                used_bytes=row.used_bytes,
                free_bytes=row.free_bytes,
                inodes_used=row.inodes_used,
                inodes_free=row.inodes_free,
                device_id=row.device_id,
                fstype=row.fstype,
                collected_at=row.collected_at,
            )
            for row in fs_rows
        ]

        return DashboardRaw(
            metrics=metrics,
            disk_io=disk_io,
            net_io=net_io,
            filesystems=filesystems,
            os_family=os_family,
            kernel_version=kernel_version,
            block_devices=block_devices,
            net_interfaces=net_interfaces,
            cpu_cores=cpu_cores,
        )

    async def get_latest_saturation(self, server_ids: list[int], since: datetime) -> dict[int, SaturationRaw]:
        """서버별 실시간 포화 원자료 (os 통일).

        - run_queue: 최신 cpu_run_queue gauge (Linux procs_running / Windows Processor Queue).
        - await_ms / disk_io_util_pct / pending_ops: 물리 disk worst device 기준 (산출·신뢰 조건은 da CTE 주석).
        - paging_major_rate: 하드폴트 rate — Linux paging_major(refault) / Windows paging_in(Pages Input/sec).
          agent 가 Windows 에서 paging.operations 를 direction=in 만 발행해 paging_major 가 항상 NULL 이라
          컬럼이 갈린다 (get_report_aggregate pages_input_rate 와 동일 소스).
        - psi_cpu / psi_mem / psi_io: PSI %정체 — Linux 4.20+ 만 값, 그 외 null.

        since 이후 최신 2행 delta. reset(값-감소)은 None 가드, now+2m 상한으로 시계 어긋난 미래 행을 배제한다.
        """
        if not server_ids:
            return {}
        sql = text(f"""
            WITH m2 AS (
                SELECT sm.server_id, sm.cpu_run_queue,
                       CASE WHEN si.os_family = 'windows' THEN sm.paging_in ELSE sm.paging_major END AS paging_val,
                       sm.net_tcp_retransmits, sm.collected_at,
                       sm.net_conntrack_usage, sm.net_conntrack_limit,
                       row_number() OVER (PARTITION BY sm.server_id ORDER BY sm.collected_at DESC) AS rn
                FROM server_metrics sm
                JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
                WHERE sm.server_id = ANY(:sids) AND sm.collected_at >= :since
                      AND sm.collected_at <= now() + interval '2 minutes'
            ),
            m AS (
                SELECT server_id,
                    max(CASE WHEN rn = 1 THEN cpu_run_queue END) AS run_queue,
                    max(CASE WHEN rn = 1 THEN net_tcp_retransmits END)
                        - max(CASE WHEN rn = 2 THEN net_tcp_retransmits END) AS retrans_delta,
                    CASE WHEN max(CASE WHEN rn = 1 THEN net_conntrack_limit END) > 0
                         THEN max(CASE WHEN rn = 1 THEN net_conntrack_usage END)::float
                              / max(CASE WHEN rn = 1 THEN net_conntrack_limit END) END AS conntrack_ratio,
                    CASE WHEN max(CASE WHEN rn = 1 THEN paging_val END) >= max(CASE WHEN rn = 2 THEN paging_val END)
                              AND max(CASE WHEN rn = 1 THEN collected_at END) > max(CASE WHEN rn = 2 THEN collected_at END)
                         THEN (max(CASE WHEN rn = 1 THEN paging_val END) - max(CASE WHEN rn = 2 THEN paging_val END))::float
                              / EXTRACT(EPOCH FROM (max(CASE WHEN rn = 1 THEN collected_at END)
                                                    - max(CASE WHEN rn = 2 THEN collected_at END)))
                    END AS paging_major_rate
                FROM m2 WHERE rn <= 2 GROUP BY server_id
            ),
            d2 AS (
                -- 물리 disk 만(합성/숨김 pseudo-device 제외, chart/get_report_aggregate 와 동일 fail-closed 필터) —
                -- 미필터면 Windows aggregate:system 같은 pseudo-device 가 실측 0%/await 로 위장해 진짜 물리
                -- 디바이스(PhysicalDrive0 등) 카운터 이상 시 그걸로 대체돼 버림(오탐 은폐, N/A 여야 할 게 값으로 보임).
                SELECT server_id, device_id,
                       (COALESCE(op_read_time_s,0) + COALESCE(op_write_time_s,0)) AS t,
                       (COALESCE(ops_read,0) + COALESCE(ops_write,0)) AS ops,
                       COALESCE(io_time_s,0) AS iot, pending_ops, collected_at,
                       row_number() OVER (PARTITION BY server_id, device_id ORDER BY collected_at DESC) AS rn
                FROM server_disk_io
                WHERE server_id = ANY(:sids) AND collected_at >= :since AND collected_at <= now() + interval '2 minutes'
                      AND {_PHYS_DISK_SQL_FILTER}
            ),
            dd AS (
                -- device 별 델타 — await = delta(op_time) / delta(ops), util = delta(io_time) / delta(wall).
                SELECT server_id,
                    max(CASE WHEN rn = 1 THEN t END)   - max(CASE WHEN rn = 2 THEN t END)   AS t_delta,
                    max(CASE WHEN rn = 1 THEN ops END) - max(CASE WHEN rn = 2 THEN ops END) AS ops_delta,
                    max(CASE WHEN rn = 1 THEN iot END) - max(CASE WHEN rn = 2 THEN iot END) AS iot_delta,
                    EXTRACT(EPOCH FROM (max(CASE WHEN rn = 1 THEN collected_at END)
                                        - max(CASE WHEN rn = 2 THEN collected_at END))) AS wall,
                    max(CASE WHEN rn = 1 THEN pending_ops END) AS pending_ops
                FROM d2 WHERE rn <= 2 GROUP BY server_id, device_id
            ),
            da AS (
                -- 실제 바쁜(io_time util >= :diskio_util_min) device 만 await 채택 후 worst(MAX) — report.py 와 동일.
                -- 유휴 device 의 writeback 큐 잔류 await 폭증 억제(병목 아님).
                -- disk_io_util_pct(delta(io_time)/delta(wall)*100) 는 worst(MAX) device 채택 — 유휴(0%)도 실측값이라 await 같은
                -- util 하한 게이트는 없음. 단 ops_delta > 0 요구(await 와 동일 원칙) — 연산 0건인데 io_time 만 증가하는
                -- 건 물리적으로 모순(구세대 virtio 드라이버 phantom busy 카운터 실측, 완료 연산 없이 바쁨 시간만 누적) —
                -- 그대로 보이면 오탐이라 그 device 는 미측정 처리. d2 가 이미 물리 disk only(_PHYS_DISK_SQL_FILTER)라
                -- 합성 pseudo-device 로 위장 대체될 일은 없음 — 유일한 물리 device 가 phantom busy 면 host 전체 None.
                -- iot_delta < 0(카운터 리셋/역행) 또는 iot_delta > wall(busy 시간이 경과시간을 초과 — agent-data.md
                -- disk.io_time 계약상 %util busy 분율은 [0, wall] 이어야 정상, 초과는 카운터 이상)도 None 가드 —
                -- await 와 동일 원칙(reset·overflow 흡수).
                -- pending_ops(await 폴백, Windows 전용)도 동일 신뢰 조건 요구 — io_time 카운터가 깨진 device 는
                -- io_time 만 못 믿는 게 아니라 그 device 자체를 못 믿는다(보수적 원칙). 게이트 없이 max(pending_ops)
                -- 그대로 쓰면 phantom busy/overflow device 의 다른 카운터(대기열 깊이)까지 진짜인 척 새어나가
                -- Windows 큐 폴백도 같은 신뢰 조건을 적용한다.
                SELECT server_id,
                    max(CASE WHEN ops_delta > 0 AND t_delta >= 0 AND wall > 0
                                  AND iot_delta / wall >= :diskio_util_min AND iot_delta <= wall
                             THEN t_delta::float / ops_delta * 1000 END) AS await_ms,
                    max(CASE WHEN ops_delta > 0 AND wall > 0 AND iot_delta >= 0 AND iot_delta <= wall
                             THEN iot_delta / wall * 100 END) AS disk_io_util_pct,
                    max(CASE WHEN ops_delta > 0 AND wall > 0 AND iot_delta >= 0 AND iot_delta <= wall
                             THEN pending_ops END) AS pending_ops
                FROM dd GROUP BY server_id
            ),
            n2 AS (
                SELECT server_id, rx_packets, tx_packets, rx_dropped, tx_dropped,
                       row_number() OVER (PARTITION BY server_id, iface_id ORDER BY collected_at DESC) AS rn
                FROM server_net_io
                WHERE server_id = ANY(:sids) AND collected_at >= :since AND collected_at <= now() + interval '2 minutes'
            ),
            nt AS (
                SELECT server_id,
                    SUM(CASE WHEN rn = 1 THEN COALESCE(tx_packets,0)+COALESCE(rx_packets,0) END)
                      - SUM(CASE WHEN rn = 2 THEN COALESCE(tx_packets,0)+COALESCE(rx_packets,0) END) AS pkt_delta,
                    SUM(CASE WHEN rn = 1 THEN COALESCE(tx_packets,0) END)
                      - SUM(CASE WHEN rn = 2 THEN COALESCE(tx_packets,0) END) AS txp_delta,
                    SUM(CASE WHEN rn = 1 THEN COALESCE(rx_dropped,0)+COALESCE(tx_dropped,0) END)
                      - SUM(CASE WHEN rn = 2 THEN COALESCE(rx_dropped,0)+COALESCE(tx_dropped,0) END) AS drop_delta
                FROM n2 WHERE rn <= 2 GROUP BY server_id
            ),
            psi AS (
                -- PSI %정체 = stall_time_s delta / wall-time delta * 100 (scope=some, resource cpu/memory/io).
                -- 에이전트는 stall_time_s(counter)를 발행(ratio_avg10 gauge 는 미발행) -> 페이징과 동형 rate 산출.
                -- Linux 4.20+ 만 행 존재 -> 미지원 OS 는 psi_* null (LEFT JOIN). reset(값-감소)·dt<=0 은 null 가드.
                SELECT server_id,
                    max(CASE WHEN resource = 'cpu'    THEN rate END) AS psi_cpu,
                    max(CASE WHEN resource = 'memory' THEN rate END) AS psi_mem,
                    max(CASE WHEN resource = 'io'     THEN rate END) AS psi_io
                FROM (
                    SELECT server_id, resource,
                        CASE WHEN s1 >= s2 AND t1 > t2
                             THEN (s1 - s2) / EXTRACT(EPOCH FROM (t1 - t2)) * 100 END AS rate
                    FROM (
                        SELECT server_id, resource,
                            max(CASE WHEN rn = 1 THEN stall_time_s END) AS s1,
                            max(CASE WHEN rn = 2 THEN stall_time_s END) AS s2,
                            max(CASE WHEN rn = 1 THEN collected_at END) AS t1,
                            max(CASE WHEN rn = 2 THEN collected_at END) AS t2
                        FROM (
                            SELECT server_id, resource, stall_time_s, collected_at,
                                   row_number() OVER (PARTITION BY server_id, resource
                                                      ORDER BY collected_at DESC) AS rn
                            FROM server_pressure
                            WHERE server_id = ANY(:sids) AND scope = 'some'
                                  AND collected_at >= :since AND collected_at <= now() + interval '2 minutes'
                        ) pp WHERE rn <= 2 GROUP BY server_id, resource
                    ) pd
                ) pr GROUP BY server_id
            )
            SELECT m.server_id, m.run_queue, m.conntrack_ratio, m.paging_major_rate, da.pending_ops,
                   psi.psi_cpu, psi.psi_mem, psi.psi_io,
                   da.await_ms AS await_ms, da.disk_io_util_pct AS disk_io_util_pct,
                   CASE WHEN nt.txp_delta > 0 AND m.retrans_delta >= 0
                        THEN m.retrans_delta::float / nt.txp_delta * 100 END AS retrans_pct,
                   CASE WHEN nt.pkt_delta > 0 AND nt.drop_delta >= 0
                        THEN nt.drop_delta::float / nt.pkt_delta * 100 END AS drop_pct
            FROM m LEFT JOIN da ON da.server_id = m.server_id LEFT JOIN nt ON nt.server_id = m.server_id
                   LEFT JOIN psi ON psi.server_id = m.server_id
        """)
        result = await self.session.execute(
            sql, {"sids": server_ids, "since": since, "diskio_util_min": right_sizing.DISKIO_UTIL_MIN}
        )

        def _f(v: float | None) -> float | None:
            return float(v) if v is not None else None

        return {
            r.server_id: SaturationRaw(
                run_queue=_f(r.run_queue),
                await_ms=_f(r.await_ms),
                disk_io_util_pct=_f(r.disk_io_util_pct),
                pending_ops=_f(r.pending_ops),
                paging_major_rate=_f(r.paging_major_rate),
                retrans_pct=_f(r.retrans_pct),
                drop_pct=_f(r.drop_pct),
                conntrack_ratio=_f(r.conntrack_ratio),
                psi_cpu=_f(r.psi_cpu),
                psi_mem=_f(r.psi_mem),
                psi_io=_f(r.psi_io),
            )
            for r in result
        }

    async def get_latest_errors(self, server_id: int, since: datetime) -> ErrorFleetRaw:
        m = (
            await self.session.execute(
                text("""
                    SELECT COALESCE(MAX(cpu_mce) - MIN(cpu_mce), 0) AS mce,
                           COALESCE(MAX(mem_oom_kill) - MIN(mem_oom_kill), 0) AS oom,
                           count(*) AS n,
                           (SELECT mem_hardware_corrupted_bytes FROM server_metrics
                             WHERE server_id = :sid AND collected_at >= :since
                             ORDER BY collected_at DESC LIMIT 1) AS corrupted
                    FROM server_metrics
                    WHERE server_id = :sid AND collected_at >= :since
                      AND collected_at <= now() + interval '2 minutes'
                """),
                {"sid": server_id, "since": since},
            )
        ).one()
        net = (
            await self.session.execute(
                text("""
                    SELECT COALESCE(SUM(d), 0) AS net_err, count(*) AS ifaces FROM (
                        SELECT MAX(COALESCE(rx_errors,0)+COALESCE(tx_errors,0))
                             - MIN(COALESCE(rx_errors,0)+COALESCE(tx_errors,0)) AS d
                        FROM server_net_io
                        WHERE server_id = :sid AND collected_at >= :since
                          AND collected_at <= now() + interval '2 minutes'
                        GROUP BY iface_id
                    ) x
                """),
                {"sid": server_id, "since": since},
            )
        ).one()
        de = (
            await self.session.execute(
                text("""
                    SELECT COALESCE(SUM(d), 0) AS cnt,
                           COALESCE(array_agg(DISTINCT kc) FILTER (WHERE d > 0), ARRAY[]::text[]) AS kinds,
                           MAX(last_at) FILTER (WHERE d > 0) AS last_at
                    FROM (
                        SELECT error_kind || CASE WHEN error_class = '' THEN '' ELSE '/' || error_class END AS kc,
                               MAX(count) - MIN(count) AS d, MAX(collected_at) AS last_at
                        FROM server_disk_error
                        WHERE server_id = :sid AND collected_at >= :since
                          AND collected_at <= now() + interval '2 minutes'
                        GROUP BY device_id, error_kind, error_class, member
                    ) x
                """),
                {"sid": server_id, "since": since},
            )
        ).one()
        return ErrorFleetRaw(
            measured=m.n > 0,
            net_measured=net.ifaces > 0,
            disk_err_measured=True,
            mce_count=int(m.mce or 0),
            oom_count=int(m.oom or 0),
            corrupted_bytes=int(m.corrupted) if m.corrupted is not None else None,
            net_error_count=int(net.net_err or 0),
            disk_error_count=int(de.cnt or 0),
            disk_error_kinds=list(de.kinds or []),
            last_error_at=de.last_at,
        )

    async def get_fleet_error_summary(self, server_ids: list[int], since: datetime) -> FleetErrorRaw:
        if not server_ids:
            return FleetErrorRaw()
        m = (
            await self.session.execute(
                text("""
                    SELECT count(*) AS total,
                           count(*) FILTER (WHERE mce_d > 0)  AS mce_hosts,
                           count(*) FILTER (WHERE oom_d > 0)  AS oom_hosts,
                           count(*) FILTER (WHERE corrupted > 0) AS corrupted_hosts
                    FROM (
                        SELECT server_id,
                            COALESCE(MAX(cpu_mce) - MIN(cpu_mce), 0) AS mce_d,
                            COALESCE(MAX(mem_oom_kill) - MIN(mem_oom_kill), 0) AS oom_d,
                            COALESCE(MAX(mem_hardware_corrupted_bytes), 0) AS corrupted
                        FROM server_metrics
                        WHERE server_id = ANY(:sids) AND collected_at >= :since
                          AND collected_at <= now() + interval '2 minutes'
                        GROUP BY server_id
                    ) x
                """),
                {"sids": server_ids, "since": since},
            )
        ).one()
        net_hosts = (
            await self.session.execute(
                text("""
                    SELECT count(*) FILTER (WHERE net_d > 0) FROM (
                        SELECT server_id, SUM(d) AS net_d FROM (
                            SELECT server_id, MAX(COALESCE(rx_errors,0)+COALESCE(tx_errors,0))
                                            - MIN(COALESCE(rx_errors,0)+COALESCE(tx_errors,0)) AS d
                            FROM server_net_io
                            WHERE server_id = ANY(:sids) AND collected_at >= :since
                              AND collected_at <= now() + interval '2 minutes'
                            GROUP BY server_id, iface_id
                        ) y GROUP BY server_id
                    ) z
                """),
                {"sids": server_ids, "since": since},
            )
        ).scalar_one()
        disk_hosts = (
            await self.session.execute(
                text("""
                    SELECT count(DISTINCT server_id) FROM (
                        SELECT server_id, MAX(count) - MIN(count) AS d
                        FROM server_disk_error
                        WHERE server_id = ANY(:sids) AND collected_at >= :since
                          AND collected_at <= now() + interval '2 minutes'
                        GROUP BY server_id, device_id, error_kind, error_class, member
                    ) w WHERE d > 0
                """),
                {"sids": server_ids, "since": since},
            )
        ).scalar_one()
        return FleetErrorRaw(
            total=int(m.total or 0),
            mce_hosts=int(m.mce_hosts or 0),
            oom_hosts=int(m.oom_hosts or 0),
            corrupted_hosts=int(m.corrupted_hosts or 0),
            net_error_hosts=int(net_hosts or 0),
            disk_error_hosts=int(disk_hosts or 0),
        )

    async def get_fleet_error_hosts(self, server_ids: list[int], since: datetime) -> set[int]:
        if not server_ids:
            return set()
        hosts: set[int] = set()
        m_rows = (
            await self.session.execute(
                text("""
                    SELECT server_id FROM (
                        SELECT server_id,
                            COALESCE(MAX(cpu_mce) - MIN(cpu_mce), 0) AS mce_d,
                            COALESCE(MAX(mem_oom_kill) - MIN(mem_oom_kill), 0) AS oom_d,
                            COALESCE(MAX(mem_hardware_corrupted_bytes), 0) AS corrupted
                        FROM server_metrics
                        WHERE server_id = ANY(:sids) AND collected_at >= :since
                          AND collected_at <= now() + interval '2 minutes'
                        GROUP BY server_id
                    ) x
                    WHERE mce_d > 0 OR oom_d > 0 OR corrupted > 0
                """),
                {"sids": server_ids, "since": since},
            )
        ).scalars()
        hosts.update(m_rows)
        net_rows = (
            await self.session.execute(
                text("""
                    SELECT server_id FROM (
                        SELECT server_id, SUM(d) AS net_d FROM (
                            SELECT server_id, MAX(COALESCE(rx_errors,0)+COALESCE(tx_errors,0))
                                            - MIN(COALESCE(rx_errors,0)+COALESCE(tx_errors,0)) AS d
                            FROM server_net_io
                            WHERE server_id = ANY(:sids) AND collected_at >= :since
                              AND collected_at <= now() + interval '2 minutes'
                            GROUP BY server_id, iface_id
                        ) y GROUP BY server_id
                    ) z WHERE net_d > 0
                """),
                {"sids": server_ids, "since": since},
            )
        ).scalars()
        hosts.update(net_rows)
        disk_rows = (
            await self.session.execute(
                text("""
                    SELECT DISTINCT server_id FROM (
                        SELECT server_id, MAX(count) - MIN(count) AS d
                        FROM server_disk_error
                        WHERE server_id = ANY(:sids) AND collected_at >= :since
                          AND collected_at <= now() + interval '2 minutes'
                        GROUP BY server_id, device_id, error_kind, error_class, member
                    ) w WHERE d > 0
                """),
                {"sids": server_ids, "since": since},
            )
        ).scalars()
        hosts.update(disk_rows)
        return {int(h) for h in hosts}

    async def get_latest_link_speed(self, server_ids: list[int], since: datetime) -> dict[int, dict[str, int]]:
        """서버·iface별 최신 link_speed_bps (bit/s gauge).

        inventory speed_mbps 가 null 인 환경(Windows NT5.2·virtio)에서 엔진이 대신 채우는 폴백 소스 (agent 확정 규약).
        """
        return await self._latest_link_speed(server_ids, since)

    async def get_metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeries]:
        upper = cursor or datetime.now(UTC)
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

    async def get_metric_chart(
        self,
        server_id: int,
        metric_type: MetricType,
        dimension: str | None,
        time_range: TimeRange,
        bucket: BucketSize,
        agg: AggFunc,
        end: datetime | None = None,
        collapse: bool = False,
    ) -> list[MetricSeries]:

        end_dt = end or datetime.now(UTC)
        start = end_dt - TIME_RANGE_TD[time_range]
        return await self.get_metric_trend(
            metric_type,
            start,
            end_dt,
            bucket,
            server_ids=[server_id],
            agg=agg,
            dimension=dimension,
            collapse=collapse,
        )

    async def get_metric_trend(
        self,
        metric_type: MetricType | EnvironmentMetricType,
        start: datetime,
        end: datetime,
        bucket: BucketSize,
        server_ids: list[int] | None = None,
        agg: AggFunc = "avg",
        dimension: str | None = None,
        collapse: bool = True,
    ) -> list[MetricSeries]:
        bi, bucket_td = _BUCKET_INFO[bucket]
        ctx = _TrendCtx(
            metric_type=metric_type,
            bi=bi,
            bucket_td=bucket_td,
            ae=_AGG[agg],
            sid="AND server_id = ANY(:server_ids)" if server_ids else "",
            start=start,
            end=end,
            server_ids=server_ids,
            dimension=dimension,
            collapse=collapse,
        )
        sql, params = _TREND_BUILDERS[metric_type](ctx)

        if server_ids:
            params["server_ids"] = server_ids
        result = await self.session.execute(sql, params)
        return [
            MetricSeries(collected_at=row.ts, value=row.value, dimension=row.dimension, kind=row.kind)
            for row in result.all()
        ]

    async def get_reboot_events(
        self,
        server_id: int,
        start: datetime,
        end: datetime,
    ) -> list[RebootEvent]:
        sql = text("""
            WITH base AS (
                SELECT collected_at, boot_time, agent_started_at,
                    LAG(boot_time)        OVER (ORDER BY collected_at) AS prev_boot,
                    LAG(agent_started_at) OVER (ORDER BY collected_at) AS prev_agent
                FROM server_inventory_history
                WHERE server_id = :sid
                  AND collected_at <= :end
                  AND collected_at >= :buffer_start
            )
            SELECT collected_at, boot_time, agent_started_at,
                CASE
                    WHEN prev_boot IS NULL                            THEN 'reboot'
                    WHEN ABS(EXTRACT(EPOCH FROM (boot_time - prev_boot))) > :jitter_sec THEN 'reboot'
                    WHEN agent_started_at IS DISTINCT FROM prev_agent THEN 'restart'
                    ELSE NULL
                END AS kind
            FROM base
            WHERE collected_at >= :start
              AND (
                  prev_boot IS NULL
                  OR ABS(EXTRACT(EPOCH FROM (boot_time - prev_boot))) > :jitter_sec
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
