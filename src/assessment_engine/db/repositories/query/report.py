"""Report aggregation 도메인 concrete — USE Method 통계 + 환경 활용률."""

from datetime import datetime, timedelta

from sqlalchemy import text

from assessment_engine import recommendation  # 순수 도메인 커널 — right-sizing 정책 상수(순환 없음)
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
                    -- Windows Memory\\Pages Input/sec(하드 페이지 폴트, 누적 counter) -> delta/time_delta 로 rate 환산.
                    -- mmap 파일 I/O 미혼입이라 총 Pages/sec 과 달리 순수 메모리 압박 신호. Linux 는 null -> swap 축 사용.
                    CASE WHEN time_delta(mem_pages_input_ca) > 0
                         THEN GREATEST(0, delta(mem_pages_input_ca) / time_delta(mem_pages_input_ca))
                    END AS pages_input_rate,
                    -- ADR 0052 신 신호 (per-bucket delta/gauge) — steal%·D-state·swap page-out·hard page-in·재전송 델타.
                    CASE WHEN delta(cpu_total_ca) > 0
                         THEN GREATEST(0, delta(cpu_steal_ca) / delta(cpu_total_ca) * 100)
                    END AS steal_pct,
                    procs_blocked_avg AS procs_blocked,
                    procs_running_avg AS procs_running,
                    delta(pswpout_ca)         AS pswpout_delta,
                    delta(mem_pages_input_ca) AS pages_in_delta,
                    delta(oom_kill_ca)        AS oom_delta,
                    delta(tcp_retrans_ca)     AS retrans_delta,
                    mem_pct_avg, mem_pct_max, load_15m_max, swap_in_use, disk_queue_avg, cpu_run_queue_avg
                FROM server_metrics_5m
                WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
            ),
            cpu_stats AS (
                SELECT server_id,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY cpu_pct) AS cpu_p95,
                    percentile_cont(0.5)  WITHIN GROUP (ORDER BY cpu_pct) AS cpu_median,
                    AVG(cpu_pct) AS cpu_avg,
                    MAX(cpu_pct) AS cpu_peak,
                    COUNT(cpu_pct) AS cpu_sample,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY iowait_pct) AS iowait_p95,
                    MAX(iowait_pct) AS iowait_peak,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY disk_queue_avg) AS disk_queue_p95,
                    -- Windows CPU saturation(Processor Queue Length gauge)·Memory saturation(Pages Input/sec rate) p95.
                    -- Linux 는 두 축 null -> percentile_cont 가 null 반환(load/swap 축 사용 유지).
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY cpu_run_queue_avg) AS cpu_run_queue_p95,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY pages_input_rate) AS mem_pages_input_rate_p95
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
            rs_stats AS (
                -- ADR 0052 host-wide 신 신호 (bkt 무필터 집계) — steal p95·D-state p95·swap paging bool·재전송 총량·
                -- 이용률 추세 기울기(regr_slope %/day, 임계 비교는 도메인 — SQL 은 raw slope 만, P1 aggregate 예외).
                SELECT server_id,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY steal_pct)      AS cpu_steal_p95,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY procs_blocked)  AS procs_blocked_p95,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY procs_running)  AS procs_running_p95,
                    bool_or(COALESCE(pswpout_delta, 0) > 0 OR COALESCE(pages_in_delta, 0) > 0) AS swap_paging,
                    bool_or(COALESCE(oom_delta, 0) > 0) AS oom_occurred,
                    SUM(retrans_delta) AS retrans_total,
                    regr_slope(cpu_pct, extract(epoch FROM bucket)) * 86400 AS cpu_trend_slope,
                    regr_slope(mem_pct_avg, extract(epoch FROM bucket)) * 86400 AS mem_trend_slope
                FROM bkt GROUP BY server_id
            ),
            mount_max AS (
                -- 서버 worst mount used_pct (period 안 최대). server_mount_usage_5m cagg (가상 mount 필터 pre-applied).
                SELECT server_id, MAX(used_pct_max) AS worst_used_pct
                FROM server_mount_usage_5m
                WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
                GROUP BY server_id
            ),
            mount_span AS (
                -- ADR 0052 디스크 용량 runway 입력 — 마운트별 가용 이력 전체(하한 없이 bucket <= :end)의
                -- 시작·종료 avail/inode + 총 용량 + 실제 관측 span(일). runway 는 분류 14일 창과 달리 전체 이력을 쓴다
                -- (누적 신호라 길수록 추세 정확 — 사이징 lookback 과 별개 축, 기준 정의).
                SELECT server_id, mount,
                    first(avail_first, bucket)       AS av_first,
                    last(avail_last, bucket)         AS av_last,
                    max(total_bytes_max)             AS total_bytes,
                    first(inodes_free_first, bucket) AS in_first,
                    last(inodes_free_last, bucket)   AS in_last,
                    EXTRACT(EPOCH FROM (max(bucket) - min(bucket))) / 86400.0 AS span_days
                FROM server_mount_usage_5m
                WHERE server_id = ANY(:sids) AND bucket <= :end
                GROUP BY server_id, mount
            ),
            mount_calc AS (
                -- 마운트별 runway(소진 일수) + 확장 목표 용량(GB, 두-경로). rate = avail 감소분/일.
                -- 경로1(추세 신뢰, span >= :trend_min_span): used + rate*365 = 1년 수명 목표. 충분히 관측된 성장률만 외삽.
                -- 경로2(자료 부족 + used >= :static_pct): used / (:headroom_pct/100) = 확장 후 headroom_pct% 착지. 이용률 기반.
                -- 짧은 span 을 365일로 외삽하면 spike 과외삽(비현실적 값) -> 경로1 은 span 게이트, 미충족은 경로2/None.
                SELECT server_id, mount,
                    CASE WHEN (av_first - av_last) > 0 AND span_days > 0 AND av_last >= 0
                         THEN GREATEST(0, FLOOR(av_last / ((av_first - av_last) / span_days))) END AS disk_runway_days,
                    CASE WHEN (in_first - in_last) > 0 AND span_days > 0 AND in_last >= 0
                         THEN GREATEST(0, FLOOR(in_last / ((in_first - in_last) / span_days))) END AS inode_runway_days,
                    CASE
                        -- 경로1(추세 신뢰, span >= :trend_min_span): used + rate*365 = 1년 수명 목표.
                        WHEN (av_first - av_last) > 0 AND span_days >= :trend_min_span AND total_bytes > 0
                            THEN CEIL(((total_bytes - av_last) + :target_runway * ((av_first - av_last) / span_days)) / 1e9)
                        -- 경로2(소진 임박·짧은 span): 30일 예상 used 를 headroom(:headroom_pct)에 착지 — 근시라 현실적, 항상 값.
                        WHEN (av_first - av_last) > 0 AND span_days > 0 AND total_bytes > 0
                            THEN CEIL(((total_bytes - av_last) + :near_horizon * ((av_first - av_last) / span_days))
                                      / (:headroom_pct / 100.0) / 1e9)
                        -- 경로3(추세 없음 + 임계 초과): 이용률 headroom.
                        WHEN total_bytes > 0 AND av_last >= 0 AND (1 - av_last::float / total_bytes) * 100 >= :static_pct
                            THEN CEIL((total_bytes - av_last) / (:headroom_pct / 100.0) / 1e9)
                    END AS target_gb,
                    CASE WHEN total_bytes > 0 THEN (1 - av_last::float / total_bytes) * 100 END AS used_pct,
                    -- 30일 후 예상 used% (현재 rate 로 근시 외삽, clamp 없이 실제값). 100% 초과 = 30일 내 소진(속도 노출).
                    CASE WHEN (av_first - av_last) > 0 AND span_days > 0 AND total_bytes > 0
                         THEN (1 - (av_last - :near_horizon * ((av_first - av_last) / span_days)) / total_bytes::float) * 100
                    END AS proj_30d_pct
                FROM mount_span
            ),
            mount_runway AS (
                -- 서버당 가장 빨리 소진되는 마운트(MIN runway) + 조치 대상 마운트의 확장 목표·30일 예상.
                -- 감소·수평·span<=0 이면 runway null(안 참). report_mount_worst 의 worst-by-usage 와 별개 축.
                SELECT r.server_id, r.disk_runway_days, r.inode_runway_days, t.target_gb, t.proj_30d_pct, t.used_pct
                FROM (
                    SELECT server_id, MIN(disk_runway_days) AS disk_runway_days, MIN(inode_runway_days) AS inode_runway_days
                    FROM mount_calc GROUP BY server_id
                ) r
                LEFT JOIN (
                    -- 조치 대상 마운트(소진 임박 OR 임계 초과)의 목표·예상·used%. 소진 임박(runway) 우선, 없으면 최고 used.
                    -- used%·proj_30d 를 같은 마운트에서 뽑아 표시 계층에서 짝 맞춤(worst-used 마운트와 혼입 방지).
                    SELECT DISTINCT ON (server_id) server_id, target_gb, proj_30d_pct, used_pct
                    FROM mount_calc WHERE disk_runway_days IS NOT NULL OR used_pct >= :static_pct
                    ORDER BY server_id, disk_runway_days ASC NULLS LAST, used_pct DESC NULLS LAST
                ) t ON t.server_id = r.server_id
            ),
            disk_await AS (
                -- ADR 0052 디스크 I/O await (Linux time_reading/writing 델타 / IO수 델타, 물리 device). virtio 라
                -- %util·avgqu 대신 응답 지연이 포화 주신호. Windows disk_io 시간필드 미발행 -> null(포화 미관측).
                SELECT server_id, bucket, MAX(await_ms) AS worst_await FROM (
                    SELECT server_id, bucket,
                        (delta(treading_ca) + delta(twriting_ca))
                            / NULLIF(delta(reads_ca) + delta(writes_ca), 0) AS await_ms
                    FROM server_disk_io_5m
                    WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
                ) d WHERE await_ms IS NOT NULL GROUP BY server_id, bucket
            ),
            disk_await_stats AS (
                SELECT server_id, percentile_cont(0.95) WITHIN GROUP (ORDER BY worst_await) AS await_p95
                FROM disk_await GROUP BY server_id
            ),
            net_quality AS (
                -- ADR 0052 네트워크 품질 — 드롭 총량/패킷 총량, tx 패킷(재전송 분모). server_net_io_5m 물리+bond.
                SELECT server_id,
                    SUM(rxd) + SUM(txd) AS drops_total,
                    SUM(rxp) + SUM(txp) AS packets_total,
                    SUM(txp)            AS tx_packets_total
                FROM (
                    SELECT server_id,
                        delta(rxd_ca) AS rxd, delta(txd_ca) AS txd,
                        delta(rxp_ca) AS rxp, delta(txp_ca) AS txp
                    FROM server_net_io_5m
                    WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
                ) n GROUP BY server_id
            ),
            percore AS (
                -- ADR 0052 단일스레드 병목 — 코어별 이용률 p95. server_cpu_core_5m cagg(코어별 counter_agg).
                -- per-bucket 코어 util% = 1 - delta(idle)/delta(total). Windows·구 agent 는 행 없음(null).
                SELECT server_id, core_id, percentile_cont(0.95) WITHIN GROUP (ORDER BY core_util) AS core_p95
                FROM (
                    SELECT server_id, core_id,
                        CASE WHEN delta(cpu_total_ca) > 0
                             THEN GREATEST(0, (1 - delta(cpu_idle_ca) / delta(cpu_total_ca)) * 100)
                        END AS core_util
                    FROM server_cpu_core_5m
                    WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
                ) cu WHERE core_util IS NOT NULL GROUP BY server_id, core_id
            ),
            percore_max AS (
                -- 서버당 가장 바쁜 코어의 p95 — 어느 코어든 임계 넘으면 다운사이즈/유휴 보류(단일스레드 보호).
                SELECT server_id, MAX(core_p95) AS cpu_percore_p95_max FROM percore GROUP BY server_id
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
                cs.cpu_run_queue_p95 AS cpu_run_queue_p95,
                cs.mem_pages_input_rate_p95 AS mem_pages_input_rate_p95,
                ms.mem_p95      AS mem_p95,
                ms.mem_avg      AS mem_avg,
                ms.mem_peak     AS mem_peak,
                COALESCE(ms.swap_used, false) AS swap_used,
                ls.load_15m_max AS load_15m_max,
                mm.worst_used_pct AS worst_mount_used_pct,
                -- 표본 충분성 — 실측 cpu/mem 버킷 / 윈도우 기대 버킷(period_days*288, 5분 cagg). p95 신뢰도 단서.
                cs.cpu_sample::float / NULLIF(:expected_samples, 0) AS cpu_sufficiency,
                ms.mem_sample::float / NULLIF(:expected_samples, 0) AS mem_sufficiency,
                -- ADR 0052 신 신호 — steal p95·burst(p95/median)·D-state p95·swap paging·await p95·runway·품질·추세.
                rs.cpu_steal_p95 AS cpu_steal_p95,
                CASE WHEN cs.cpu_median > 0 THEN cs.cpu_p95 / cs.cpu_median END AS cpu_burst_ratio,
                rs.procs_blocked_p95 AS procs_blocked_p95,
                rs.procs_running_p95 AS procs_running_p95,
                COALESCE(rs.swap_paging, false) AS swap_paging,
                COALESCE(rs.oom_occurred, false) AS oom_occurred,
                cs.cpu_sample * 5.0 / 60.0 AS history_hours,  -- 관측 버킷(5분) 누적 시간(계층3 AWS 30h floor)
                da.await_p95 AS disk_await_p95_ms,
                mr.disk_runway_days  AS disk_capacity_runway_days,
                mr.inode_runway_days AS disk_inode_runway_days,
                mr.target_gb         AS disk_capacity_target_gb,
                mr.proj_30d_pct      AS disk_capacity_proj_30d_pct,
                mr.used_pct          AS disk_capacity_driving_used_pct,
                nq.drops_total  / NULLIF(nq.packets_total, 0)    * 100 AS net_drop_pct,
                rs.retrans_total / NULLIF(nq.tx_packets_total, 0) * 100 AS net_retrans_pct,
                rs.cpu_trend_slope AS cpu_trend_slope,
                rs.mem_trend_slope AS mem_trend_slope,
                pc.cpu_percore_p95_max AS cpu_percore_p95_max
            FROM server_inventory s
            LEFT JOIN cpu_stats   cs ON cs.server_id = s.id
            LEFT JOIN mem_stats   ms ON ms.server_id = s.id
            LEFT JOIN load_stats  ls ON ls.server_id = s.id
            LEFT JOIN rs_stats    rs ON rs.server_id = s.id
            LEFT JOIN mount_max   mm ON mm.server_id = s.id
            LEFT JOIN mount_runway mr ON mr.server_id = s.id
            LEFT JOIN disk_await_stats da ON da.server_id = s.id
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
                # runway(mount_span)는 :period_days 미사용 — 전체 이력 실제 span 기반. start/end 는 분류 14일 창.
                "expected_samples": period_days * 288,  # 5분 버킷 윈도우 기대(24*12), cagg 사전집계
                "target_runway": recommendation.RS_DISK_TARGET_RUNWAY_DAYS,  # 확장 목표 수명(일) — 1년
                "trend_min_span": recommendation.RS_DISK_TREND_MIN_SPAN_DAYS,  # 성장률 외삽 신뢰 최소 관측 span(일)
                "near_horizon": recommendation.RS_DISK_NEAR_HORIZON_DAYS,  # 근시 지평(30일) — 짧은 span 목표·예상 공통
                "static_pct": recommendation.RS_DISK_STATIC_GUARD_PCT,  # 임계 초과 기준 used%(85)
                "headroom_pct": recommendation.RS_DISK_HEADROOM_TARGET_PCT,  # 확장 후 착지 used%(70)
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
                cpu_run_queue_p95=r.cpu_run_queue_p95,
                mem_pages_input_rate_p95=r.mem_pages_input_rate_p95,
                cpu_cores=r.cpu_cores,
                mem_total_kb=r.mem_total_kb,
                disks=r.disks,
                inventory_mounts=r.inventory_mounts,
                boot_time=r.boot_time,
                worst_mount_used_pct=r.worst_mount_used_pct,
                cpu_sufficiency=r.cpu_sufficiency,
                mem_sufficiency=r.mem_sufficiency,
                # ADR 0052 신 신호 (신 모델 rollup_host 입력, build_resource_stats 가 ResourceStats 로 배선)
                cpu_steal_p95_pct=r.cpu_steal_p95,
                cpu_burst_ratio=r.cpu_burst_ratio,
                procs_blocked_p95=r.procs_blocked_p95,
                procs_running_p95=r.procs_running_p95,
                mem_swap_paging=bool(r.swap_paging),
                oom_occurred=bool(r.oom_occurred),
                history_hours=r.history_hours,
                disk_await_p95_ms=r.disk_await_p95_ms,
                disk_capacity_runway_days=r.disk_capacity_runway_days,
                disk_capacity_target_gb=r.disk_capacity_target_gb,
                disk_capacity_proj_30d_pct=r.disk_capacity_proj_30d_pct,
                disk_capacity_driving_used_pct=r.disk_capacity_driving_used_pct,
                disk_inode_runway_days=r.disk_inode_runway_days,
                net_drop_pct=r.net_drop_pct,
                net_retrans_pct=r.net_retrans_pct,
                cpu_trend_slope=r.cpu_trend_slope,
                mem_trend_slope=r.mem_trend_slope,
                cpu_percore_p95_max=r.cpu_percore_p95_max,
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
