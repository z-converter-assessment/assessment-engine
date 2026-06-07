"""Report aggregation 도메인 concrete — USE Method 통계 + 환경 활용률."""

from datetime import datetime, timedelta

from sqlalchemy import text

from assessment_engine.db.dtos.outbound import (
    CpuBreakdownRaw,
    EnvironmentUtilizationRaw,
    MemoryBreakdownRaw,
    ReportMountUsageRaw,
    ReportRowRaw,
)
from assessment_engine.db.repositories.query._base import _BaseQueryMixin
from assessment_engine.db.repositories.query.base_report import BaseReportQueryRepository
from assessment_engine.db.repositories.query.types import (
    _CPU_TOTAL_EXPR,
    _DATA_VOLUME_SQL_FILTER,
    _PHYS_DISK_SQL_FILTER,
    _VIRTUAL_IFACE_SQL_FILTER,
    BOOT_JITTER_SEC,
)


class ReportQueryRepository(_BaseQueryMixin, BaseReportQueryRepository):
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
        - mount_max CTE: 디스크 capacity 활용률 (서버 worst mount used_pct). USE Method disk_capacity
          평가·표시 단일 진실 — report_mount_worst(이름·days 별도)와 산식 동일(가상 mount 제외).
        - server_inventory LEFT JOIN — metric 없는 서버도 행 반환. services JSONB 동시 SELECT (N+1 회피).
        """
        start = end - timedelta(days=period_days)

        sql = text(f"""
            WITH cpu_deltas AS (
                SELECT server_id,
                    boot_time,
                    LAG(boot_time) OVER (PARTITION BY server_id ORDER BY collected_at) AS prev_boot,
                    cpu_idle - LAG(cpu_idle) OVER (PARTITION BY server_id ORDER BY collected_at) AS d_idle,
                    cpu_iowait - LAG(cpu_iowait) OVER (PARTITION BY server_id ORDER BY collected_at) AS d_iowait,
                    (cpu_user + cpu_nice + cpu_system + cpu_idle
                     + cpu_iowait + cpu_irq + cpu_softirq + cpu_steal)
                      - LAG(cpu_user + cpu_nice + cpu_system + cpu_idle
                            + cpu_iowait + cpu_irq + cpu_softirq + cpu_steal)
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
                  AND (boot_time IS NULL OR prev_boot IS NULL
                       OR ABS(EXTRACT(EPOCH FROM (boot_time - prev_boot))) <= :jitter_sec)
            ),
            cpu_stats AS (
                SELECT server_id,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY pct) AS cpu_p95,
                    AVG(pct) AS cpu_avg,
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
                    AVG(pct) AS mem_avg,
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
            ),
            mount_max AS (
                -- 서버 worst mount used_pct (period 안 최대). 가상 mount 제외 — report_mount_worst 와 산식 동일.
                SELECT server_id,
                    MAX((1 - avail_bytes::float / total_bytes) * 100) AS worst_used_pct
                FROM server_mount_usage
                WHERE server_id = ANY(:sids) AND collected_at >= :start AND collected_at <= :end
                  AND total_bytes > 0
                  AND {_DATA_VOLUME_SQL_FILTER}
                GROUP BY server_id
            )
            SELECT
                s.id            AS server_id,
                s.public_id     AS public_id,
                s.hostname      AS hostname,
                s.os_family     AS os_family,
                s.os_id         AS os_id,
                s.os_version    AS os_version,
                s.kernel_version AS kernel_version,
                s.ip_internal   AS ip_internal,
                s.services      AS services,
                s.listen_ports  AS listen_ports,
                s.last_seen_at  AS last_seen_at,
                s.cpu_cores     AS cpu_cores,
                s.mem_total_kb  AS mem_total_kb,
                s.disks         AS disks,
                s.boot_time     AS boot_time,
                cs.cpu_p95      AS cpu_p95,
                cs.cpu_avg      AS cpu_avg,
                cs.cpu_peak     AS cpu_peak,
                cs.iowait_p95   AS iowait_p95,
                cs.iowait_peak  AS iowait_peak,
                ms.mem_p95      AS mem_p95,
                ms.mem_avg      AS mem_avg,
                ms.mem_peak     AS mem_peak,
                COALESCE(ms.swap_used, false) AS swap_used,
                ls.load_15m_max AS load_15m_max,
                mm.worst_used_pct AS worst_mount_used_pct
            FROM server_inventory s
            LEFT JOIN cpu_stats  cs ON cs.server_id = s.id
            LEFT JOIN mem_stats  ms ON ms.server_id = s.id
            LEFT JOIN load_stats ls ON ls.server_id = s.id
            LEFT JOIN mount_max  mm ON mm.server_id = s.id
            WHERE s.id = ANY(:sids)
            ORDER BY s.hostname
        """)
        result = await self.session.execute(
            sql, {"sids": server_ids, "start": start, "end": end, "jitter_sec": BOOT_JITTER_SEC}
        )

        return [
            ReportRowRaw(
                server_id=r.server_id,
                public_id=str(r.public_id),
                hostname=r.hostname,
                os_family=r.os_family,
                os_id=r.os_id,
                os_version=r.os_version,
                kernel_version=r.kernel_version,
                ip_internal=r.ip_internal,
                services=r.services,
                listen_ports=r.listen_ports,
                last_seen_at=r.last_seen_at,
                cpu_p95_pct=r.cpu_p95,
                cpu_avg_pct=r.cpu_avg,
                cpu_peak_pct=r.cpu_peak,
                mem_p95_pct=r.mem_p95,
                mem_avg_pct=r.mem_avg,
                mem_peak_pct=r.mem_peak,
                load_15m_max=r.load_15m_max,
                swap_used=bool(r.swap_used),
                iowait_p95_pct=r.iowait_p95,
                iowait_peak_pct=r.iowait_peak,
                cpu_cores=r.cpu_cores,
                mem_total_kb=r.mem_total_kb,
                disks=r.disks,
                boot_time=r.boot_time,
                worst_mount_used_pct=r.worst_mount_used_pct,
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
                -- (server_id, mount)별 max used_pct. 가상 mount 제외 — 단일 진실 _DATA_VOLUME_SQL_FILTER.
                SELECT server_id, mount,
                    MAX((1 - avail_bytes::float / total_bytes) * 100) AS max_used_pct
                FROM server_mount_usage
                WHERE server_id = ANY(:sids) AND collected_at >= :start AND collected_at <= :end
                  AND total_bytes > 0
                  AND {_DATA_VOLUME_SQL_FILTER}
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
                  AND {_DATA_VOLUME_SQL_FILTER}
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
            sql,
            {"sids": server_ids, "start": start, "end": end, "period_days": period_days},
        )
        return {r.server_id: (r.mount, r.max_used_pct, r.days_until_full) for r in result.all()}

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

    async def report_agent_restart_stats(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> dict[int, int]:
        """period 안 server_inventory_history의 agent_started_at DISTINCT count - 1 (=에이전트 재시작 횟수).

        report_uptime_stats(재부팅, boot_time)와 동일 산식 — anchor+window 정합 (#F10).
        """
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

    async def agent_restart_counts_recent(self, server_ids: list[int], since: datetime) -> dict[int, int]:
        """since 이후 server별 agent 재시작 횟수 (DISTINCT agent_started_at - 1).

        attention agent_unstable fixed 윈도우 표시 — Redis sliding 카운터 대체 (정확한 '최근 N시간').
        report_agent_restart_stats(보고서 window)와 동일 산식, since~now 고정 윈도우만 다름.
        """
        if not server_ids:
            return {}
        sql = text("""
            SELECT server_id, GREATEST(0, COUNT(DISTINCT agent_started_at) - 1) AS restart_count
            FROM server_inventory_history
            WHERE server_id = ANY(:sids) AND collected_at >= :since
              AND agent_started_at IS NOT NULL
            GROUP BY server_id
        """)
        result = await self.session.execute(sql, {"sids": server_ids, "since": since})
        return {r.server_id: int(r.restart_count) for r in result.all()}

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

        sql = text(f"""
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
                  AND {_PHYS_DISK_SQL_FILTER}
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
                CASE WHEN b.total_seconds > 0
                     THEN b.total_bytes / b.total_seconds / 1024 END AS throughput_kbps_baseline,
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

        sql = text(f"""
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
                  AND {_VIRTUAL_IFACE_SQL_FILTER}
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

    async def environment_utilization(
        self,
        period_days: float,
        end: datetime,
        server_ids: list[int] | None = None,
    ) -> EnvironmentUtilizationRaw:
        """환경(또는 선택 N대) capacity-weighted 평균 활용률 — 자원 총량 가중 (Σused / Σtotal).

        전 서버·전 시점 통합 비율. 빈 구간/미수집 시점은 분자·분모에서 동시에 빠져
        "그 시점 살아있는 VM" 만 자동 반영 — 서버별 측정 기간 편차도 분모에 녹아들어
        별도 정규화 불필요. 거대 VM 이 큰 비중 = 환경 물리 자원 활용률 관점 정확
        (서버 1대=1표 동등 가중이 아님 — 작은 VM 1대와 큰 VM 1대를 동급 취급하지 않는다).
        - cpu_avg: (1 - Σ d_idle / Σ d_total) x 100. jiffies delta 합 통합 — 코어 수가 jiffies 에
          내재해 코어 수 곱셈 없이 capacity-weighted. report_aggregate 와 동일한 d_idle/d_total
          정의 + boot_time 변경 시 reset 제외.
        - mem_avg: Σ(mem_total_kb - mem_available_kb) / Σ mem_total_kb x 100. 절대 KB 라 메모리 큰 서버 큰 비중.
        - disk_avg: Σ(total_bytes - avail_bytes) / Σ total_bytes x 100. 전 mount 통합, 가상 mount 제외
          (_DATA_VOLUME_SQL_FILTER). 서버별 worst mount 개념은 환경 평균에서 폐기 (호스트 상세엔 유지).
        - sample_size: 기간 내 metric 발행 서버 distinct count.
        end 기준 윈도우 (selection 발행 시점 anchor 스냅샷 존중). server_ids=None 전체 환경,
        list 면 해당 서버만 (selection 보고서 동일 SQL 경로). partition pruning 의무 (C5).
        period_days <= 30 cap (DB scan 보호).
        """
        capped = min(max(period_days, 0.0), 30)
        start = end - timedelta(days=capped)
        sid = " AND server_id = ANY(:sids)" if server_ids else ""
        sql = text(f"""
            WITH cpu_deltas AS (
                SELECT server_id,
                    boot_time,
                    LAG(boot_time) OVER (PARTITION BY server_id ORDER BY collected_at) AS prev_boot,
                    cpu_idle - LAG(cpu_idle) OVER (PARTITION BY server_id ORDER BY collected_at) AS d_idle,
                    ({_CPU_TOTAL_EXPR}) - LAG({_CPU_TOTAL_EXPR})
                        OVER (PARTITION BY server_id ORDER BY collected_at) AS d_total
                FROM server_metrics
                WHERE collected_at >= :start AND collected_at <= :end{sid}
            ),
            cpu_valid AS (
                -- 유효 delta = d_total>0 AND idle present AND reset 아님 (report_aggregate 와 동일 게이트).
                SELECT d_idle, d_total
                FROM cpu_deltas
                WHERE d_total > 0 AND d_idle IS NOT NULL
                  AND (boot_time IS NULL OR prev_boot IS NULL
                       OR ABS(EXTRACT(EPOCH FROM (boot_time - prev_boot))) <= :jitter_sec)
            )
            SELECT
                -- capacity-weighted: 서버별 평균이 아니라 전 서버·전 시점 delta 합으로 통합 비율.
                (SELECT CASE WHEN SUM(d_total) > 0
                             THEN GREATEST(0, (1 - SUM(d_idle)::float / SUM(d_total)) * 100)
                        END FROM cpu_valid) AS cpu_avg,
                (SELECT CASE WHEN SUM(mem_total_kb) > 0
                             THEN SUM(mem_total_kb - mem_available_kb)::float / SUM(mem_total_kb) * 100
                        END
                 FROM server_metrics
                 WHERE collected_at >= :start AND collected_at <= :end{sid}
                   AND mem_total_kb > 0 AND mem_available_kb IS NOT NULL) AS mem_avg,
                (SELECT CASE WHEN SUM(total_bytes) > 0
                             THEN SUM(total_bytes - avail_bytes)::float / SUM(total_bytes) * 100
                        END
                 FROM server_mount_usage
                 WHERE collected_at >= :start AND collected_at <= :end{sid}
                   AND {_DATA_VOLUME_SQL_FILTER}
                   AND total_bytes > 0 AND avail_bytes IS NOT NULL) AS disk_avg,
                (SELECT COUNT(DISTINCT server_id) FROM server_metrics
                 WHERE collected_at >= :start AND collected_at <= :end{sid}) AS sample_size
        """)
        params: dict = {"start": start, "end": end, "jitter_sec": BOOT_JITTER_SEC}
        if server_ids:
            params["sids"] = server_ids
        result = await self.session.execute(sql, params)
        row = result.one()
        return EnvironmentUtilizationRaw(
            cpu_avg_pct=float(row.cpu_avg) if row.cpu_avg is not None else None,
            mem_avg_pct=float(row.mem_avg) if row.mem_avg is not None else None,
            disk_avg_pct=float(row.disk_avg) if row.disk_avg is not None else None,
            sample_size=int(row.sample_size or 0),
        )

    async def report_mount_usage(self, server_id: int, period_days: float, end: datetime) -> list[ReportMountUsageRaw]:
        """마운트별 윈도우 평균 사용률 — 개별 보고서 스토리지 상세 (worst 1개 아닌 전체, 가상 mount 제외)."""
        start = end - timedelta(days=period_days)
        sql = text(f"""
            SELECT mount, max(total_bytes) AS total_bytes,
                avg(CASE WHEN total_bytes > 0 AND avail_bytes IS NOT NULL
                         THEN ((total_bytes - avail_bytes)::float / total_bytes) * 100 END) AS used_pct
            FROM server_mount_usage
            WHERE server_id = :sid AND collected_at >= :start AND collected_at <= :end
              AND {_DATA_VOLUME_SQL_FILTER}
            GROUP BY mount
            ORDER BY used_pct DESC NULLS LAST
        """)
        result = await self.session.execute(sql, {"sid": server_id, "start": start, "end": end})
        return [
            ReportMountUsageRaw(
                mount=r.mount,
                total_bytes=int(r.total_bytes) if r.total_bytes is not None else None,
                used_pct=float(r.used_pct) if r.used_pct is not None else None,
            )
            for r in result.all()
        ]

    async def report_memory_breakdown(self, server_id: int, period_days: float, end: datetime) -> MemoryBreakdownRaw:
        """메모리 구성 윈도우 평균 — used/available/cached/buffers (전체 메모리 대비 %, 시점값 avg)."""
        start = end - timedelta(days=period_days)
        sql = text("""
            SELECT
                avg(CASE WHEN mem_total_kb > 0 THEN (1 - mem_available_kb::float / mem_total_kb) * 100 END) AS used_pct,
                avg(CASE WHEN mem_total_kb > 0 AND mem_available_kb IS NOT NULL
                         THEN mem_available_kb::float / mem_total_kb * 100 END) AS available_pct,
                avg(CASE WHEN mem_total_kb > 0 AND mem_cached_kb IS NOT NULL
                         THEN mem_cached_kb::float / mem_total_kb * 100 END) AS cached_pct,
                avg(CASE WHEN mem_total_kb > 0 AND mem_buffers_kb IS NOT NULL
                         THEN mem_buffers_kb::float / mem_total_kb * 100 END) AS buffers_pct
            FROM server_metrics
            WHERE server_id = :sid AND collected_at >= :start AND collected_at <= :end
        """)
        row = (await self.session.execute(sql, {"sid": server_id, "start": start, "end": end})).one()
        return MemoryBreakdownRaw(
            used_pct=float(row.used_pct) if row.used_pct is not None else None,
            available_pct=float(row.available_pct) if row.available_pct is not None else None,
            cached_pct=float(row.cached_pct) if row.cached_pct is not None else None,
            buffers_pct=float(row.buffers_pct) if row.buffers_pct is not None else None,
        )

    async def report_cpu_breakdown(self, server_id: int, period_days: float, end: datetime) -> CpuBreakdownRaw:
        """CPU 분류 윈도우 평균 — user/system/iowait (jiffies LAG delta, counter reset(dt<=0·d<0) 흡수)."""
        start = end - timedelta(days=period_days)
        sql = text(f"""
            WITH raw AS (
                SELECT collected_at, cpu_user AS u, cpu_system AS s, cpu_iowait AS w,
                    ({_CPU_TOTAL_EXPR}) AS total
                FROM server_metrics
                WHERE server_id = :sid AND collected_at >= :start AND collected_at <= :end
            ),
            deltas AS (
                SELECT
                    u - LAG(u)         OVER (ORDER BY collected_at) AS du,
                    s - LAG(s)         OVER (ORDER BY collected_at) AS ds,
                    w - LAG(w)         OVER (ORDER BY collected_at) AS dw,
                    total - LAG(total) OVER (ORDER BY collected_at) AS dt
                FROM raw
            )
            SELECT
                avg(CASE WHEN dt > 0 AND du >= 0 THEN du * 100.0 / dt END) AS user_pct,
                avg(CASE WHEN dt > 0 AND ds >= 0 THEN ds * 100.0 / dt END) AS system_pct,
                avg(CASE WHEN dt > 0 AND dw >= 0 THEN dw * 100.0 / dt END) AS iowait_pct
            FROM deltas
        """)
        row = (await self.session.execute(sql, {"sid": server_id, "start": start, "end": end})).one()
        return CpuBreakdownRaw(
            user_pct=float(row.user_pct) if row.user_pct is not None else None,
            system_pct=float(row.system_pct) if row.system_pct is not None else None,
            iowait_pct=float(row.iowait_pct) if row.iowait_pct is not None else None,
        )
