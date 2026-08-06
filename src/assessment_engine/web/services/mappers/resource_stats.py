"""ReportRowRaw -> 도메인 분류 입력(`recommendation.ResourceStats`) 어댑터.

표시 파생이 하나도 없다 — 단위 변환도 배지도 정렬도 없고, 집계 raw 를 도메인 축 이름으로 옮겨
담기만 한다. 그래서 표시 mapper 안에 있을 이유가 없고, 표시 모듈 둘(report·server)이 서로를
import 하던 순환도 여기로 내려오면서 끊긴다.

report·attention·서버목록·환경이 전부 이 함수 하나를 거쳐 `rollup_host` 를 탄다 — 화면 간 분류
정합의 근거이고, 임계를 다시 계산하는 경로가 생기면 그 정합이 깨진다 (#E3).
"""

from typing import TYPE_CHECKING

from assessment_engine import recommendation

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import ReportRowRaw


def build_resource_stats(raw: ReportRowRaw) -> recommendation.ResourceStats:
    """ReportRowRaw -> USE Method ResourceStats — report·attention mapper 공용(단일 진실).

    net baseline = server_net_io rx+tx 윈도우 평균(kB/s). 둘 다 None 이면 None(유휴 skip),
    하나만 있으면 다른쪽 0. os_family 전달로 포화 축 OS 분기(P2). report·attention·서버목록·환경이
    동일 stats 로 rollup_host 를 타 화면 간 분류 정합(임계 재계산 0).
    """
    net_avg = (
        None if raw.net_rx_kbps is None and raw.net_tx_kbps is None else (raw.net_rx_kbps or 0) + (raw.net_tx_kbps or 0)
    )
    # 표본 충분성 — 측정된 축(p95 not None)의 sufficiency 만 모아 min(보수적). 둘 다 부재면 None(판정 무관).
    suffs = [
        s
        for p95, s in ((raw.cpu_p95_pct, raw.cpu_sufficiency), (raw.mem_p95_pct, raw.mem_sufficiency))
        if p95 is not None and s is not None
    ]
    return recommendation.ResourceStats(
        cpu_p95_pct=raw.cpu_p95_pct,
        cpu_peak_pct=raw.cpu_peak_pct,
        # CPU 포화는 실행 큐로 판정한다 — Linux procs_running_p95, Windows cpu_run_queue_p95.
        cpu_cores=raw.cpu_cores,
        mem_p95_pct=raw.mem_p95_pct,
        mem_near_peak_pct=raw.mem_near_peak_pct,
        disk_used_pct=raw.worst_mount_used_pct,
        net_avg_kbytes_per_s=net_avg,
        os_family=raw.os_family,
        sample_sufficiency=min(suffs) if suffs else None,
        # Windows CPU saturation — Processor Queue Length p95 / Memory 는 Pages Input/sec rate p95 (os-aware 소비).
        cpu_run_queue_p95=raw.cpu_run_queue_p95,
        mem_pages_input_rate_p95=raw.mem_pages_input_rate_p95,
        # --- rollup_host 입력 — report_aggregate 산출 raw 를 도메인 축으로 배선 ---
        # 가장 바쁜 코어 p95 — 단일스레드 병목 판정(RS_CPU_PERCORE_HOLD). Windows·구 agent 는 None(graceful skip).
        cpu_percore_p95_max=raw.cpu_percore_p95_max,
        procs_blocked_p95=raw.procs_blocked_p95,
        # Linux CPU 포화 신호 + OOM 메모리 증거 — cpu_saturated·assess_memory os-aware 소비.
        procs_running_p95=raw.procs_running_p95,
        oom_occurred=raw.oom_occurred,
        mem_swap_paging=raw.mem_swap_paging,
        mem_total_mb=(raw.mem_total_bytes // 1024**2 if raw.mem_total_bytes is not None else None),
        disk_await_p95_ms=raw.disk_await_p95_ms,
        disk_iops_baseline=raw.disk_iops_baseline,  # 유휴 판정 활동 축 (디스크 I/O 활동량)
        disk_capacity_runway_days=raw.disk_capacity_runway_days,
        disk_inode_runway_days=raw.disk_inode_runway_days,
        disk_inode_used_pct=raw.disk_inode_used_pct,
        disk_capacity_target_gb=raw.disk_capacity_target_gb,
        net_retrans_pct=raw.net_retrans_pct,
        net_drop_pct=raw.net_drop_pct,
        conntrack_ratio=raw.conntrack_ratio,
        history_hours=raw.history_hours,
        cpu_burst_ratio=raw.cpu_burst_ratio,
        # 이용률 상승 추세 — 임계 이진화는 도메인 단일(regr_slope %/day raw -> bool). 다운사이즈 정상성 게이트.
        # span 가드 — 이력이 추세 신뢰 바닥(RS_CONFIDENCE_MIN_HOURS) 미만이면 slope 가 boot-ramp/지터에 지배돼
        # 오탐(상승추세)이므로 추세 미판정(None). 짧은 이력은 어차피 low_precision 으로 다운사이즈 이미 보류.
        util_trend_rising=(
            recommendation.util_trend_rising_from_slopes(raw.cpu_trend_slope, raw.mem_trend_slope)
            if raw.history_hours is not None and raw.history_hours >= recommendation.RS_CONFIDENCE_MIN_HOURS
            else None
        ),
        cpu_steal_p95_pct=raw.cpu_steal_p95_pct,
    )
