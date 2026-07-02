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
    _DATA_VOLUME_SQL_FILTER,
)


class ReportQueryRepository(_BaseQueryMixin, BaseReportQueryRepository):
    async def report_aggregate(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> list[ReportRowRaw]:
        """N서버 x period_days 통계 → ReportRowRaw list. role/recommendation 등 표시 파생은 service에서.

        보존 의도:
        - CPU/iowait = server_metrics_5m cagg(5분 counter_agg). delta()가 reset(재부팅·wraparound)을 값-감소
          기준 일률 흡수. per-bucket CPU% = 5분 평균(right-sizing 표준), percentile 은 버킷에 정확 산출.
        - mem/load/swap = cagg 사전집계(시점값 avg/max). mount_max = server_mount_usage_5m cagg(가상 mount 필터 pre-applied).
        - server_inventory LEFT JOIN — metric 없는 서버도 행 반환, services JSONB 동시 SELECT (N+1 회피).
        """
        start = end - timedelta(days=period_days)

        sql = text("""
            WITH bkt AS (
                -- server_metrics_5m cagg — 5분 버킷 counter_agg. delta() = 버킷 내 카운터 증가(reset 값-감소 일률
                -- 처리, 재부팅·wraparound 흡수 — boot_time gate 불요). per-bucket CPU% = 5분 평균(right-sizing 표준).
                SELECT server_id, bucket,
                    CASE WHEN delta(cpu_total_ca) > 0
                         THEN GREATEST(0, (1 - delta(cpu_idle_ca) / delta(cpu_total_ca)) * 100)
                    END AS cpu_pct,
                    CASE WHEN delta(cpu_total_ca) > 0
                         THEN GREATEST(0, delta(cpu_iowait_ca) / delta(cpu_total_ca) * 100)
                    END AS iowait_pct,
                    mem_pct_avg, mem_pct_max, load_15m_max, swap_in_use, disk_queue_avg
                FROM server_metrics_5m
                WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
            ),
            cpu_stats AS (
                SELECT server_id,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY cpu_pct) AS cpu_p95,
                    AVG(cpu_pct) AS cpu_avg,
                    MAX(cpu_pct) AS cpu_peak,
                    COUNT(cpu_pct) AS cpu_sample,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY iowait_pct) AS iowait_p95,
                    MAX(iowait_pct) AS iowait_peak,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY disk_queue_avg) AS disk_queue_p95
                FROM bkt GROUP BY server_id
            ),
            mem_stats AS (
                SELECT server_id,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY mem_pct_avg) AS mem_p95,
                    AVG(mem_pct_avg) AS mem_avg,
                    MAX(mem_pct_max) AS mem_peak,
                    COUNT(mem_pct_avg) AS mem_sample,
                    bool_or(swap_in_use > 0) AS swap_used
                FROM bkt WHERE mem_pct_avg IS NOT NULL GROUP BY server_id
            ),
            load_stats AS (
                SELECT server_id, MAX(load_15m_max) AS load_15m_max FROM bkt GROUP BY server_id
            ),
            mount_max AS (
                -- 서버 worst mount used_pct (period 안 최대). server_mount_usage_5m cagg (가상 mount 필터 pre-applied).
                SELECT server_id, MAX(used_pct_max) AS worst_used_pct
                FROM server_mount_usage_5m
                WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
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
                s.interfaces    AS interfaces,
                s.services      AS services,
                s.listen_ports  AS listen_ports,
                s.last_seen_at  AS last_seen_at,
                s.cpu_cores     AS cpu_cores,
                s.mem_total_kb  AS mem_total_kb,
                s.disks         AS disks,
                s.mounts        AS inventory_mounts,
                s.boot_time     AS boot_time,
                cs.cpu_p95      AS cpu_p95,
                cs.cpu_avg      AS cpu_avg,
                cs.cpu_peak     AS cpu_peak,
                cs.iowait_p95   AS iowait_p95,
                cs.iowait_peak  AS iowait_peak,
                cs.disk_queue_p95 AS disk_queue_p95,
                ms.mem_p95      AS mem_p95,
                ms.mem_avg      AS mem_avg,
                ms.mem_peak     AS mem_peak,
                COALESCE(ms.swap_used, false) AS swap_used,
                ls.load_15m_max AS load_15m_max,
                mm.worst_used_pct AS worst_mount_used_pct,
                -- 표본 충분성 — 실측 cpu/mem 버킷 / 윈도우 기대 버킷(period_days*288, 5분 cagg). p95 신뢰도 단서.
                cs.cpu_sample::float / NULLIF(:expected_samples, 0) AS cpu_sufficiency,
                ms.mem_sample::float / NULLIF(:expected_samples, 0) AS mem_sufficiency
            FROM server_inventory s
            LEFT JOIN cpu_stats  cs ON cs.server_id = s.id
            LEFT JOIN mem_stats  ms ON ms.server_id = s.id
            LEFT JOIN load_stats ls ON ls.server_id = s.id
            LEFT JOIN mount_max  mm ON mm.server_id = s.id
            WHERE s.id = ANY(:sids)
            ORDER BY s.hostname
        """)
        result = await self.session.execute(
            sql,
            {
                "sids": server_ids,
                "start": start,
                "end": end,
                "expected_samples": period_days * 288,  # 5분 버킷 윈도우 기대(24*12), cagg 사전집계
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
                kernel_version=r.kernel_version,
                interfaces=r.interfaces,
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
                disk_queue_p95=r.disk_queue_p95,
                cpu_cores=r.cpu_cores,
                mem_total_kb=r.mem_total_kb,
                disks=r.disks,
                inventory_mounts=r.inventory_mounts,
                boot_time=r.boot_time,
                worst_mount_used_pct=r.worst_mount_used_pct,
                cpu_sufficiency=r.cpu_sufficiency,
                mem_sufficiency=r.mem_sufficiency,
            )
            for r in result.all()
        ]

    async def report_mount_worst(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> dict[int, tuple[str | None, float | None, int | None]]:
        """마운트별 max used_pct + fill_rate 기반 days_until_full 추정. 서버당 worst 1건만 반환.

        worst = used_pct DESC 첫 행 (동률 시 days_until_full ASC). fill_rate = (avail_start - avail_end)/period_days.
        """
        start = end - timedelta(days=period_days)

        sql = text("""
            WITH usage_max AS (
                -- server_mount_usage_5m cagg (가상 mount 필터 pre-applied). (server, mount)별 max used_pct.
                SELECT server_id, mount, MAX(used_pct_max) AS max_used_pct
                FROM server_mount_usage_5m
                WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
                GROUP BY server_id, mount
            ),
            fill_rate AS (
                -- 윈도우 시작·종료 avail = 첫 버킷 avail_first / 마지막 버킷 avail_last (toolkit first/last).
                SELECT server_id, mount,
                    first(avail_first, bucket) AS avail_start,
                    last(avail_last, bucket)   AS avail_end
                FROM server_mount_usage_5m
                WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
                GROUP BY server_id, mount
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
        """period 안 boot_time DISTINCT count - 1 (=재부팅 횟수). 현재 boot_time 포함이라 -1."""
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
        """period 안 agent_started_at DISTINCT count - 1 (=재시작 횟수). report_uptime_stats 와 동일 산식 (#F10)."""
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
        """since 이후 server별 agent 재시작 횟수 — attention agent_unstable fixed 윈도우 (Redis sliding 대체)."""
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
        """server_id -> (iops_baseline, throughput_kbps_baseline, iops_p95, iops_peak, kbps_p95, kbps_peak).

        baseline = SUM(delta)/SUM(dt). p95/peak = 시점별 device 합산 rate 분포. reset 행 제외(dt>0 AND delta>=0).
        """
        start = end - timedelta(days=period_days)

        sql = text("""
            WITH per_dev AS (
                -- server_disk_io_5m cagg (물리 device만). delta()=버킷 내 ops/bytes 증가(reset 값-감소 일률 처리),
                -- time_delta()=버킷 관측 시간. rate = delta/time_delta.
                SELECT server_id, bucket,
                    delta(reads_ca) + delta(writes_ca)               AS ops,
                    (delta(sread_ca) + delta(swritten_ca)) * 512     AS bytes,
                    time_delta(reads_ca)                             AS dt
                FROM server_disk_io_5m
                WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
            ),
            per_bucket AS (
                -- device 합산(서버 IO) + 버킷 시간(device 표본 시각 ~공통이라 MAX). dt>0 = 버킷 내 2+ 표본.
                -- device 간 dt 편차 클 때 rate 근사(baseline 용도라 수용, peak 영향은 trade-off).
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
        """server_id -> (rx_kbps_baseline, tx_kbps_baseline, rx_p95, rx_peak, tx_p95, tx_peak).

        baseline = SUM/SUM. p95/peak = 시점별 interface 합산 rate 분포.
        """
        start = end - timedelta(days=period_days)

        sql = text("""
            WITH per_if AS (
                -- server_net_io_5m cagg (물리 interface만). delta()=버킷 내 bytes 증가(reset 일률 처리),
                -- time_delta()=버킷 관측 시간.
                SELECT server_id, bucket,
                    delta(rx_ca) AS rx_bytes, delta(tx_ca) AS tx_bytes, time_delta(rx_ca) AS dt
                FROM server_net_io_5m
                WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
            ),
            per_bucket AS (
                -- interface 합산 + 버킷 시간 MAX(공통). interface 간 dt 편차 시 rate 근사(baseline 용도 수용).
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

        전 서버·전 시점 통합 비율. 빈 구간/미수집 시점은 분자·분모 동시 제외 — "그 시점 살아있는 VM" 만
        자동 반영, 측정 기간 편차도 분모에 녹아 별도 정규화 불필요. 서버 1대=1표가 아닌 자원량 가중이라
        거대 VM 이 큰 비중 = 물리 자원 활용률 관점에서 정확.
        cpu 는 코어 수가 jiffies 에 내재해 곱셈 없이 capacity-weighted. CPU 는 server_metrics_5m cagg(counter_agg,
        report_aggregate 와 동일 정석 reset 처리)라 p95 입력이 5분 버킷 분포, mem/disk 는 capacity-weighted KB
        gauge(reset weirdness 무관, cagg 에 KB 합 없음)라 raw collected_at 분포 — cpu/mem p95 입도 비대칭 의도.
        end 기준 윈도우(selection anchor 스냅샷 존중). C5 partition pruning, period_days <= 30 cap.
        """
        capped = min(max(period_days, 0.0), 30)
        start = end - timedelta(days=capped)
        sid = " AND server_id = ANY(:sids)" if server_ids else ""
        sql = text(f"""
            WITH cpu_bkt AS (
                -- server_metrics_5m cagg — per (server, bucket) jiffies delta (counter_agg, reset 값-감소 일률
                -- 처리, report_aggregate 와 동일 정석). CPU 는 코어 수가 jiffies 에 내재해 곱셈 없이 capacity-weighted.
                SELECT bucket, delta(cpu_idle_ca) AS d_idle, delta(cpu_total_ca) AS d_total
                FROM server_metrics_5m
                WHERE bucket >= :start AND bucket <= :end{sid}
            ),
            cpu_valid AS (
                SELECT bucket, d_idle, d_total FROM cpu_bkt WHERE d_total > 0 AND d_idle IS NOT NULL
            ),
            -- 버킷별 capacity-weighted 환경값 (p95 입력) — 전 서버 jiffies 합 비율 분포의 95퍼센타일.
            cpu_per_ts AS (
                SELECT GREATEST(0, (1 - SUM(d_idle)::float / SUM(d_total)) * 100) AS v
                FROM cpu_valid GROUP BY bucket HAVING SUM(d_total) > 0
            ),
            mem_per_ts AS (
                SELECT SUM(mem_total_kb - mem_available_kb)::float / NULLIF(SUM(mem_total_kb), 0) * 100 AS v
                FROM server_metrics
                WHERE collected_at >= :start AND collected_at <= :end{sid}
                  AND mem_total_kb > 0 AND mem_available_kb IS NOT NULL
                GROUP BY collected_at
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
                -- p95: 시점별 환경값 분포의 95퍼센타일 (avg 와 동일 per_ts 기반). CPU·메모리만 —
                -- 디스크는 물리디스크/디바이스(major·minor) 인식이 Windows 에서 불완전해 capacity 합이 신뢰 불가라 제외.
                (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY v) FROM cpu_per_ts) AS cpu_p95,
                (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY v)
                 FROM mem_per_ts WHERE v IS NOT NULL) AS mem_p95,
                (SELECT COUNT(DISTINCT server_id) FROM server_metrics
                 WHERE collected_at >= :start AND collected_at <= :end{sid}) AS sample_size
        """)
        params: dict = {"start": start, "end": end}
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

    async def report_mount_usage(self, server_id: int, period_days: float, end: datetime) -> list[ReportMountUsageRaw]:
        """마운트별 윈도우 평균 사용률 — 개별 보고서 스토리지 상세. batch 의 N=1 특수화 (SQL 단일 진실)."""
        return (await self.report_mount_usage_batch([server_id], period_days, end)).get(server_id, [])

    async def report_memory_breakdown(self, server_id: int, period_days: float, end: datetime) -> MemoryBreakdownRaw:
        """메모리 구성 윈도우 평균 — batch 의 N=1 특수화 (SQL 단일 진실). 데이터 없으면 전 축 None."""
        return (await self.report_memory_breakdown_batch([server_id], period_days, end)).get(
            server_id, MemoryBreakdownRaw(None, None, None, None)
        )

    async def report_cpu_breakdown(self, server_id: int, period_days: float, end: datetime) -> CpuBreakdownRaw:
        """CPU 분류 윈도우 평균 — batch 의 N=1 특수화 (SQL 단일 진실). 데이터 없으면 전 축 None."""
        return (await self.report_cpu_breakdown_batch([server_id], period_days, end)).get(
            server_id, CpuBreakdownRaw(None, None, None)
        )

    async def report_mount_usage_batch(
        self, server_ids: list[int], period_days: float, end: datetime
    ) -> dict[int, list[ReportMountUsageRaw]]:
        """마운트별 윈도우 평균 사용률 배치 — server_mount_usage_5m cagg 단일 소스 (C5).

        used_pct_avg(5분 사전집계)·total_bytes_max·kind='data' 필터(가상 mount 제외)가 cagg 정의에
        내장돼 raw scan·재계산·필터 재지정 불필요. single report_mount_usage 는 본 메서드의 N=1 특수화.
        child fan-out 1회 조회 (A5). C5: cagg 조회는 WHERE bucket >= 로 partition pruning.
        """
        start = end - timedelta(days=period_days)
        sql = text("""
            SELECT server_id, mount, max(total_bytes_max) AS total_bytes, avg(used_pct_avg) AS used_pct
            FROM server_mount_usage_5m
            WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
            GROUP BY server_id, mount
            ORDER BY server_id, used_pct DESC NULLS LAST
        """)
        result = await self.session.execute(sql, {"sids": server_ids, "start": start, "end": end})
        out: dict[int, list[ReportMountUsageRaw]] = {}
        for r in result.all():
            out.setdefault(r.server_id, []).append(
                ReportMountUsageRaw(
                    mount=r.mount,
                    total_bytes=int(r.total_bytes) if r.total_bytes is not None else None,
                    used_pct=float(r.used_pct) if r.used_pct is not None else None,
                )
            )
        return out

    async def report_memory_breakdown_batch(
        self, server_ids: list[int], period_days: float, end: datetime
    ) -> dict[int, MemoryBreakdownRaw]:
        """`report_memory_breakdown` 배치 — GROUP BY server_id."""
        start = end - timedelta(days=period_days)
        sql = text("""
            SELECT server_id,
                avg(CASE WHEN mem_total_kb > 0 THEN (1 - mem_available_kb::float / mem_total_kb) * 100 END) AS used_pct,
                avg(CASE WHEN mem_total_kb > 0 AND mem_available_kb IS NOT NULL
                         THEN mem_available_kb::float / mem_total_kb * 100 END) AS available_pct,
                avg(CASE WHEN mem_total_kb > 0 AND mem_cached_kb IS NOT NULL
                         THEN mem_cached_kb::float / mem_total_kb * 100 END) AS cached_pct,
                avg(CASE WHEN mem_total_kb > 0 AND mem_buffers_kb IS NOT NULL
                         THEN mem_buffers_kb::float / mem_total_kb * 100 END) AS buffers_pct
            FROM server_metrics
            WHERE server_id = ANY(:sids) AND collected_at >= :start AND collected_at <= :end
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

    async def report_cpu_breakdown_batch(
        self, server_ids: list[int], period_days: float, end: datetime
    ) -> dict[int, CpuBreakdownRaw]:
        """`report_cpu_breakdown` 배치 — server_metrics_5m counter_agg delta, GROUP BY server_id."""
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
            SELECT server_id,
                avg(u) AS user_pct, avg(s) AS system_pct, avg(w) AS iowait_pct
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
