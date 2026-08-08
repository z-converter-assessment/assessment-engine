"""ReportRowRaw -> 도메인 분류 입력(`right_sizing.ResourceStats`) 어댑터.

표시 파생이 하나도 없다 — 단위 변환도 배지도 정렬도 없고, 집계 raw 를 도메인 축 이름으로 옮겨 담기만 한다.
그래서 표시 mapper 안에 있을 이유가 없고, 표시 모듈 둘(report·server)이 서로를 import 하던 순환도 여기로
내려오면서 끊긴다.

report·attention·서버목록·환경이 전부 이 함수 하나를 거쳐 `rollup_host` 를 탄다 — 화면 간 분류 정합의
근거이고, 임계를 다시 계산하는 경로가 생기면 그 정합이 깨진다.
"""

from typing import TYPE_CHECKING

from assessment_engine.domain import right_sizing

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import ReportRowRaw


def build_resource_stats(raw: ReportRowRaw, *, disk_baseline: int | None) -> right_sizing.ResourceStats:
    net_avg = (
        None if raw.net_rx_kbps is None and raw.net_tx_kbps is None else (raw.net_rx_kbps or 0) + (raw.net_tx_kbps or 0)
    )

    suffs = [
        s
        for p95, s in ((raw.cpu_p95_pct, raw.cpu_sufficiency), (raw.mem_p95_pct, raw.mem_sufficiency))
        if p95 is not None and s is not None
    ]
    return right_sizing.ResourceStats(
        cpu_p95_pct=raw.cpu_p95_pct,
        cpu_peak_pct=raw.cpu_peak_pct,
        cpu_cores=raw.cpu_cores,
        mem_p95_pct=raw.mem_p95_pct,
        mem_near_peak_pct=raw.mem_near_peak_pct,
        disk_used_pct=raw.worst_mount_used_pct,
        net_avg_kbytes_per_s=net_avg,
        os_family=raw.os_family,
        sample_sufficiency=min(suffs) if suffs else None,
        # Windows 포화 축 — Processor Queue Length p95 / Pages Input/sec rate p95 (os-aware helper 소비).
        cpu_run_queue_p95=raw.cpu_run_queue_p95,
        mem_pages_input_rate_p95=raw.mem_pages_input_rate_p95,
        # 가장 바쁜 코어 p95 — 단일스레드 병목 판정용. Windows·구 agent 는 None(graceful skip).
        cpu_percore_p95_max=raw.cpu_percore_p95_max,
        procs_blocked_p95=raw.procs_blocked_p95,
        # Linux CPU 포화 신호 + OOM 메모리 증거 (os-aware helper 소비).
        procs_running_p95=raw.procs_running_p95,
        oom_occurred=raw.oom_occurred,
        mem_swap_paging=raw.mem_swap_paging,
        mem_total_mb=(raw.mem_total_bytes // 1024**2 if raw.mem_total_bytes is not None else None),
        disk_await_p95_ms=raw.disk_await_p95_ms,
        disk_iops_baseline=disk_baseline,
        disk_capacity_runway_days=raw.disk_capacity_runway_days,
        disk_inode_runway_days=raw.disk_inode_runway_days,
        disk_inode_used_pct=raw.disk_inode_used_pct,
        disk_capacity_target_gb=raw.disk_capacity_target_gb,
        net_retrans_pct=raw.net_retrans_pct,
        net_drop_pct=raw.net_drop_pct,
        conntrack_ratio=raw.conntrack_ratio,
        history_hours=raw.history_hours,
        cpu_burst_ratio=raw.cpu_burst_ratio,
        util_trend_rising=(
            right_sizing.util_trend_rising_from_slopes(raw.cpu_trend_slope, raw.mem_trend_slope)
            if raw.history_hours is not None and raw.history_hours >= right_sizing.CONFIDENCE_MIN_HOURS
            else None
        ),
        cpu_steal_p95_pct=raw.cpu_steal_p95_pct,
    )
