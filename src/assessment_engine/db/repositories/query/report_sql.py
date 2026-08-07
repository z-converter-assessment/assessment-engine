"""Report aggregation 도메인 concrete — USE Method 통계 + 환경 활용률.

입력은 raw hypertable 이 아니라 5분 cagg 다 — counter reset 은 `counter_agg` 가 값-감소 기준으로
흡수하므로 여기서 LAG·boot_time gate 를 되살리지 않는다.
"""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text

from assessment_engine.db.dtos.outbound import (
    CpuBreakdownRaw,
    DiskIoBaselineRaw,
    EnvironmentUtilizationRaw,
    MemoryBreakdownRaw,
    MountCapacityRaw,
    NetIoBaselineRaw,
    ReportRowRaw,
)
from assessment_engine.db.repositories.query._base import _BaseQueryMixin
from assessment_engine.db.repositories.query.types import (
    _DATA_VOLUME_CAGG_FILTER,
    _PHYS_DISK_SQL_FILTER,
    _PHYS_IFACE_SQL_FILTER,
)
from assessment_engine.domain import right_sizing  # 순수 도메인 커널 — right-sizing 정책 상수(순환 없음)

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject


class SqlReportQueryRepository(_BaseQueryMixin):
    async def get_report_aggregate(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> list[ReportRowRaw]:
        """N서버 x period_days 통계 -> ReportRowRaw list. 표시 파생은 service 몫이다.

        server_inventory 에서 LEFT JOIN 하므로 metric 이 하나도 없는 서버도 행이 나온다 (호출부 N+1 회피).
        """
        start = end - timedelta(days=period_days)

        sql = text(f"""
            WITH bkt AS (
                SELECT server_id, bucket,
                    CASE WHEN delta(cpu_total_ca) > 0
                         THEN GREATEST(0, (1 - delta(cpu_idle_ca) / delta(cpu_total_ca)) * 100) END AS cpu_pct,
                    CASE WHEN delta(cpu_total_ca) > 0
                         THEN GREATEST(0, delta(cpu_iowait_ca) / delta(cpu_total_ca) * 100) END AS iowait_pct,
                    CASE WHEN delta(cpu_total_ca) > 0
                         THEN GREATEST(0, delta(cpu_steal_ca) / delta(cpu_total_ca) * 100) END AS steal_pct,
                    -- Windows Pages Input/sec(하드폴트 counter) -> delta/time_delta rate. Linux 는 paging_in null.
                    CASE WHEN time_delta(paging_in_ca) > 0
                         THEN GREATEST(0, delta(paging_in_ca) / time_delta(paging_in_ca)) END AS pages_input_rate,
                    blocked_avg   AS procs_blocked,
                    run_queue_avg AS procs_running,
                    delta(paging_major_ca) AS paging_major_delta,  -- Linux 메모리 포화 dual-gate 입력(refault)
                    delta(oom_kill_ca)     AS oom_delta,
                    delta(tcp_retrans_ca)  AS retrans_delta,
                    conntrack_ratio_max AS conntrack_ratio,
                    mem_pct_avg, mem_pct_max
                FROM server_metrics_5m
                WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
            ),
            cpu_stats AS (
                SELECT server_id,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY cpu_pct) AS cpu_p95,
                    percentile_cont(0.5)  WITHIN GROUP (ORDER BY cpu_pct) AS cpu_median,
                    AVG(cpu_pct) AS cpu_avg, MAX(cpu_pct) AS cpu_peak, COUNT(cpu_pct) AS cpu_sample,
                    COUNT(*) AS total_buckets,  -- 메트릭 종류 독립 관측 버킷수 — history_hours 산출(Windows CPU util NULL 무관)
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY iowait_pct) AS iowait_p95,
                    MAX(iowait_pct) AS iowait_peak,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY procs_running) AS cpu_run_queue_p95,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY pages_input_rate) AS mem_pages_input_rate_p95
                FROM bkt GROUP BY server_id
            ),
            mem_stats AS (
                SELECT server_id,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY mem_pct_avg) AS mem_p95,
                    -- near-peak = 버킷 최댓값(mem_pct_max)의 p95 — 메모리 사이징 통계(비탄력 피크 대표). p99.9 는
                    -- 표본 ~210 에서 절대 max 와 사실상 동일해(단일 5분 스파이크 지배) 견고 못 함 -> p95 로 이상치 제외.
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY mem_pct_max) AS mem_near_peak,
                    AVG(mem_pct_avg) AS mem_avg, MAX(mem_pct_max) AS mem_peak, COUNT(mem_pct_avg) AS mem_sample
                FROM bkt WHERE mem_pct_avg IS NOT NULL GROUP BY server_id
            ),
            rs_stats AS (
                SELECT server_id,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY steal_pct)     AS cpu_steal_p95,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY procs_blocked) AS procs_blocked_p95,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY procs_running) AS procs_running_p95,
                    bool_or(COALESCE(paging_major_delta, 0) > 0) AS swap_paging,  -- Linux paging_major(refault) 발생
                    bool_or(COALESCE(oom_delta, 0) > 0) AS oom_occurred,
                    SUM(retrans_delta) AS retrans_total,
                    MAX(conntrack_ratio) AS conntrack_ratio,
                    regr_slope(cpu_pct, extract(epoch FROM bucket)) * 86400 AS cpu_trend_slope,
                    regr_slope(mem_pct_avg, extract(epoch FROM bucket)) * 86400 AS mem_trend_slope
                FROM bkt GROUP BY server_id
            ),
            mount_max_raw AS (
                -- 마운트별 period 안 최대 used%/inode%. server_filesystem_5m, 가상 fs/boot 제외.
                SELECT server_id, mountpoint,
                    MAX(used_pct_max) AS used_pct_max, MAX(inode_pct_max) AS inode_pct_max
                FROM server_filesystem_5m
                WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end AND {_DATA_VOLUME_CAGG_FILTER}
                GROUP BY server_id, mountpoint
            ),
            mount_max AS (
                -- 서버 worst mount used%/inode% (각각 독립 MAX — 다른 마운트일 수 있음) + used% 를 낸 마운트
                -- 이름(worst_mount, 14일 카드 "사용률" 행 표기용 — 도넛 집계%와 다른 worst-mount 산식임을 명시).
                -- 동률이면 mountpoint 오름차순 결정적 선택(LATERAL, 부동소수 값 자체 self-match).
                SELECT agg.server_id, agg.worst_used_pct, agg.worst_inode_used_pct, wm.mountpoint AS worst_mount
                FROM (
                    SELECT server_id, MAX(used_pct_max) AS worst_used_pct, MAX(inode_pct_max) AS worst_inode_used_pct
                    FROM mount_max_raw GROUP BY server_id
                ) agg
                LEFT JOIN LATERAL (
                    SELECT mountpoint FROM mount_max_raw m
                    WHERE m.server_id = agg.server_id AND m.used_pct_max = agg.worst_used_pct
                    ORDER BY mountpoint ASC LIMIT 1
                ) wm ON true
            ),
            mount_span AS (
                -- 용량 runway 입력 — 마운트별 가용 이력 전체(하한 없이 bucket <= :end)의 free/inode 시작·종료 + 총량 + span.
                SELECT server_id, mountpoint,
                    first(free_first, bucket)       AS av_first,
                    last(free_last, bucket)         AS av_last,
                    max(total_bytes_max)            AS total_bytes,
                    first(inode_free_first, bucket) AS in_first,
                    last(inode_free_last, bucket)   AS in_last,
                    EXTRACT(EPOCH FROM (max(bucket) - min(bucket))) / 86400.0 AS span_days
                FROM server_filesystem_5m
                WHERE server_id = ANY(:sids) AND bucket <= :end AND {_DATA_VOLUME_CAGG_FILTER}
                GROUP BY server_id, mountpoint
            ),
            mount_calc AS (
                SELECT server_id, mountpoint,
                    CASE WHEN (av_first - av_last) > 0 AND span_days >= :rate_min_span AND av_last >= 0
                         THEN GREATEST(0, FLOOR(av_last / ((av_first - av_last) / span_days))) END AS disk_runway_days,
                    CASE WHEN (in_first - in_last) > 0 AND span_days >= :rate_min_span AND in_last >= 0
                         THEN GREATEST(0, FLOOR(in_last / ((in_first - in_last) / span_days))) END AS inode_runway_days,
                    CASE
                        WHEN (av_first - av_last) > 0 AND span_days >= :trend_min_span AND total_bytes > 0
                            THEN CEIL(((total_bytes - av_last) + :target_runway * ((av_first - av_last) / span_days)) / 1e9)
                        WHEN (av_first - av_last) > 0 AND span_days >= :rate_min_span AND total_bytes > 0
                            THEN CEIL(((total_bytes - av_last) + :near_horizon * ((av_first - av_last) / span_days))
                                      / (:headroom_pct / 100.0) / 1e9)
                        WHEN total_bytes > 0 AND av_last >= 0 AND (1 - av_last::float / total_bytes) * 100 >= :static_pct
                            THEN CEIL((total_bytes - av_last) / (:headroom_pct / 100.0) / 1e9)
                    END AS target_gb,
                    CASE WHEN total_bytes > 0 THEN (1 - av_last::float / total_bytes) * 100 END AS used_pct,
                    CASE WHEN (av_first - av_last) > 0 AND span_days >= :rate_min_span AND total_bytes > 0
                         THEN (1 - (av_last - :near_horizon * ((av_first - av_last) / span_days)) / total_bytes::float) * 100
                    END AS proj_30d_pct
                FROM mount_span
            ),
            mount_runway AS (
                SELECT r.server_id, r.disk_runway_days, r.inode_runway_days,
                       t.mountpoint AS driving_mount, t.target_gb, t.proj_30d_pct, t.used_pct
                FROM (
                    SELECT server_id, MIN(disk_runway_days) AS disk_runway_days, MIN(inode_runway_days) AS inode_runway_days
                    FROM mount_calc GROUP BY server_id
                ) r
                LEFT JOIN (
                    SELECT DISTINCT ON (server_id) server_id, mountpoint, target_gb, proj_30d_pct, used_pct
                    FROM mount_calc WHERE disk_runway_days IS NOT NULL OR used_pct >= :static_pct
                    ORDER BY server_id, disk_runway_days ASC NULLS LAST, used_pct DESC NULLS LAST
                ) t ON t.server_id = r.server_id
            ),
            disk_dev AS (
                -- server_disk_io_5m 단일 스캔(B1) — await·iops baseline 공용 per-device 델타. 물리필터 1회 평가.
                -- 2회 참조라 PG12+ 가 기본 materialize -> cagg 스캔·PHYS 필터가 각 1회로 끝난다.
                SELECT server_id, bucket,
                    delta(io_time_ca)                        AS d_io_time,
                    time_delta(io_time_ca)                   AS td_io_time,
                    delta(op_rtime_ca) + delta(op_wtime_ca)  AS d_optime,
                    delta(ops_read_ca) + delta(ops_write_ca) AS d_ops,
                    time_delta(ops_read_ca)                  AS td_ops
                FROM server_disk_io_5m
                WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end AND {_PHYS_DISK_SQL_FILTER}
            ),
            disk_await AS (
                -- await(ms) = sum(delta(op_time)) / sum(delta(ops)). device 가 실제 바쁜(io_time 사용률 >= :diskio_util_min)
                -- 버킷만 — 유휴 device 의 tick 기반 await 는 writeback 큐 잔류로 폭증하나 병목 아님(util AND await).
                SELECT server_id, bucket, MAX(await_ms) AS worst_await FROM (
                    SELECT server_id, bucket,
                        CASE WHEN d_io_time / NULLIF(td_io_time, 0) >= :diskio_util_min
                             THEN d_optime / NULLIF(d_ops, 0) * 1000 END AS await_ms
                    FROM disk_dev
                ) d WHERE await_ms IS NOT NULL GROUP BY server_id, bucket
            ),
            disk_await_stats AS (
                SELECT server_id, percentile_cont(0.95) WITHIN GROUP (ORDER BY worst_await) AS await_p95
                FROM disk_await GROUP BY server_id
            ),
            disk_io_base AS (
                -- 서버 iops baseline = sum(delta(ops)) / sum(dt). 유휴 판정 활동 축(idle 게이트, _host_status) 입력.
                SELECT server_id, CASE WHEN SUM(dt) > 0 THEN SUM(ops) / SUM(dt) END AS iops_baseline FROM (
                    SELECT server_id, bucket, SUM(d_ops) AS ops, MAX(td_ops) AS dt
                    FROM disk_dev GROUP BY server_id, bucket
                ) pb WHERE dt > 0 GROUP BY server_id
            ),
            net_quality AS (
                SELECT server_id, SUM(rxd) + SUM(txd) AS drops_total,
                    SUM(rxp) + SUM(txp) AS packets_total, SUM(txp) AS tx_packets_total
                FROM (
                    SELECT server_id, delta(rxd_ca) AS rxd, delta(txd_ca) AS txd,
                        delta(rxp_ca) AS rxp, delta(txp_ca) AS txp
                    FROM server_net_io_5m
                    WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end AND {_PHYS_IFACE_SQL_FILTER}
                ) n GROUP BY server_id
            ),
            percore AS (
                SELECT server_id, core_id, percentile_cont(0.95) WITHIN GROUP (ORDER BY core_util) AS core_p95
                FROM (
                    SELECT server_id, core_id,
                        CASE WHEN delta(cpu_total_ca) > 0
                             THEN GREATEST(0, (1 - delta(cpu_idle_ca) / delta(cpu_total_ca)) * 100) END AS core_util
                    FROM server_cpu_core_5m
                    WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
                ) cu WHERE core_util IS NOT NULL GROUP BY server_id, core_id
            ),
            percore_max AS (
                SELECT server_id, MAX(core_p95) AS cpu_percore_p95_max FROM percore GROUP BY server_id
            )
            SELECT
                s.id AS server_id, s.public_id, s.hostname, s.os_family, s.os_id, s.os_version,
                s.os_codename, s.kernel_version,
                s.net_interfaces, s.services, s.listen_ports, s.last_seen_at, s.cpu_cores, s.mem_total_bytes,
                s.block_devices, s.lvm_vgs, s.boot_time,
                s.arch, s.bits, s.boot_firmware, s.secure_boot, s.edition, s.product_name, s.timezone, s.rtc_utc,
                s.boot, s.nonblock_mounts,
                cs.cpu_p95, cs.cpu_avg, cs.cpu_peak, cs.iowait_p95, cs.iowait_peak,
                cs.cpu_run_queue_p95, cs.mem_pages_input_rate_p95,
                ms.mem_p95, ms.mem_avg, ms.mem_peak, ms.mem_near_peak,
                mm.worst_used_pct AS worst_mount_used_pct,
                mm.worst_mount AS disk_capacity_worst_mount,
                cs.cpu_sample::float / NULLIF(:expected_samples, 0) AS cpu_sufficiency,
                ms.mem_sample::float / NULLIF(:expected_samples, 0) AS mem_sufficiency,
                rs.cpu_steal_p95,
                CASE WHEN cs.cpu_median > 0 THEN cs.cpu_p95 / cs.cpu_median END AS cpu_burst_ratio,
                rs.procs_blocked_p95, rs.procs_running_p95,
                COALESCE(rs.swap_paging, false) AS swap_paging,
                COALESCE(rs.oom_occurred, false) AS oom_occurred,
                cs.total_buckets * 5.0 / 60.0 AS history_hours,
                da.await_p95 AS disk_await_p95_ms,
                dib.iops_baseline AS disk_iops_baseline,
                mm.worst_inode_used_pct AS disk_inode_used_pct,
                rs.conntrack_ratio,
                mr.disk_runway_days  AS disk_capacity_runway_days,
                mr.inode_runway_days AS disk_inode_runway_days,
                mr.driving_mount     AS disk_capacity_driving_mount,
                mr.target_gb         AS disk_capacity_target_gb,
                mr.proj_30d_pct      AS disk_capacity_proj_30d_pct,
                mr.used_pct          AS disk_capacity_driving_used_pct,
                nq.drops_total  / NULLIF(nq.packets_total, 0)    * 100 AS net_drop_pct,
                rs.retrans_total / NULLIF(nq.tx_packets_total, 0) * 100 AS net_retrans_pct,
                rs.cpu_trend_slope, rs.mem_trend_slope, pc.cpu_percore_p95_max
            FROM server_inventory s
            LEFT JOIN cpu_stats   cs ON cs.server_id = s.id
            LEFT JOIN mem_stats   ms ON ms.server_id = s.id
            LEFT JOIN rs_stats    rs ON rs.server_id = s.id
            LEFT JOIN mount_max   mm ON mm.server_id = s.id
            LEFT JOIN mount_runway mr ON mr.server_id = s.id
            LEFT JOIN disk_await_stats da ON da.server_id = s.id
            LEFT JOIN disk_io_base dib ON dib.server_id = s.id
            LEFT JOIN net_quality nq ON nq.server_id = s.id
            LEFT JOIN percore_max pc ON pc.server_id = s.id
            WHERE s.id = ANY(:sids)
            ORDER BY s.hostname
        """)
        result = await self.session.execute(
            sql,
            {
                "sids": server_ids,
                "start": start,
                "end": end,
                "expected_samples": period_days * 288,
                "target_runway": right_sizing.DISK_TARGET_RUNWAY_DAYS,
                "trend_min_span": right_sizing.DISK_TREND_MIN_SPAN_DAYS,
                "rate_min_span": right_sizing.DISK_RATE_MIN_SPAN_DAYS,
                "diskio_util_min": right_sizing.DISKIO_UTIL_MIN,
                "near_horizon": right_sizing.DISK_NEAR_HORIZON_DAYS,
                "static_pct": right_sizing.DISK_STATIC_GUARD_PCT,
                "headroom_pct": right_sizing.DISK_HEADROOM_TARGET_PCT,
            },
        )

        return [
            ReportRowRaw(
                server_id=r.server_id,
                public_id=str(r.public_id),
                hostname=r.hostname,
                os_family=r.os_family,
                os_id=r.os_id,
                os_version=r.os_version,
                os_codename=r.os_codename,
                kernel_version=r.kernel_version,
                net_interfaces=r.net_interfaces,
                services=r.services,
                listen_ports=r.listen_ports,
                last_seen_at=r.last_seen_at,
                cpu_p95_pct=r.cpu_p95,
                cpu_avg_pct=r.cpu_avg,
                cpu_peak_pct=r.cpu_peak,
                mem_p95_pct=r.mem_p95,
                mem_avg_pct=r.mem_avg,
                mem_peak_pct=r.mem_peak,
                mem_near_peak_pct=r.mem_near_peak,
                iowait_p95_pct=r.iowait_p95,
                iowait_peak_pct=r.iowait_peak,
                cpu_run_queue_p95=r.cpu_run_queue_p95,
                mem_pages_input_rate_p95=r.mem_pages_input_rate_p95,
                cpu_cores=r.cpu_cores,
                mem_total_bytes=r.mem_total_bytes,
                block_devices=r.block_devices,
                lvm_vgs=r.lvm_vgs,
                boot_time=r.boot_time,
                arch=r.arch,
                bits=r.bits,
                boot_firmware=r.boot_firmware,
                secure_boot=r.secure_boot,
                edition=r.edition,
                product_name=r.product_name,
                timezone=r.timezone,
                rtc_utc=r.rtc_utc,
                boot=r.boot,
                nonblock_mounts=r.nonblock_mounts,
                worst_mount_used_pct=r.worst_mount_used_pct,
                disk_capacity_worst_mount=r.disk_capacity_worst_mount,
                cpu_sufficiency=r.cpu_sufficiency,
                mem_sufficiency=r.mem_sufficiency,
                cpu_steal_p95_pct=r.cpu_steal_p95,
                cpu_burst_ratio=r.cpu_burst_ratio,
                procs_blocked_p95=r.procs_blocked_p95,
                procs_running_p95=r.procs_running_p95,
                mem_swap_paging=bool(r.swap_paging),
                oom_occurred=bool(r.oom_occurred),
                history_hours=r.history_hours,
                disk_await_p95_ms=r.disk_await_p95_ms,
                disk_iops_baseline=int(r.disk_iops_baseline) if r.disk_iops_baseline is not None else None,
                disk_capacity_runway_days=r.disk_capacity_runway_days,
                disk_capacity_driving_mount=r.disk_capacity_driving_mount,
                disk_capacity_target_gb=r.disk_capacity_target_gb,
                disk_capacity_proj_30d_pct=r.disk_capacity_proj_30d_pct,
                disk_capacity_driving_used_pct=r.disk_capacity_driving_used_pct,
                disk_inode_runway_days=r.disk_inode_runway_days,
                disk_inode_used_pct=r.disk_inode_used_pct,
                net_drop_pct=r.net_drop_pct,
                net_retrans_pct=r.net_retrans_pct,
                conntrack_ratio=r.conntrack_ratio,
                cpu_trend_slope=r.cpu_trend_slope,
                mem_trend_slope=r.mem_trend_slope,
                cpu_percore_p95_max=r.cpu_percore_p95_max,
            )
            for r in result.all()
        ]

    async def get_report_uptime_stats(self, server_ids: list[int], period_days: float, end: datetime) -> dict[int, int]:
        """server_id -> period 안 재부팅 횟수. 현재 boot_time 도 DISTINCT 에 들어오므로 -1."""
        start = end - timedelta(days=period_days)
        sql = text("""
            SELECT server_id, GREATEST(0, COUNT(DISTINCT boot_time) - 1) AS reboot_count
            FROM server_inventory_history
            WHERE server_id = ANY(:sids) AND collected_at >= :start AND collected_at <= :end AND boot_time IS NOT NULL
            GROUP BY server_id
        """)
        result = await self.session.execute(sql, {"sids": server_ids, "start": start, "end": end})
        return {r.server_id: int(r.reboot_count) for r in result.all()}

    async def get_report_agent_restart_stats(
        self, server_ids: list[int], period_days: float, end: datetime
    ) -> dict[int, int]:
        """server_id -> period 안 agent 재시작 횟수. `get_report_uptime_stats` 와 동일 산식."""
        start = end - timedelta(days=period_days)
        sql = text("""
            SELECT server_id, GREATEST(0, COUNT(DISTINCT agent_started_at) - 1) AS restart_count
            FROM server_inventory_history
            WHERE server_id = ANY(:sids) AND collected_at >= :start AND collected_at <= :end
              AND agent_started_at IS NOT NULL
            GROUP BY server_id
        """)
        result = await self.session.execute(sql, {"sids": server_ids, "start": start, "end": end})
        return {r.server_id: int(r.restart_count) for r in result.all()}

    async def get_agent_restart_counts_recent(self, server_ids: list[int], since: datetime) -> dict[int, int]:
        """since 이후 server 별 agent 재시작 횟수 — attention agent_unstable 의 고정 윈도우.

        consumer 가 쌓는 Redis sliding 카운터를 읽지 않고 DB 에서 다시 세는 자리다.
        """
        if not server_ids:
            return {}
        sql = text("""
            SELECT server_id, GREATEST(0, COUNT(DISTINCT agent_started_at) - 1) AS restart_count
            FROM server_inventory_history
            WHERE server_id = ANY(:sids) AND collected_at >= :since AND agent_started_at IS NOT NULL
            GROUP BY server_id
        """)
        result = await self.session.execute(sql, {"sids": server_ids, "since": since})
        return {r.server_id: int(r.restart_count) for r in result.all()}

    async def get_report_disk_io_baseline(
        self, server_ids: list[int], period_days: float, end: datetime
    ) -> dict[int, DiskIoBaselineRaw]:
        """server_id -> DiskIoBaselineRaw. throughput 단위는 kB/s, iops 는 회/s."""
        start = end - timedelta(days=period_days)
        sql = text(f"""
            WITH per_dev AS (
                SELECT server_id, bucket,
                    delta(ops_read_ca) + delta(ops_write_ca)  AS ops,
                    delta(io_read_ca) + delta(io_write_ca)    AS bytes,
                    time_delta(ops_read_ca)                   AS dt
                FROM server_disk_io_5m
                WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end AND {_PHYS_DISK_SQL_FILTER}
            ),
            per_bucket AS (
                SELECT server_id, bucket, SUM(ops) AS ops, SUM(bytes) AS bytes, MAX(dt) AS dt
                FROM per_dev WHERE dt > 0 GROUP BY server_id, bucket
            ),
            disk_baseline AS (
                SELECT server_id, SUM(ops) AS total_ops, SUM(bytes) AS total_bytes, SUM(dt) AS total_seconds
                FROM per_bucket GROUP BY server_id
            ),
            disk_stats AS (
                SELECT server_id,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY ops / dt) AS iops_p95,
                    MAX(ops / dt) AS iops_peak,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY bytes / dt / 1024) AS kbps_p95,
                    MAX(bytes / dt / 1024) AS kbps_peak
                FROM per_bucket WHERE dt > 0 GROUP BY server_id
            )
            SELECT b.server_id,
                CASE WHEN b.total_seconds > 0 THEN b.total_ops / b.total_seconds END AS iops_baseline,
                CASE WHEN b.total_seconds > 0 THEN b.total_bytes / b.total_seconds / 1024 END AS throughput_kbps_baseline,
                s.iops_p95, s.iops_peak, s.kbps_p95, s.kbps_peak
            FROM disk_baseline b LEFT JOIN disk_stats s ON s.server_id = b.server_id
        """)
        result = await self.session.execute(sql, {"sids": server_ids, "start": start, "end": end})
        return {
            r.server_id: DiskIoBaselineRaw(
                iops_baseline=int(r.iops_baseline) if r.iops_baseline is not None else None,
                throughput_kbps_baseline=(
                    float(r.throughput_kbps_baseline) if r.throughput_kbps_baseline is not None else None
                ),
                iops_p95=float(r.iops_p95) if r.iops_p95 is not None else None,
                iops_peak=float(r.iops_peak) if r.iops_peak is not None else None,
                kbps_p95=float(r.kbps_p95) if r.kbps_p95 is not None else None,
                kbps_peak=float(r.kbps_peak) if r.kbps_peak is not None else None,
            )
            for r in result.all()
        }

    async def get_report_net_io_baseline(
        self, server_ids: list[int], period_days: float, end: datetime
    ) -> dict[int, NetIoBaselineRaw]:
        """server_id -> NetIoBaselineRaw. rx·tx 단위는 kB/s."""
        start = end - timedelta(days=period_days)
        sql = text(f"""
            WITH per_if AS (
                SELECT server_id, bucket,
                    delta(rx_ca) AS rx_bytes, delta(tx_ca) AS tx_bytes, time_delta(rx_ca) AS dt
                FROM server_net_io_5m
                WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end AND {_PHYS_IFACE_SQL_FILTER}
            ),
            per_bucket AS (
                SELECT server_id, bucket, SUM(rx_bytes) AS rx_bytes, SUM(tx_bytes) AS tx_bytes, MAX(dt) AS dt
                FROM per_if WHERE dt > 0 GROUP BY server_id, bucket
            ),
            net_baseline AS (
                SELECT server_id, SUM(rx_bytes) AS total_rx_bytes, SUM(tx_bytes) AS total_tx_bytes,
                    SUM(dt) AS total_seconds
                FROM per_bucket GROUP BY server_id
            ),
            net_stats AS (
                SELECT server_id,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY rx_bytes / dt / 1024) AS rx_p95,
                    MAX(rx_bytes / dt / 1024) AS rx_peak,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY tx_bytes / dt / 1024) AS tx_p95,
                    MAX(tx_bytes / dt / 1024) AS tx_peak
                FROM per_bucket WHERE dt > 0 GROUP BY server_id
            )
            SELECT b.server_id,
                CASE WHEN b.total_seconds > 0 THEN b.total_rx_bytes / b.total_seconds / 1024 END AS rx_kbps_baseline,
                CASE WHEN b.total_seconds > 0 THEN b.total_tx_bytes / b.total_seconds / 1024 END AS tx_kbps_baseline,
                s.rx_p95, s.rx_peak, s.tx_p95, s.tx_peak
            FROM net_baseline b LEFT JOIN net_stats s ON s.server_id = b.server_id
        """)
        result = await self.session.execute(sql, {"sids": server_ids, "start": start, "end": end})
        return {
            r.server_id: NetIoBaselineRaw(
                rx_kbps_baseline=float(r.rx_kbps_baseline) if r.rx_kbps_baseline is not None else None,
                tx_kbps_baseline=float(r.tx_kbps_baseline) if r.tx_kbps_baseline is not None else None,
                rx_p95=float(r.rx_p95) if r.rx_p95 is not None else None,
                rx_peak=float(r.rx_peak) if r.rx_peak is not None else None,
                tx_p95=float(r.tx_p95) if r.tx_p95 is not None else None,
                tx_peak=float(r.tx_peak) if r.tx_peak is not None else None,
            )
            for r in result.all()
        }

    async def get_environment_utilization(
        self, period_days: float, end: datetime, server_ids: list[int] | None = None
    ) -> EnvironmentUtilizationRaw:
        """환경(또는 선택 N대) capacity-weighted 평균 활용률 — 대수 평균이 아니라 sum(used)/sum(total).

        cpu 는 seconds 에 코어 수가 이미 내재해 합만으로 코어 가중이 된다 (따로 곱하지 않는다).
        disk 는 p95 를 내지 않는다 — Windows 물리 디스크 인식이 불완전하다. period_days 는 30일로 cap 한다.
        """
        capped = min(max(period_days, 0.0), 30)
        start = end - timedelta(days=capped)
        sid = " AND server_id = ANY(:sids)" if server_ids else ""
        sql = text(f"""
            WITH cpu_bkt AS (
                SELECT bucket, delta(cpu_idle_ca) AS d_idle, delta(cpu_total_ca) AS d_total
                FROM server_metrics_5m WHERE bucket >= :start AND bucket <= :end{sid}
            ),
            cpu_valid AS (
                SELECT bucket, d_idle, d_total FROM cpu_bkt WHERE d_total > 0 AND d_idle IS NOT NULL
            ),
            cpu_per_ts AS (
                SELECT GREATEST(0, (1 - SUM(d_idle)::float / SUM(d_total)) * 100) AS v
                FROM cpu_valid GROUP BY bucket HAVING SUM(d_total) > 0
            ),
            mem_per_ts AS (
                -- capacity-weighted mem% per bucket 을 cagg byte gauge 에서 낸다 (raw hypertable 스캔 회피).
                SELECT SUM(mem_limit_avg - mem_available_avg) / NULLIF(SUM(mem_limit_avg), 0) * 100 AS v
                FROM server_metrics_5m
                WHERE bucket >= :start AND bucket <= :end{sid}
                  AND mem_limit_avg > 0 AND mem_available_avg IS NOT NULL
                GROUP BY bucket
            )
            SELECT
                (SELECT CASE WHEN SUM(d_total) > 0
                             THEN GREATEST(0, (1 - SUM(d_idle)::float / SUM(d_total)) * 100) END FROM cpu_valid) AS cpu_avg,
                (SELECT CASE WHEN SUM(mem_limit_avg) > 0
                             THEN SUM(mem_limit_avg - mem_available_avg) / SUM(mem_limit_avg) * 100 END
                 FROM server_metrics_5m
                 WHERE bucket >= :start AND bucket <= :end{sid}
                   AND mem_limit_avg > 0 AND mem_available_avg IS NOT NULL) AS mem_avg,
                (SELECT CASE WHEN SUM(total_bytes_max) > 0
                             THEN SUM(total_bytes_max * used_pct_avg / 100) / SUM(total_bytes_max) * 100 END
                 FROM server_filesystem_5m
                 WHERE bucket >= :start AND bucket <= :end{sid}
                   AND {_DATA_VOLUME_CAGG_FILTER}
                   AND total_bytes_max > 0 AND used_pct_avg IS NOT NULL) AS disk_avg,
                (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY v) FROM cpu_per_ts) AS cpu_p95,
                (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY v) FROM mem_per_ts WHERE v IS NOT NULL) AS mem_p95,
                (SELECT COUNT(DISTINCT server_id) FROM server_metrics_5m
                 WHERE bucket >= :start AND bucket <= :end{sid}) AS sample_size
        """)
        params: JsonObject = {"start": start, "end": end}
        if server_ids:
            params["sids"] = server_ids
        result = await self.session.execute(sql, params)
        row = result.one()
        return EnvironmentUtilizationRaw(
            cpu_avg_pct=float(row.cpu_avg) if row.cpu_avg is not None else None,
            mem_avg_pct=float(row.mem_avg) if row.mem_avg is not None else None,
            disk_avg_pct=float(row.disk_avg) if row.disk_avg is not None else None,
            sample_size=int(row.sample_size or 0),
            cpu_p95_pct=float(row.cpu_p95) if row.cpu_p95 is not None else None,
            mem_p95_pct=float(row.mem_p95) if row.mem_p95 is not None else None,
        )

    async def get_report_memory_breakdown(
        self, server_id: int, period_days: float, end: datetime
    ) -> MemoryBreakdownRaw:
        """메모리 구성 윈도우 평균. 데이터 없으면 전 축 None."""
        return (await self.get_report_memory_breakdown_batch([server_id], period_days, end)).get(
            server_id, MemoryBreakdownRaw(None, None, None, None)
        )

    async def get_report_cpu_breakdown(self, server_id: int, period_days: float, end: datetime) -> CpuBreakdownRaw:
        """CPU 분류 윈도우 평균. 데이터 없으면 전 축 None."""
        return (await self.get_report_cpu_breakdown_batch([server_id], period_days, end)).get(
            server_id, CpuBreakdownRaw(None, None, None)
        )

    async def get_report_mount_capacity_batch(
        self, server_ids: list[int], end: datetime
    ) -> dict[int, list[MountCapacityRaw]]:
        """마운트별 용량 사이징 입력. `get_report_aggregate` 와 달리 호스트 worst-mount 로 접지 않는다.

        프로비저닝은 볼륨마다 따로 사이징해야 하므로 마운트 행을 그대로 돌려준다. runway/target 산식
        자체는 `get_report_aggregate` mount_calc 와 동일하다. target_bytes 는 목표 총 용량(bytes, None
        이면 확장 불필요). runway 는 사이징 창이 아니라 가용 이력 전체 span 기준이다 — 누적 신호라
        길수록 정확하다.
        """
        sql = text(f"""
            WITH mount_span AS (
                SELECT server_id, mountpoint,
                    first(free_first, bucket)       AS av_first,
                    last(free_last, bucket)         AS av_last,
                    max(total_bytes_max)            AS total_bytes,
                    first(inode_free_first, bucket) AS in_first,
                    last(inode_free_last, bucket)   AS in_last,
                    max(inode_pct_max)              AS inode_used_pct,
                    EXTRACT(EPOCH FROM (max(bucket) - min(bucket))) / 86400.0 AS span_days
                FROM server_filesystem_5m
                WHERE server_id = ANY(:sids) AND bucket <= :end AND {_DATA_VOLUME_CAGG_FILTER}
                GROUP BY server_id, mountpoint
            )
            SELECT server_id, mountpoint, total_bytes, inode_used_pct,
                CASE WHEN total_bytes > 0 THEN (1 - av_last::float / total_bytes) * 100 END AS used_pct,
                CASE WHEN (av_first - av_last) > 0 AND span_days >= :rate_min_span AND av_last >= 0
                     THEN GREATEST(0, FLOOR(av_last / ((av_first - av_last) / span_days))) END AS byte_runway_days,
                CASE WHEN (in_first - in_last) > 0 AND span_days >= :rate_min_span AND in_last >= 0
                     THEN GREATEST(0, FLOOR(in_last / ((in_first - in_last) / span_days))) END AS inode_runway_days,
                CASE
                    WHEN (av_first - av_last) > 0 AND span_days >= :trend_min_span AND total_bytes > 0
                        THEN CEIL((total_bytes - av_last) + :target_runway * ((av_first - av_last) / span_days))
                    WHEN (av_first - av_last) > 0 AND span_days >= :rate_min_span AND total_bytes > 0
                        THEN CEIL(((total_bytes - av_last) + :near_horizon * ((av_first - av_last) / span_days))
                                  / (:headroom_pct / 100.0))
                    WHEN total_bytes > 0 AND av_last >= 0 AND (1 - av_last::float / total_bytes) * 100 >= :static_pct
                        THEN CEIL((total_bytes - av_last) / (:headroom_pct / 100.0))
                END AS target_bytes
            FROM mount_span
            ORDER BY server_id, mountpoint
        """)
        result = await self.session.execute(
            sql,
            {
                "sids": server_ids,
                "end": end,
                "target_runway": right_sizing.DISK_TARGET_RUNWAY_DAYS,
                "trend_min_span": right_sizing.DISK_TREND_MIN_SPAN_DAYS,
                "rate_min_span": right_sizing.DISK_RATE_MIN_SPAN_DAYS,
                "near_horizon": right_sizing.DISK_NEAR_HORIZON_DAYS,
                "static_pct": right_sizing.DISK_STATIC_GUARD_PCT,
                "headroom_pct": right_sizing.DISK_HEADROOM_TARGET_PCT,
            },
        )
        out: dict[int, list[MountCapacityRaw]] = {}
        for r in result.all():
            out.setdefault(r.server_id, []).append(
                MountCapacityRaw(
                    mountpoint=r.mountpoint,
                    total_bytes=int(r.total_bytes) if r.total_bytes is not None else None,
                    used_pct=float(r.used_pct) if r.used_pct is not None else None,
                    byte_runway_days=float(r.byte_runway_days) if r.byte_runway_days is not None else None,
                    inode_runway_days=float(r.inode_runway_days) if r.inode_runway_days is not None else None,
                    inode_used_pct=float(r.inode_used_pct) if r.inode_used_pct is not None else None,
                    target_bytes=int(r.target_bytes) if r.target_bytes is not None else None,
                )
            )
        return out

    async def get_report_memory_breakdown_batch(
        self, server_ids: list[int], period_days: float, end: datetime
    ) -> dict[int, MemoryBreakdownRaw]:
        """`get_report_memory_breakdown` 배치."""
        start = end - timedelta(days=period_days)
        # 버킷 avg 를 다시 창 avg 로 접는다 — mem_pct_avg 규약과 동형.
        sql = text("""
            SELECT server_id,
                avg(mem_pct_avg) AS used_pct,
                100 - avg(mem_pct_avg) AS available_pct,
                avg(mem_cached_pct_avg) AS cached_pct,
                avg(mem_buffered_pct_avg) AS buffers_pct
            FROM server_metrics_5m
            WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
            GROUP BY server_id
        """)
        result = await self.session.execute(sql, {"sids": server_ids, "start": start, "end": end})
        return {
            r.server_id: MemoryBreakdownRaw(
                used_pct=float(r.used_pct) if r.used_pct is not None else None,
                available_pct=float(r.available_pct) if r.available_pct is not None else None,
                cached_pct=float(r.cached_pct) if r.cached_pct is not None else None,
                buffers_pct=float(r.buffers_pct) if r.buffers_pct is not None else None,
            )
            for r in result.all()
        }

    async def get_report_cpu_breakdown_batch(
        self, server_ids: list[int], period_days: float, end: datetime
    ) -> dict[int, CpuBreakdownRaw]:
        """`get_report_cpu_breakdown` 배치."""
        start = end - timedelta(days=period_days)
        sql = text("""
            WITH bkt AS (
                SELECT server_id,
                    CASE WHEN delta(cpu_total_ca) > 0 THEN delta(cpu_user_ca)   / delta(cpu_total_ca) * 100 END AS u,
                    CASE WHEN delta(cpu_total_ca) > 0 THEN delta(cpu_system_ca) / delta(cpu_total_ca) * 100 END AS s,
                    CASE WHEN delta(cpu_total_ca) > 0 THEN delta(cpu_iowait_ca) / delta(cpu_total_ca) * 100 END AS w
                FROM server_metrics_5m
                WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
            )
            SELECT server_id, avg(u) AS user_pct, avg(s) AS system_pct, avg(w) AS iowait_pct
            FROM bkt GROUP BY server_id
        """)
        result = await self.session.execute(sql, {"sids": server_ids, "start": start, "end": end})
        return {
            r.server_id: CpuBreakdownRaw(
                user_pct=float(r.user_pct) if r.user_pct is not None else None,
                system_pct=float(r.system_pct) if r.system_pct is not None else None,
                iowait_pct=float(r.iowait_pct) if r.iowait_pct is not None else None,
            )
            for r in result.all()
        }
