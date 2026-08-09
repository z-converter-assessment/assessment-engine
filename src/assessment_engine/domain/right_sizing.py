"""호스트 자원 Right-sizing 판정 규칙.

`rollup_host()`가 CPU, 메모리, 디스크 용량, 디스크 I/O, 네트워크 판정을 합쳐 `HostAssessment`를 반환한다.
"""

import math
from dataclasses import dataclass, field
from typing import Literal

# 평가 기간.
WINDOW_DAYS = 14

# 최소 관측 시간.
CONFIDENCE_MIN_HOURS = 30

# CPU 판정 기준.
CPU_UNDER_PCT = 70
CPU_PERCORE_HOLD_PCT = 85
CPU_STEAL_BIAS_PCT = 5
PROCS_RUNNING_PER_CORE_SATURATION = 1.0
CPU_RUN_QUEUE_PER_CORE_SATURATION = 2.0
IDLE_CPU_P95_PCT = 3
STRONG_IDLE_CPU_PEAK_PCT = 1

# CPU 사이징 기준.
CPU_SIZING_TARGET_PCT = 70
CPU_SAT_HEADROOM = 0.7

# 메모리 판정 기준.
MEM_UNDER_PCT = 90
WIN_PAGES_INPUT_SATURATION = 20.0

# 메모리 사이징 기준.
MEM_SIZING_TARGET_PCT = 80
MEM_SATURATION_HEADROOM_PCT = 30

# 디스크 용량 판정 기준.
DISK_RUNWAY_DAYS = 30
DISK_STATIC_GUARD_PCT = 85  # monitoring 표준(major) — 추세 신뢰도 낮을 때 fallback
DISK_RATE_MIN_SPAN_DAYS = CONFIDENCE_MIN_HOURS / 24

# 디스크 용량 사이징 기준.
DISK_TARGET_RUNWAY_DAYS = 365
DISK_TREND_MIN_SPAN_DAYS = WINDOW_DAYS
DISK_NEAR_HORIZON_DAYS = 30
DISK_HEADROOM_TARGET_PCT = 70

# 디스크 I/O 판정 기준.
DISKIO_AWAIT_MS = 20  # VMware(read >20ms critical) / SQL Server(~10-15ms)
DISKIO_UTIL_MIN = 0.5
DISK_QUEUE_PER_DISK_SATURATION = 2.0
PROCS_BLOCKED_DSTATE_SATURATION = 1.0
IDLE_DISK_BASELINE_IOPS = 5

# 네트워크 판정 기준.
NET_RETRANS_PCT = 1.0
NET_DROP_PCT = 0.5
NET_MIN_TRAFFIC_KBPS = 10.0
CONNTRACK_SATURATION_RATIO = 0.8
IDLE_NET_THROUGHPUT_MBPS = 2
STRONG_IDLE_NET_THROUGHPUT_KBYTES_PER_S = 1

# 신뢰도와 다운사이즈 처방 기준.
DOWNSIZE_MIN_SAMPLE_COVERAGE = 0.7  # CPU와 메모리 유효 5분 버킷 비율 중 작은 값의 다운사이즈 처방 하한이다.
# CPU p95 / 중앙값이 이 값 초과면 부하 변동으로 보고 다운사이즈 신뢰도를 낮춘다.
CPU_P95_TO_MEDIAN_BURST_RATIO_MAX = 2.0

# 보고서 burst 표시와 신뢰도 판단 기준.
CPU_BURST_PEAK_FLOOR_PCT = 30  # peak / p95 변동 비율이 높아도 CPU peak가 이 값 이하면 보고서에 표시하지 않는다.
MEM_BURST_PEAK_FLOOR_PCT = 50  # peak / p95 변동 비율이 높아도 메모리 peak가 이 값 이하면 보고서에 표시하지 않는다.
UTILIZATION_TREND_RISE_PCT_POINTS_PER_DAY = 0.2  # CPU 또는 메모리 이용률 회귀선의 일별 상승률 기준이다.


type Recommendation = Literal[
    "idle",
    "over_provisioned",
    "under_provisioned",
    "optimal",
    "insufficient_data",
]
type ResourceKind = Literal["cpu", "memory", "disk_capacity", "disk_io", "network"]
type TriggerKind = Literal[
    "cpu_util",
    "cpu_saturation",
    "mem_util",
    "mem_saturation",
    "mem_oom",
    "disk_capacity",
    "disk_inode",
    "disk_io",
    "net_retrans",
    "net_drop",
    "net_conntrack",
]
type ResourceStatus = Literal[
    "under",
    "optimal",
    "over",
    "filling",
    "capacity_ok",
    "io_bound",
    "io_ok",
    "congested",
    "quality_ok",
    "unmeasured",
]


@dataclass
class ResourceStats:
    """Right-sizing 판정 입력 통계."""

    cpu_p95_pct: float | None
    cpu_peak_pct: float | None
    cpu_cores: int | None
    mem_p95_pct: float | None
    disk_used_pct: float | None

    net_avg_kbytes_per_s: float | None
    os_family: str | None = None
    sample_sufficiency: float | None = None
    cpu_run_queue_p95: float | None = None
    mem_pages_input_rate_p95: float | None = None
    cpu_percore_p95_max: float | None = None
    procs_blocked_p95: float | None = None
    procs_running_p95: float | None = None
    mem_swap_paging: bool | None = None
    oom_occurred: bool = False
    mem_total_mb: int | None = None
    mem_near_peak_pct: float | None = None
    disk_await_p95_ms: float | None = None
    disk_iops_baseline: float | None = None

    disk_capacity_runway_days: float | None = None
    disk_inode_runway_days: float | None = None
    disk_inode_used_pct: float | None = None
    disk_capacity_target_gb: float | None = None
    net_retrans_pct: float | None = None
    net_drop_pct: float | None = None
    conntrack_ratio: float | None = None
    history_hours: float | None = None
    cpu_burst_ratio: float | None = None
    cpu_utilization_trend_rising: bool | None = None
    memory_utilization_trend_rising: bool | None = None
    cpu_steal_p95_pct: float | None = None


def is_disk_io_saturated(stats: ResourceStats) -> bool | None:
    """디스크 I/O 포화 여부를 반환한다. 미측정이면 None이다."""
    if stats.disk_await_p95_ms is not None:
        return stats.disk_await_p95_ms > DISKIO_AWAIT_MS
    return None


def is_cpu_saturated(stats: ResourceStats) -> bool | None:
    """CPU 포화 여부를 반환한다. 미측정이면 None이다."""
    if stats.cpu_cores is None or stats.cpu_cores <= 0:
        return None
    if stats.os_family == "windows":
        if stats.cpu_run_queue_p95 is None:
            return None
        rq_sat = (stats.cpu_run_queue_p95 / stats.cpu_cores) >= CPU_RUN_QUEUE_PER_CORE_SATURATION
    else:
        if stats.procs_running_p95 is None:
            return None
        rq_sat = (stats.procs_running_p95 / stats.cpu_cores) >= PROCS_RUNNING_PER_CORE_SATURATION
    if not rq_sat:
        return False

    if stats.cpu_p95_pct is None:
        return True
    return stats.cpu_p95_pct >= CPU_UNDER_PCT


def is_memory_saturated(stats: ResourceStats) -> bool | None:
    """메모리 포화 여부를 반환한다. 미측정이면 None이다."""
    if stats.mem_p95_pct is None:
        return None
    if stats.mem_p95_pct < MEM_UNDER_PCT:
        return False
    if stats.os_family == "windows":
        if stats.mem_pages_input_rate_p95 is None:
            return None
        return stats.mem_pages_input_rate_p95 >= WIN_PAGES_INPUT_SATURATION
    return stats.mem_swap_paging


# 호스트 권고 상태의 표시명과 정렬 순서.
RECOMMENDATION_LABEL_KO: dict[Recommendation, str] = {
    "idle": "유휴",
    "over_provisioned": "과다 할당",
    "under_provisioned": "자원 부족",
    "optimal": "정상",
    "insufficient_data": "표본 부족",
}

# 포화 관측 여부를 확인하는 자원.
_SATURATION_KINDS: tuple[ResourceKind, ...] = ("cpu", "memory", "disk_io")


CLASSIFICATION_ORDER: dict[Recommendation, int] = {
    "under_provisioned": 0,
    "over_provisioned": 1,
    "idle": 2,
    "optimal": 3,
    "insufficient_data": 4,
}

# 호스트 권고 상태별 기본 조치 문구.
RECOMMENDATION_ACTION_KO: dict[Recommendation, str] = {
    "under_provisioned": "증설 검토",
    "over_provisioned": "축소 검토",
    "idle": "종료·통합 검토",
    "optimal": "적정 — 유지",
    "insufficient_data": "표본 부족 — 관측 지속",
}


def is_strong_idle(stats: ResourceStats) -> bool:
    """즉시 종료 검토 기준의 강한 유휴 여부를 반환한다."""
    return (
        stats.cpu_peak_pct is not None
        and stats.cpu_peak_pct <= STRONG_IDLE_CPU_PEAK_PCT
        and stats.net_avg_kbytes_per_s is not None
        and stats.net_avg_kbytes_per_s <= STRONG_IDLE_NET_THROUGHPUT_KBYTES_PER_S
    )


def recommend_action(rec: Recommendation, stats: ResourceStats) -> str:
    """권고 카테고리에 맞는 조치 문구를 반환한다."""
    if rec == "idle":
        return "즉시 종료 검토" if is_strong_idle(stats) else "통합·재배치 검토"
    return RECOMMENDATION_ACTION_KO.get(rec, "")


def is_utilization_trend_rising(slope: float | None) -> bool | None:
    """한 자원의 이용률 추세가 상승 기준을 넘는지 반환한다."""
    if slope is None:
        return None
    return slope >= UTILIZATION_TREND_RISE_PCT_POINTS_PER_DAY


@dataclass
class ConfidenceNote:
    """판정 신뢰도 제한 요인."""

    insufficient_history: bool = False
    high_utilization_variability: bool = False
    coverage_gap: bool = False
    measurement_bias: bool = False
    rising_utilization_trend: bool = False

    @property
    def high(self) -> bool:
        return not (
            self.insufficient_history or self.high_utilization_variability or self.coverage_gap or self.measurement_bias
        )


@dataclass
class ResourceAssessment:
    """자원별 Right-sizing 판정 결과."""

    kind: ResourceKind
    status: ResourceStatus
    triggers: list[TriggerKind] = field(default_factory=list[TriggerKind])
    sizing_target: int | None = None
    sizing_floor: int | None = None
    confidence: ConfidenceNote = field(default_factory=ConfidenceNote)
    detail: str = ""


@dataclass
class HostAssessment:
    """호스트 Right-sizing 종합 결과."""

    resources: dict[ResourceKind, ResourceAssessment]
    root_cause: ResourceKind | None = None

    symptom_of_root: list[ResourceKind] = field(default_factory=list[ResourceKind])
    recommendation: Recommendation = "optimal"
    network_congested: bool = False
    sample_sufficiency: float | None = None


def _has_insufficient_history(stats: ResourceStats) -> bool:
    return stats.history_hours is not None and stats.history_hours < CONFIDENCE_MIN_HOURS


def _has_high_cpu_utilization_variability(stats: ResourceStats) -> bool:
    return bool(stats.cpu_burst_ratio is not None and stats.cpu_burst_ratio > CPU_P95_TO_MEDIAN_BURST_RATIO_MAX)


def _initial_confidence(
    stats: ResourceStats,
    *,
    measurement_bias: bool = False,
    high_utilization_variability: bool = False,
    utilization_trend_rising: bool | None = None,
) -> ConfidenceNote:
    return ConfidenceNote(
        insufficient_history=_has_insufficient_history(stats),
        high_utilization_variability=high_utilization_variability,
        rising_utilization_trend=bool(utilization_trend_rising),
        measurement_bias=measurement_bias,
    )


def _cpu_run_queue_p95(stats: ResourceStats) -> float | None:
    return stats.cpu_run_queue_p95 if stats.os_family == "windows" else stats.procs_running_p95


def _cpu_target_cores(
    util_pct: float, cores: int, run_queue: float | None, os_family: str | None, saturated: bool = False
) -> int:
    util_cores = math.ceil(util_pct * cores / CPU_SIZING_TARGET_PCT) if util_pct > 0 else 1
    sat_cores = 0
    if saturated and run_queue and run_queue > 0:
        sat_line = CPU_RUN_QUEUE_PER_CORE_SATURATION if os_family == "windows" else PROCS_RUNNING_PER_CORE_SATURATION
        sat_cores = math.ceil(run_queue / (sat_line * CPU_SAT_HEADROOM))
    return max(1, util_cores, sat_cores)


def assess_cpu(stats: ResourceStats) -> ResourceAssessment:
    """CPU Right-sizing 판정 결과를 반환한다."""
    util = stats.cpu_p95_pct
    cores = stats.cpu_cores
    sat = is_cpu_saturated(stats)
    steal_biased = stats.cpu_steal_p95_pct is not None and stats.cpu_steal_p95_pct >= CPU_STEAL_BIAS_PCT
    conf = _initial_confidence(
        stats,
        measurement_bias=steal_biased,
        high_utilization_variability=_has_high_cpu_utilization_variability(stats),
        utilization_trend_rising=stats.cpu_utilization_trend_rising,
    )
    if sat is None:
        conf.coverage_gap = True
    if util is None:
        conf.coverage_gap = True
    if util is None and not sat:
        return ResourceAssessment("cpu", "unmeasured", confidence=conf, detail="이용률 미측정")
    if cores is None or cores <= 0:
        return ResourceAssessment("cpu", "unmeasured", confidence=conf, detail="코어 수 미상")
    target = _cpu_target_cores(util or 0.0, cores, _cpu_run_queue_p95(stats), stats.os_family, saturated=bool(sat))
    triggers: list[TriggerKind] = []
    if util is not None and util >= CPU_UNDER_PCT:
        triggers.append("cpu_util")
    if sat:
        triggers.append("cpu_saturation")
    percore_busy = stats.cpu_percore_p95_max is not None and stats.cpu_percore_p95_max >= CPU_PERCORE_HOLD_PCT
    if triggers or target > cores:
        up = target if target > cores else None
        floor = None if up is not None else cores + 1
        return ResourceAssessment(
            "cpu",
            "under",
            triggers=triggers,
            sizing_target=up,
            sizing_floor=floor,
            confidence=conf,
            detail=(f"목표 {up}코어" if up else f"증설(최소 {floor}코어)"),
        )
    if target < cores and not percore_busy:
        return ResourceAssessment("cpu", "over", sizing_target=target, confidence=conf, detail=f"목표 {target}코어")
    return ResourceAssessment("cpu", "optimal", sizing_target=cores, confidence=conf)


def _mem_target_mb(near_peak_pct: float, total_mb: int | None) -> int | None:
    if total_mb is None or near_peak_pct <= 0:
        return None
    return math.ceil(total_mb * near_peak_pct / MEM_SIZING_TARGET_PCT)


def _is_memory_saturation_active(stats: ResourceStats) -> bool:
    return bool(is_memory_saturated(stats))


def _memory_increase_target_mb(utilization_target_mb: int | None, stats: ResourceStats) -> int | None:
    total = stats.mem_total_mb
    increase_target_mb = utilization_target_mb
    if total is not None and (_is_memory_saturation_active(stats) or stats.oom_occurred):
        headroom_target_mb = math.ceil(total * (1 + MEM_SATURATION_HEADROOM_PCT / 100))
        increase_target_mb = max(increase_target_mb or 0, headroom_target_mb)
    if increase_target_mb is None:
        return None
    return increase_target_mb if total is None or increase_target_mb > total else None


def assess_memory(stats: ResourceStats) -> ResourceAssessment:
    """메모리 Right-sizing 판정 결과를 반환한다."""
    util = stats.mem_p95_pct
    conf = _initial_confidence(stats, utilization_trend_rising=stats.memory_utilization_trend_rising)
    saturation = is_memory_saturated(stats)
    if saturation is None:
        conf.coverage_gap = True
    if util is None:
        if stats.oom_occurred:
            floor = (
                math.ceil(stats.mem_total_mb * (1 + MEM_SATURATION_HEADROOM_PCT / 100))
                if stats.mem_total_mb is not None
                else None
            )
            return ResourceAssessment(
                "memory",
                "under",
                triggers=["mem_oom"],
                sizing_floor=floor,
                confidence=conf,
                detail="이용률 미측정, OOM 발생",
            )
        conf.coverage_gap = True
        return ResourceAssessment("memory", "unmeasured", confidence=conf, detail="이용률 미측정")
    triggers: list[TriggerKind] = []
    if util >= MEM_UNDER_PCT:
        triggers.append("mem_util")
    if saturation:
        triggers.append("mem_saturation")
    if stats.oom_occurred:
        triggers.append("mem_oom")
    near_peak = stats.mem_near_peak_pct if stats.mem_near_peak_pct is not None else util
    target_mb = _mem_target_mb(near_peak, stats.mem_total_mb)
    if triggers:
        up = _memory_increase_target_mb(target_mb, stats)
        floor = None
        if up is None and stats.mem_total_mb is not None:
            floor = math.ceil(stats.mem_total_mb * (1 + MEM_SATURATION_HEADROOM_PCT / 100))
        return ResourceAssessment(
            "memory",
            "under",
            triggers=triggers,
            sizing_target=up,
            sizing_floor=floor,
            confidence=conf,
            detail=(
                f"목표 {up}MB"
                if up
                else (f"증설(최소 {floor}MB)" if floor is not None else "증설(현재 사양 기준 상향)")
            ),
        )
    if stats.mem_total_mb and target_mb and target_mb < stats.mem_total_mb:
        return ResourceAssessment(
            "memory", "over", sizing_target=target_mb, confidence=conf, detail=f"목표 {target_mb}MB"
        )
    return ResourceAssessment("memory", "optimal", sizing_target=stats.mem_total_mb, confidence=conf)


def _min_runway(*runways: float | None) -> float | None:
    vals = [r for r in runways if r is not None]
    return min(vals) if vals else None


def assess_disk_capacity(stats: ResourceStats) -> ResourceAssessment:
    """디스크 용량 Right-sizing 판정 결과를 반환한다."""
    conf = _initial_confidence(stats)
    byte_runway = stats.disk_capacity_runway_days
    inode_runway = stats.disk_inode_runway_days
    used = stats.disk_used_pct
    inode_used = stats.disk_inode_used_pct
    tgt = stats.disk_capacity_target_gb
    tgt = math.ceil(tgt) if tgt is not None and tgt >= 1 else None
    byte_filling = (byte_runway is not None and byte_runway < DISK_RUNWAY_DAYS) or (
        byte_runway is None and used is not None and used >= DISK_STATIC_GUARD_PCT
    )
    inode_filling = (inode_runway is not None and inode_runway < DISK_RUNWAY_DAYS) or (
        inode_runway is None and inode_used is not None and inode_used >= DISK_STATIC_GUARD_PCT
    )
    if byte_filling or inode_filling:
        triggers: list[TriggerKind] = []
        details: list[str] = []
        if byte_filling:
            triggers.append("disk_capacity")
            if byte_runway is not None:
                details.append(f"바이트 {byte_runway:.0f}일 후 소진")
            else:
                details.append(f"바이트 used {used:.0f}% (정적 가드)")
            if tgt is not None:
                details.append(f"목표 {tgt:.0f}GB")
        if inode_filling:
            triggers.append("disk_inode")
            if inode_runway is not None:
                details.append(f"inode {inode_runway:.0f}일 후 소진")
            else:
                details.append(f"inode used {inode_used:.0f}% (정적 가드)")
        return ResourceAssessment(
            "disk_capacity",
            "filling",
            triggers=triggers,
            sizing_target=tgt if byte_filling else None,
            confidence=conf,
            detail=", ".join(details),
        )
    runway = _min_runway(byte_runway, inode_runway)
    if runway is None and used is None and inode_used is None:
        conf.coverage_gap = True
        return ResourceAssessment("disk_capacity", "unmeasured", confidence=conf, detail="용량 미측정")
    if runway is not None:
        return ResourceAssessment("disk_capacity", "capacity_ok", confidence=conf, detail=f"{runway:.0f}일 여유")
    return ResourceAssessment(
        "disk_capacity", "capacity_ok", confidence=conf, detail=(f"used {used:.0f}%" if used is not None else "여유")
    )


_GIB = 1024**3


@dataclass
class MountSizing:
    """마운트별 용량 사이징 결과."""

    current_gib: int
    recommended_gib: int
    action: Literal["increase", "keep"]
    estimate_quality: Literal["exact", "floor"]
    note: str = ""


def assess_mount_capacity(
    total_bytes: int | None,
    target_bytes: float | None,
    byte_runway_days: float | None,
    used_pct: float | None,
    inode_runway_days: float | None,
    inode_used_pct: float | None,
) -> MountSizing | None:
    """마운트별 용량 사이징 결과를 반환한다. 총용량을 모르면 None이다."""
    if not total_bytes:
        return None
    current_gib = math.ceil(total_bytes / _GIB)
    byte_filling = (byte_runway_days is not None and byte_runway_days < DISK_RUNWAY_DAYS) or (
        byte_runway_days is None and used_pct is not None and used_pct >= DISK_STATIC_GUARD_PCT
    )
    inode_filling = (inode_runway_days is not None and inode_runway_days < DISK_RUNWAY_DAYS) or (
        inode_runway_days is None and inode_used_pct is not None and inode_used_pct >= DISK_STATIC_GUARD_PCT
    )
    inode_note = "inode 소진 — 파일 정리/재포맷(용량 확장 무관)" if inode_filling else ""
    if byte_filling and target_bytes is not None:
        rec_gib = max(current_gib, math.ceil(target_bytes / _GIB))
        action = "increase" if rec_gib > current_gib else "keep"
        return MountSizing(current_gib, rec_gib, action, "exact", note=inode_note)
    if byte_filling:
        floor_gib = max(current_gib, math.ceil(current_gib / (DISK_HEADROOM_TARGET_PCT / 100)))
        return MountSizing(current_gib, floor_gib, "increase", "floor", note=inode_note)
    if inode_filling:
        return MountSizing(
            current_gib,
            current_gib,
            "keep",
            "exact",
            note=inode_note,
        )
    return MountSizing(current_gib, current_gib, "keep", "exact")


def assess_disk_io(stats: ResourceStats) -> ResourceAssessment:
    """디스크 I/O 판정 결과를 반환한다."""
    conf = _initial_confidence(stats)
    sat = is_disk_io_saturated(stats)
    if sat is None:
        if stats.disk_iops_baseline is not None and stats.disk_iops_baseline <= IDLE_DISK_BASELINE_IOPS:
            return ResourceAssessment("disk_io", "io_ok", confidence=conf, detail="device 저활동 (병목 아님)")
        conf.coverage_gap = True
        return ResourceAssessment("disk_io", "unmeasured", confidence=conf, detail="응답 지연 미측정")
    detail = f"await p95 {stats.disk_await_p95_ms:.0f}ms"
    if sat:
        return ResourceAssessment("disk_io", "io_bound", triggers=["disk_io"], confidence=conf, detail=detail)
    return ResourceAssessment("disk_io", "io_ok", confidence=conf, detail=detail)


def has_sufficient_network_traffic(stats: ResourceStats) -> bool:
    """재전송과 드롭 비율을 평가할 수 있는 처리량인지 반환한다."""
    return stats.net_avg_kbytes_per_s is not None and stats.net_avg_kbytes_per_s >= NET_MIN_TRAFFIC_KBPS


def assess_network(stats: ResourceStats) -> ResourceAssessment:
    """네트워크 품질 판정 결과를 반환한다."""
    conf = _initial_confidence(stats)
    retrans = stats.net_retrans_pct
    drop = stats.net_drop_pct
    conntrack = stats.conntrack_ratio

    traffic_sufficient = has_sufficient_network_traffic(stats)
    triggers: list[TriggerKind] = []
    if traffic_sufficient and retrans is not None and retrans > NET_RETRANS_PCT:
        triggers.append("net_retrans")
    if traffic_sufficient and drop is not None and drop > NET_DROP_PCT:
        triggers.append("net_drop")
    if conntrack is not None and conntrack >= CONNTRACK_SATURATION_RATIO:
        triggers.append("net_conntrack")
    if triggers:
        parts: list[str] = []
        if "net_retrans" in triggers and retrans is not None:
            parts.append(f"재전송 {retrans:.1f}%")
        if "net_drop" in triggers and drop is not None:
            parts.append(f"드롭 {drop:.2f}%")
        if "net_conntrack" in triggers and conntrack is not None:
            parts.append(f"conntrack {conntrack * 100:.0f}%")
        return ResourceAssessment("network", "congested", triggers=triggers, confidence=conf, detail=" ".join(parts))
    if retrans is None and drop is None and conntrack is None:
        conf.coverage_gap = True
        return ResourceAssessment("network", "unmeasured", confidence=conf, detail="품질 신호 미측정")
    if not traffic_sufficient:
        return ResourceAssessment("network", "quality_ok", confidence=conf, detail="저트래픽: 재전송/드롭 판정 생략")
    if retrans is None and drop is None:
        conf.coverage_gap = True
        return ResourceAssessment("network", "unmeasured", confidence=conf, detail="재전송/드롭 미측정")
    return ResourceAssessment("network", "quality_ok", confidence=conf)


# 호스트를 자원 부족으로 만드는 상태. filling은 디스크 용량 또는 inode 소진이 임박한 상태다.
_ROOTABLE_UNDER: tuple[ResourceStatus, ...] = ("under", "filling")


def _recommendation(
    stats: ResourceStats,
    res: dict[ResourceKind, ResourceAssessment],
    under_kinds: set[ResourceKind],
) -> Recommendation:
    # 우선순위: 자원 부족, 표본 부족, 유휴, 과다 할당, 정상.
    if under_kinds:
        return "under_provisioned"

    if res["cpu"].status == "unmeasured" and res["memory"].status == "unmeasured":
        return "insufficient_data"
    cpu = stats.cpu_p95_pct
    net = stats.net_avg_kbytes_per_s
    net_mbps = net * 8 / 1000 if net is not None else None

    disk_io_active = stats.disk_iops_baseline is not None and stats.disk_iops_baseline > IDLE_DISK_BASELINE_IOPS
    if (
        cpu is not None
        and cpu <= IDLE_CPU_P95_PCT
        and net_mbps is not None
        and net_mbps <= IDLE_NET_THROUGHPUT_MBPS
        and not disk_io_active
    ):
        return "idle"
    if any(res[k].status == "over" for k in ("cpu", "memory")):
        return "over_provisioned"
    return "optimal"


def rollup_host(stats: ResourceStats) -> HostAssessment:
    """자원별 판정을 합쳐 호스트 상태를 반환한다."""
    res: dict[ResourceKind, ResourceAssessment] = {
        "cpu": assess_cpu(stats),
        "memory": assess_memory(stats),
        "disk_capacity": assess_disk_capacity(stats),
        "disk_io": assess_disk_io(stats),
        "network": assess_network(stats),
    }
    host = HostAssessment(resources=res)
    under_kinds: set[ResourceKind] = {k for k, a in res.items() if a.status in _ROOTABLE_UNDER}
    mem_pressure = res["memory"].status == "under"
    disk_io_pressure = res["disk_io"].status == "io_bound"
    cpu_pressure = res["cpu"].status == "under"
    cpu_saturated = "cpu_saturation" in res["cpu"].triggers
    procs_blocked_high = (
        stats.procs_blocked_p95 is not None and stats.procs_blocked_p95 >= PROCS_BLOCKED_DSTATE_SATURATION
    )
    if mem_pressure and _is_memory_saturation_active(stats) and (disk_io_pressure or cpu_pressure):
        host.root_cause = "memory"
        symptoms: tuple[ResourceKind, ...] = ("disk_io", "cpu")
        host.symptom_of_root = [k for k in symptoms if k in under_kinds or (k == "disk_io" and disk_io_pressure)]
    elif disk_io_pressure and cpu_saturated and procs_blocked_high:
        host.root_cause = "disk_io"
        host.symptom_of_root = ["cpu"] if "cpu" in under_kinds else []
    elif under_kinds:
        root_order: tuple[ResourceKind, ...] = ("memory", "cpu", "disk_capacity")
        for k in root_order:
            if k in under_kinds:
                host.root_cause = k
                break
    host.network_congested = res["network"].status == "congested"
    host.sample_sufficiency = stats.sample_sufficiency
    host.recommendation = _recommendation(stats, res, under_kinds)
    return host


def has_unmeasured_saturation(host: HostAssessment) -> bool:
    """포화 자원 중 미측정 항목이 있는지 반환한다."""
    return any(host.resources[k].confidence.coverage_gap for k in _SATURATION_KINDS if k in host.resources)


# 자원별 기본 조치 문구와 부족 처방 순서.
_RESOURCE_ACTION_BASE: dict[ResourceKind, str] = {
    "cpu": "CPU 증설",
    "memory": "메모리 증설",
    "disk_capacity": "스토리지 확장",
    "disk_io": "디스크 티어 상향",
}
_SIZING_TARGET_LABEL: dict[ResourceKind, str] = {
    "cpu": "CPU",
    "memory": "메모리",
    "disk_capacity": "스토리지",
}
_UNDER_PRESCRIPTION_ORDER: tuple[ResourceKind, ...] = (
    "memory",
    "cpu",
    "disk_capacity",
)


def _format_sizing_amount(kind: ResourceKind, amount: int) -> str:
    if kind == "cpu":
        return f"{amount}코어"
    if kind == "disk_capacity":
        return f"{amount}GB"
    if amount >= 1024:
        return f"{amount / 1024:.1f}GB".replace(".0GB", "GB")
    return f"{amount}MB"


def _sizing_display(kind: ResourceKind, assessment: ResourceAssessment) -> str | None:
    label = _SIZING_TARGET_LABEL.get(kind)
    if label is None:
        return None
    if assessment.sizing_target is not None:
        return f"{label}: {_format_sizing_amount(kind, assessment.sizing_target)}"
    if assessment.sizing_floor is not None:
        return f"{label}: 최소 {_format_sizing_amount(kind, assessment.sizing_floor)}"
    return None


def prescribed_under_kinds(host: HostAssessment) -> list[ResourceKind]:
    """처방이 필요한 자원을 정해진 순서로 반환한다."""
    return [k for k in _UNDER_PRESCRIPTION_ORDER if k in host.resources and host.resources[k].status in _ROOTABLE_UNDER]


def resource_prescription(kind: ResourceKind, ra: ResourceAssessment) -> str:
    """자원별 Right-sizing 조치 문구를 반환한다."""
    sizing_display = _sizing_display(kind, ra)
    if kind == "disk_capacity":
        actions: list[str] = []
        if "disk_capacity" in ra.triggers:
            actions.append(sizing_display or _RESOURCE_ACTION_BASE[kind])
        if "disk_inode" in ra.triggers:
            actions.append("inode 정리/재포맷")
        return " | ".join(actions) if actions else _RESOURCE_ACTION_BASE.get(kind, "")
    if sizing_display is not None:
        return sizing_display
    return _RESOURCE_ACTION_BASE.get(kind, "")


def under_prescription(host: HostAssessment) -> str:
    """호스트의 부족 자원 조치 문구를 결합해 반환한다."""
    return " | ".join(resource_prescription(k, host.resources[k]) for k in prescribed_under_kinds(host))


def root_cause_display(host: HostAssessment) -> str:
    """호스트 근본원인 표시 문구를 반환한다."""
    under = prescribed_under_kinds(host)
    if not under:
        return ""
    if host.symptom_of_root and host.root_cause:
        sym = "·".join(RESOURCE_KIND_LABEL_KO[k] for k in host.symptom_of_root)
        return f"{RESOURCE_KIND_LABEL_KO[host.root_cause]} ({sym} 유발)"
    if len(under) == 1:
        return RESOURCE_KIND_LABEL_KO[under[0]]
    return "·".join(RESOURCE_KIND_LABEL_KO[k] for k in under)


def can_prescribe_downsize(assessment: ResourceAssessment, stats: ResourceStats) -> bool:
    """구체적인 다운사이즈 처방을 제시할 수 있는지 반환한다."""
    if assessment.status != "over":
        return False
    if not assessment.confidence.high:
        return False
    if assessment.confidence.rising_utilization_trend:
        return False
    return not (stats.sample_sufficiency is None or stats.sample_sufficiency < DOWNSIZE_MIN_SAMPLE_COVERAGE)


def prescribable_downsize_kinds(host: HostAssessment, stats: ResourceStats) -> list[ResourceKind]:
    """구체적인 다운사이즈 처방을 낼 수 있는 자원을 반환한다."""
    return [
        kind
        for kind in ("cpu", "memory")
        if (assessment := host.resources.get(kind)) is not None and can_prescribe_downsize(assessment, stats)
    ]


def host_recommendation_action(host: HostAssessment, stats: ResourceStats) -> str:
    """호스트 분류와 신뢰도 게이트를 반영한 조치 문구를 반환한다."""
    if host.recommendation == "under_provisioned":
        return under_prescription(host)
    if host.recommendation == "over_provisioned":
        return "축소 검토" if prescribable_downsize_kinds(host, stats) else "관찰 지속"
    return recommend_action(host.recommendation, stats)


# 자원별 판정 상태의 표시명.
RESOURCE_STATUS_LABEL_KO: dict[ResourceStatus, str] = {
    "under": "부족",
    "optimal": "정상",
    "over": "과다",
    "filling": "소진 임박",
    "capacity_ok": "용량 여유",
    "io_bound": "I/O 병목",
    "io_ok": "I/O 정상",
    "congested": "혼잡",
    "quality_ok": "품질 정상",
    "unmeasured": "미측정",
}

# 자원 종류의 표시명.
RESOURCE_KIND_LABEL_KO: dict[ResourceKind, str] = {
    "cpu": "CPU",
    "memory": "메모리",
    "disk_capacity": "디스크 용량",
    "disk_io": "디스크 I/O",
    "network": "네트워크",
}

# 발화한 판정 근거의 표시명.
TRIGGER_LABEL_KO: dict[TriggerKind, str] = {
    "cpu_util": "CPU 이용률 임계 도달",
    "cpu_saturation": "CPU 실행 큐 포화",
    "mem_util": "메모리 이용률 임계 도달",
    "mem_saturation": "메모리 페이징 발생",
    "mem_oom": "OOM(메모리 부족) 발생",
    "disk_capacity": "디스크 용량 임박",
    "disk_inode": "디스크 inode 소진 임박",
    "disk_io": "디스크 I/O 응답 지연",
    "net_retrans": "TCP 재전송 과다",
    "net_drop": "패킷 드롭 과다",
    "net_conntrack": "연결테이블 고갈 임박",
}
