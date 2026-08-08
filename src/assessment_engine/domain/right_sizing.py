"""Right-sizing 분류 — USE Method + cloud advisor 임계. web·repo 공용 도메인(표시 계층 역의존 0).

명세·판정 순서·OS 분기·한계는 docs/reference/right-sizing.md, 임계 수치와 그 출처는
docs/reference/right-sizing-thresholds.md 가 정본이다.

UI badge 임계(`mappers/constants.py` `_USAGE_DANGER_PCT`)와 수치가 겹쳐도 다른 도메인이다 — 저쪽은
시점 사용량 시각 신호, 여기는 창 통계 기반 사이징 판정이라 같은 90 을 한 상수로 합치면 둘 다 깨진다.
"""

import math
from dataclasses import dataclass, field
from typing import Literal

WINDOW_DAYS = 14


IDLE_CPU_P95_PCT = 3
IDLE_NET_MBPS = 2
IDLE_DISK_IOPS = 5
# 확실 유휴 — AWS Compute Optimizer idle 정의(거의 0). 상태가 아니라 종료 vs 통합 권고를 가르는 조치 강도.
IDLE_STRONG_PEAK_PCT = 1
IDLE_STRONG_NET_KBYTES_PER_S = 1


BURST_PEAK_FLOOR_CPU_PCT = 30
BURST_PEAK_FLOOR_MEM_PCT = 50


PROCS_RUNNING_PER_CORE_SATURATION = 1.0
# Windows Processor Queue Length/코어 (Microsoft "sustained > 2 per CPU"). Linux 1.0 과 값이 다른 건
# 모집단 차이다 — Windows 큐는 ready 대기만 세고 실행 중 스레드를 빼서 같은 포화 지점을 코어당 +1 낮게

CPU_RUN_QUEUE_PER_CORE_SATURATION = 2.0
# Windows 디스크당 Avg Disk Queue Length (Microsoft 병목 기준). 이미 디스크당 값이라 CPU 실행 큐처럼

DISK_QUEUE_PER_DISK_SATURATION = 2.0
# Windows Memory\Pages Input/sec p95 — 총 Pages/sec 과 달리 하드 폴트만 세어 mmap 파일 I/O 가 안 섞인다.

WIN_PAGES_INPUT_SATURATION = 20.0
# procs_blocked(D-state) p95 — 1 이면 적어도 한 프로세스가 상시 IO 블록(Gregg USE). 디스크 I/O 대기로

PROCS_BLOCKED_DSTATE_SATURATION = 1.0


type Recommendation = Literal[
    "idle",
    "over_provisioned",
    "under_provisioned",
    "optimal",
    "insufficient_data",
]


@dataclass
class ResourceStats:
    """USE Method 통계 입력. None = 데이터 부재 — 그 축 평가만 skip 하고 분류는 막지 않는다."""

    cpu_p95_pct: float | None
    cpu_peak_pct: float | None
    cpu_cores: int | None
    mem_p95_pct: float | None
    disk_used_pct: float | None

    net_avg_kbytes_per_s: float | None
    os_family: str | None = None  # None(unknown)은 Linux 의미로 해석
    sample_sufficiency: float | None = None
    disk_queue_p95: float | None = None
    cpu_run_queue_p95: float | None = None  # Windows Processor Queue Length p95 (Linux procs_running 등가)
    mem_pages_input_rate_p95: float | None = None  # Windows Pages Input/sec p95 (Linux 하드폴트 등가)
    cpu_percore_p95_max: float | None = None
    procs_blocked_p95: float | None = None
    procs_running_p95: float | None = None
    mem_swap_paging: bool = False
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
    util_trend_rising: bool | None = None
    cpu_steal_p95_pct: float | None = None


def disk_io_saturated(stats: ResourceStats) -> bool | None:
    """디스크 I/O 포화 여부 (os-aware). None = 미측정 -> 호출부가 unmeasured 로 표시.

    iowait 대신 await — iowait 은 게스트 CPU 스케줄링 왜곡에 오염되고(virtio), await 는 디바이스 지연을
    직접 잰다. await 를 못 읽는 구세대 viostor(IOCTL 미부착) Windows 만 큐 깊이로 폴백한다.
    """
    if stats.disk_await_p95_ms is not None:
        return stats.disk_await_p95_ms > DISKIO_AWAIT_MS
    if stats.os_family == "windows" and stats.disk_queue_p95 is not None:
        return stats.disk_queue_p95 >= DISK_QUEUE_PER_DISK_SATURATION
    return None


def cpu_saturated(stats: ResourceStats) -> bool | None:
    """CPU 실행 큐 포화 여부 (os-aware). 측정 불가(값 None·코어 미상)면 None -> unmeasured.

    Windows 는 loadavg 개념이 없어 agent 가 Processor Queue Length 를 대신 발행한다.
    loadavg 와 iowait 은 수집해도 판정 입력으로 쓰지 않는다 — 디스크 포화는 await 로 본다.
    """
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


def cpu_saturation_index(run_queue: float | None, cores: int | None, os_family: str | None) -> float | None:
    """CPU 포화 지수 = (실행 큐/코어) / OS별 임계. 1.0 이상이면 포화.

    run_queue 는 호출자가 넘기는 OS-neutral gauge(Linux procs_running / Windows Processor Queue Length)로
    cpu_saturated 와 같은 신호다. 임계로 정규화해 Linux(1.0)·Windows(2.0) 호스트를 OS 분기 없이 한 축으로
    비교·랭킹한다.
    """
    if run_queue is None or not cores:
        return None
    threshold = CPU_RUN_QUEUE_PER_CORE_SATURATION if os_family == "windows" else PROCS_RUNNING_PER_CORE_SATURATION
    return (run_queue / cores) / threshold


def disk_io_saturation_index(await_ms: float | None, disk_queue: float | None, os_family: str | None) -> float | None:
    """디스크 I/O 포화 지수 = 현재값 / 임계. 1.0 이상이면 포화 — OS 무관 단일 축 랭킹용.

    await 우선(양 OS 통일), Windows await 미측정이면 큐 깊이 폴백 — disk_io_saturated 와 같은 신호 선택.
    """
    if await_ms is not None:
        return await_ms / DISKIO_AWAIT_MS
    if os_family == "windows" and disk_queue is not None:
        return disk_queue / DISK_QUEUE_PER_DISK_SATURATION
    return None


def mem_pressure_active(paging_major_rate: float | None, os_family: str | None) -> bool:
    """실시간 메모리 압박 여부 — Windows Pages Input/sec 임계 / Linux refault 발생.

    paging_major_rate 는 호출자(get_latest_saturation SQL)가 os_family 로 물리 컬럼(Linux paging_major /
    Windows paging_in)을 골라 넘긴 OS-neutral 값이다. 메모리만 지수가 아닌 압박 불리언으로 집계한다 —
    mem_saturated 가 dual-gate bool 이라 CPU·디스크처럼 임계로 나눈 연속 지수를 만들 수 없다.
    """
    if paging_major_rate is None:
        return False
    if os_family == "windows":
        return paging_major_rate >= WIN_PAGES_INPUT_SATURATION
    return paging_major_rate > 0


def net_signal_active(
    retrans_pct: float | None,
    drop_pct: float | None,
    conntrack_ratio: float | None,
    net_kbytes_per_s: float | None,
) -> bool:
    low_traffic = net_kbytes_per_s is not None and net_kbytes_per_s < NET_MIN_TRAFFIC_KBPS
    if not low_traffic and retrans_pct is not None and retrans_pct > NET_RETRANS_PCT:
        return True
    if not low_traffic and drop_pct is not None and drop_pct > NET_DROP_PCT:
        return True
    return conntrack_ratio is not None and conntrack_ratio >= CONNTRACK_SATURATION_RATIO


def mem_saturated(stats: ResourceStats) -> bool | None:
    """메모리 포화 여부 — 이용률 AND 페이징 dual-gate (os-aware). 이용률 미측정이면 None.

    단독 신호는 양쪽 다 오탐한다 — 페이징만 보면 mmap·프로세스 시작의 정상 하드폴트를 포화로 읽고,
    이용률만 보면 페이지캐시로 찬 정상 호스트를 잡는다.
    정적 스왑 점유는 입력이 아니다 — swappiness 가 여유 RAM 에서도 유휴 페이지를 스왑아웃한다.
    """
    if stats.mem_p95_pct is None:
        return None
    if stats.mem_p95_pct < MEM_UNDER_PCT:
        return False
    if stats.os_family == "windows":
        if stats.mem_pages_input_rate_p95 is None:
            return None
        return stats.mem_pages_input_rate_p95 >= WIN_PAGES_INPUT_SATURATION
    return stats.mem_swap_paging


RECOMMENDATION_LABEL_KO: dict[Recommendation, str] = {
    "idle": "유휴",
    "over_provisioned": "과다 할당",
    "under_provisioned": "자원 부족",
    "optimal": "정상",
    "insufficient_data": "표본 부족",
}

_HOST_STATUS_TO_REC: dict[HostStatus, Recommendation] = {
    "under": "under_provisioned",
    "idle": "idle",
    "over": "over_provisioned",
    "optimal": "optimal",
    "insufficient": "insufficient_data",
}


def classify_host(stats: ResourceStats) -> Recommendation:
    return _HOST_STATUS_TO_REC[rollup_host(stats).host_status]


def host_status_to_recommendation(status: HostStatus) -> Recommendation:
    return _HOST_STATUS_TO_REC[status]


_SATURATION_KINDS: tuple[ResourceKind, ...] = ("cpu", "memory", "disk_io")


CLASSIFICATION_ORDER: dict[Recommendation, int] = {
    "under_provisioned": 0,
    "over_provisioned": 1,
    "idle": 2,
    "optimal": 3,
    "insufficient_data": 4,
}


RECOMMENDATION_ACTION_KO: dict[Recommendation, str] = {
    "under_provisioned": "증설 검토",
    "over_provisioned": "축소 검토",
    "idle": "종료·통합 검토",
    "optimal": "적정 — 유지",
    "insufficient_data": "표본 부족 — 관측 지속",
}


def is_idle_strong(stats: ResourceStats) -> bool:
    return (
        stats.cpu_peak_pct is not None
        and stats.cpu_peak_pct <= IDLE_STRONG_PEAK_PCT
        and stats.net_avg_kbytes_per_s is not None
        and stats.net_avg_kbytes_per_s <= IDLE_STRONG_NET_KBYTES_PER_S
    )


def recommend_action(rec: Recommendation, stats: ResourceStats) -> str:
    if rec == "idle":
        return "즉시 종료 검토" if is_idle_strong(stats) else "통합·재배치 검토"
    return RECOMMENDATION_ACTION_KO.get(rec, "")


CPU_UNDER_PCT = 70
CPU_SIZING_TARGET_PCT = 70
CPU_SAT_HEADROOM = 0.7
CPU_PERCORE_HOLD_PCT = 85
CPU_STEAL_BIAS_PCT = 5
MEM_UNDER_PCT = 90
MEM_SIZING_TARGET_PCT = 80
DISK_RUNWAY_DAYS = 30
DISK_TARGET_RUNWAY_DAYS = 365

DISK_TREND_MIN_SPAN_DAYS = WINDOW_DAYS

DISK_NEAR_HORIZON_DAYS = 30

DISK_HEADROOM_TARGET_PCT = 70
DISK_STATIC_GUARD_PCT = 85  # monitoring 표준(major) — 추세 신뢰도 낮을 때 fallback
DISKIO_AWAIT_MS = 20  # VMware(read >20ms critical) / SQL Server(~10-15ms)


DISKIO_UTIL_MIN = 0.5
NET_RETRANS_PCT = 1.0
NET_DROP_PCT = 0.5


NET_MIN_TRAFFIC_KBPS = 10.0

CONNTRACK_SATURATION_RATIO = 0.8
CONFIDENCE_MIN_HOURS = 30


DISK_RATE_MIN_SPAN_DAYS = CONFIDENCE_MIN_HOURS / 24


DOWNSIZE_MIN_SUFFICIENCY = 0.7
BURST_RATIO_MAX = 2.0


UTIL_TREND_RISING_PCT_PER_DAY = 0.2


_CPU_SAT_LINE = {"windows": CPU_RUN_QUEUE_PER_CORE_SATURATION}
_CPU_SAT_LINE_DEFAULT = PROCS_RUNNING_PER_CORE_SATURATION  # Linux/unknown


def util_trend_rising_from_slopes(cpu_slope: float | None, mem_slope: float | None) -> bool | None:
    slopes = [s for s in (cpu_slope, mem_slope) if s is not None]
    if not slopes:
        return None
    return any(s >= UTIL_TREND_RISING_PCT_PER_DAY for s in slopes)


type ResourceKind = Literal["cpu", "memory", "disk_capacity", "disk_io", "network"]
type TriggerKind = Literal[
    "cpu_util",
    "cpu_saturation",
    "mem_util",
    "mem_saturation",
    "mem_oom",
    "disk_capacity",
    "disk_io",
    "net_retrans",
    "net_drop",
    "net_conntrack",
]
"""판정 근거 키 — `TRIGGER_LABEL_KO` 와 1:1."""
type ResourceStatus = Literal[
    "under",
    "optimal",
    "over",
    "insufficient",
    "filling",
    "capacity_ok",
    "io_bound",
    "io_ok",
    "congested",
    "quality_ok",
    "unmeasured",
]


@dataclass
class ConfidenceNote:
    """신뢰도 4종 불확실성 — 종류가 다르면 대응도 다르다."""

    low_precision: bool = False
    coverage_gap: bool = False
    biased: bool = False
    nonstationary: bool = False

    @property
    def high(self) -> bool:
        return not (self.low_precision or self.coverage_gap or self.biased)


@dataclass
class ResourceAssessment:
    """자원 하나의 USE 판정 결과."""

    kind: ResourceKind
    status: ResourceStatus
    triggers: list[TriggerKind] = field(default_factory=list[TriggerKind])
    sizing_target: int | None = None
    sizing_floor: int | None = None
    confidence: ConfidenceNote = field(default_factory=ConfidenceNote)
    detail: str = ""


type HostStatus = Literal["under", "idle", "over", "optimal", "insufficient"]


@dataclass
class HostAssessment:
    """호스트 종합 — 자원별 판정 + 근본원인 + 호스트 요약 상태."""

    resources: dict[ResourceKind, ResourceAssessment]
    root_cause: ResourceKind | None = None

    symptom_of_root: list[ResourceKind] = field(default_factory=list[ResourceKind])
    host_status: HostStatus = "optimal"
    network_congested: bool = False
    sample_sufficiency: float | None = None


def _low_precision(stats: ResourceStats) -> bool:
    if stats.history_hours is not None and stats.history_hours < CONFIDENCE_MIN_HOURS:
        return True
    return bool(stats.cpu_burst_ratio is not None and stats.cpu_burst_ratio > BURST_RATIO_MAX)


def _base_confidence(stats: ResourceStats, *, biased: bool = False, util_bearing: bool = False) -> ConfidenceNote:
    return ConfidenceNote(
        low_precision=_low_precision(stats),
        nonstationary=bool(stats.util_trend_rising) if util_bearing else False,
        biased=biased,
    )


def _run_queue_value(stats: ResourceStats) -> float | None:
    return stats.cpu_run_queue_p95 if stats.os_family == "windows" else stats.procs_running_p95


def _cpu_target_cores(
    util_pct: float, cores: int, run_queue: float | None, os_family: str | None, saturated: bool = False
) -> int:
    util_cores = math.ceil(util_pct * cores / CPU_SIZING_TARGET_PCT) if util_pct > 0 else 1
    sat_cores = 0
    if saturated and run_queue and run_queue > 0:
        sat_line = _CPU_SAT_LINE.get(os_family or "", _CPU_SAT_LINE_DEFAULT)
        sat_cores = math.ceil(run_queue / (sat_line * CPU_SAT_HEADROOM))
    return max(1, util_cores, sat_cores)


def assess_cpu(stats: ResourceStats) -> ResourceAssessment:
    util = stats.cpu_p95_pct
    cores = stats.cpu_cores
    sat = cpu_saturated(stats)
    steal_biased = stats.cpu_steal_p95_pct is not None and stats.cpu_steal_p95_pct >= CPU_STEAL_BIAS_PCT
    conf = _base_confidence(stats, biased=steal_biased, util_bearing=True)
    if sat is None:
        conf.coverage_gap = True
    if util is None:
        conf.coverage_gap = True  # 분류 결정적 입력 결손 — Windows 이용률 블라인드 노출
    if util is None and not sat:
        return ResourceAssessment("cpu", "unmeasured", confidence=conf, detail="이용률 미측정")
    if cores is None or cores <= 0:
        return ResourceAssessment("cpu", "unmeasured", confidence=conf, detail="코어 수 미상")
    target = _cpu_target_cores(util or 0.0, cores, _run_queue_value(stats), stats.os_family, saturated=bool(sat))
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
            detail=(f"목표 {up}코어" if up else f"포화 주도 — 증설(최소 {cores + 1}코어)"),
        )
    if target < cores and not percore_busy:
        return ResourceAssessment("cpu", "over", sizing_target=target, confidence=conf, detail=f"목표 {target}코어")
    return ResourceAssessment("cpu", "optimal", sizing_target=cores, confidence=conf)


def _mem_target_mb(near_peak_pct: float, total_mb: int | None) -> int | None:
    if total_mb is None or near_peak_pct <= 0:
        return None
    return math.ceil(total_mb * near_peak_pct / MEM_SIZING_TARGET_PCT)


MEM_SATURATION_HEADROOM_PCT = 30


def _mem_paging_active(stats: ResourceStats) -> bool:
    return bool(mem_saturated(stats))


def _mem_under_target(util_target: int | None, stats: ResourceStats) -> int | None:
    total = stats.mem_total_mb
    candidates = [t for t in (util_target,) if t is not None]
    if total is not None and (_mem_paging_active(stats) or stats.oom_occurred):
        candidates.append(math.ceil(total * (1 + MEM_SATURATION_HEADROOM_PCT / 100)))
    if not candidates:
        return None
    up = max(candidates)
    return up if (total is None or up > total) else None


def assess_memory(stats: ResourceStats) -> ResourceAssessment:
    util = stats.mem_p95_pct
    conf = _base_confidence(stats, util_bearing=True)
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
    if _mem_paging_active(stats):
        triggers.append("mem_saturation")
    if stats.oom_occurred:
        triggers.append("mem_oom")
    near_peak = stats.mem_near_peak_pct if stats.mem_near_peak_pct is not None else util
    target_mb = _mem_target_mb(near_peak, stats.mem_total_mb)
    if triggers:
        up = _mem_under_target(target_mb, stats)
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
            detail=(f"목표 {up}MB" if up else "증설(현재 사양 기준 상향)"),
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
    conf = _base_confidence(stats)
    runway = _min_runway(stats.disk_capacity_runway_days, stats.disk_inode_runway_days)
    used = stats.disk_used_pct
    inode_used = stats.disk_inode_used_pct
    # 상류가 CEIL 로 내지만 0·음수·float 를 방어한다 (sizing_target 은 양의 정수 계약).
    tgt = stats.disk_capacity_target_gb
    tgt = math.ceil(tgt) if tgt is not None and tgt >= 1 else None
    if runway is not None and runway < DISK_RUNWAY_DAYS:
        rtgt = tgt if runway == stats.disk_capacity_runway_days else None
        detail = f"{runway:.0f}일 후 소진" + (f", 목표 {rtgt:.0f}GB" if rtgt else "")
        return ResourceAssessment(
            "disk_capacity", "filling", triggers=["disk_capacity"], sizing_target=rtgt, confidence=conf, detail=detail
        )

    _guard = DISK_STATIC_GUARD_PCT
    byte_static = stats.disk_capacity_runway_days is None and used is not None and used >= _guard
    inode_static = stats.disk_inode_runway_days is None and inode_used is not None and inode_used >= _guard
    if byte_static:
        detail = f"used {used:.0f}% (정적 가드)" + (f", 목표 {tgt:.0f}GB" if tgt else "")
        return ResourceAssessment(
            "disk_capacity", "filling", triggers=["disk_capacity"], sizing_target=tgt, confidence=conf, detail=detail
        )
    if inode_static:
        return ResourceAssessment(
            "disk_capacity",
            "filling",
            triggers=["disk_capacity"],
            confidence=conf,
            detail=f"inode used {inode_used:.0f}% (정적 가드)",
        )
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
    """마운트 하나의 용량 사이징 — /api/assessment per-mount 디스크 축 입력.

    current/recommended 는 같은 fs 총용량 기준이고 단위는 GiB(2^30) ceil 이다(하향 오차 방지).
    """

    current_gib: int
    recommended_gib: int
    action: str
    estimate_quality: str
    note: str = ""


def assess_mount_capacity(
    total_bytes: int | None,
    target_bytes: float | None,
    byte_runway_days: float | None,
    used_pct: float | None,
    inode_runway_days: float | None,
    inode_used_pct: float | None,
) -> MountSizing | None:
    if not total_bytes:
        return None
    current_gib = math.ceil(total_bytes / _GIB)
    byte_filling = (byte_runway_days is not None and byte_runway_days < DISK_RUNWAY_DAYS) or (
        byte_runway_days is None and used_pct is not None and used_pct >= DISK_STATIC_GUARD_PCT
    )
    inode_filling = (inode_runway_days is not None and inode_runway_days < DISK_RUNWAY_DAYS) or (
        inode_runway_days is None and inode_used_pct is not None and inode_used_pct >= DISK_STATIC_GUARD_PCT
    )
    if byte_filling and target_bytes is not None:
        rec_gib = max(current_gib, math.ceil(target_bytes / _GIB))
        action = "increase" if rec_gib > current_gib else "keep"
        return MountSizing(current_gib, rec_gib, action, "exact")
    if byte_filling:
        floor_gib = max(current_gib, math.ceil(current_gib / (DISK_HEADROOM_TARGET_PCT / 100)))
        return MountSizing(current_gib, floor_gib, "increase", "floor")
    if inode_filling:
        return MountSizing(
            current_gib,
            current_gib,
            "keep",
            "exact",
            note="inode 소진 — 파일 정리/재포맷(용량 확장 무관)",
        )
    return MountSizing(current_gib, current_gib, "keep", "exact")


def assess_disk_io(stats: ResourceStats) -> ResourceAssessment:
    conf = _base_confidence(stats, biased=True)
    sat = disk_io_saturated(stats)
    if sat is None:
        if stats.disk_iops_baseline is not None:
            return ResourceAssessment("disk_io", "io_ok", confidence=conf, detail="device 저활동 (병목 아님)")
        conf.coverage_gap = True
        return ResourceAssessment("disk_io", "unmeasured", confidence=conf, detail="응답 지연/큐 미측정")
    if stats.disk_await_p95_ms is not None:
        detail = f"await p95 {stats.disk_await_p95_ms:.0f}ms"
    else:
        detail = f"disk queue p95 {stats.disk_queue_p95:.1f}"
    if sat:
        return ResourceAssessment("disk_io", "io_bound", triggers=["disk_io"], confidence=conf, detail=detail)
    return ResourceAssessment("disk_io", "io_ok", confidence=conf, detail=detail)


def assess_network(stats: ResourceStats) -> ResourceAssessment:
    conf = _base_confidence(stats)
    retrans = stats.net_retrans_pct
    drop = stats.net_drop_pct
    conntrack = stats.conntrack_ratio

    low_traffic = stats.net_avg_kbytes_per_s is not None and stats.net_avg_kbytes_per_s < NET_MIN_TRAFFIC_KBPS
    triggers: list[TriggerKind] = []
    if not low_traffic and retrans is not None and retrans > NET_RETRANS_PCT:
        triggers.append("net_retrans")
    if not low_traffic and drop is not None and drop > NET_DROP_PCT:
        triggers.append("net_drop")
    if conntrack is not None and conntrack >= CONNTRACK_SATURATION_RATIO:
        triggers.append("net_conntrack")
    if triggers:
        parts: list[str] = []
        if retrans is not None:
            parts.append(f"재전송 {retrans:.1f}%")
        if drop is not None:
            parts.append(f"드롭 {drop:.2f}%")
        if "net_conntrack" in triggers and conntrack is not None:
            parts.append(f"conntrack {conntrack * 100:.0f}%")
        return ResourceAssessment("network", "congested", triggers=triggers, confidence=conf, detail=" ".join(parts))
    if retrans is None and drop is None and conntrack is None:
        conf.coverage_gap = True
        return ResourceAssessment("network", "unmeasured", confidence=conf, detail="품질 신호 미측정")
    return ResourceAssessment("network", "quality_ok", confidence=conf)


_ROOTABLE_UNDER: tuple[ResourceStatus, ...] = ("under", "filling")


def _host_status(
    stats: ResourceStats,
    res: dict[ResourceKind, ResourceAssessment],
    under_kinds: set[ResourceKind],
) -> HostStatus:
    if under_kinds:
        return "under"

    if res["cpu"].status in ("unmeasured", "insufficient") and res["memory"].status in ("unmeasured", "insufficient"):
        return "insufficient"
    cpu = stats.cpu_p95_pct
    net = stats.net_avg_kbytes_per_s
    net_mbps = net * 8 / 1000 if net is not None else None

    disk_io_active = stats.disk_iops_baseline is not None and stats.disk_iops_baseline > IDLE_DISK_IOPS
    if (
        cpu is not None
        and cpu <= IDLE_CPU_P95_PCT
        and net_mbps is not None
        and net_mbps <= IDLE_NET_MBPS
        and not disk_io_active
    ):
        return "idle"
    if any(res[k].status == "over" for k in ("cpu", "memory")):
        return "over"
    return "optimal"


def rollup_host(stats: ResourceStats) -> HostAssessment:
    """호스트 종합 — 5자원 판정 후 인과 근본원인(root_cause)을 짚는다.

    인과 사슬은 메모리 -> 디스크 I/O -> CPU 이고 판별 신호는 페이징 / procs_blocked(D-state) / await 다.
    root_cause·symptom_of_root 는 진단 근거일 뿐 처방을 억제하지 않는다(prescribed_under_kinds).
    """
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
    procs_blocked_high = (
        stats.procs_blocked_p95 is not None and stats.procs_blocked_p95 >= PROCS_BLOCKED_DSTATE_SATURATION
    )
    if mem_pressure and _mem_paging_active(stats) and (disk_io_pressure or cpu_pressure):
        host.root_cause = "memory"
        symptoms: tuple[ResourceKind, ...] = ("disk_io", "cpu")
        host.symptom_of_root = [k for k in symptoms if k in under_kinds or (k == "disk_io" and disk_io_pressure)]
    elif disk_io_pressure and cpu_pressure and procs_blocked_high:
        host.root_cause = "disk_io"
        host.symptom_of_root = ["cpu"] if "cpu" in under_kinds else []
    elif under_kinds:
        root_order: tuple[ResourceKind, ...] = ("memory", "disk_io", "cpu", "disk_capacity")
        for k in root_order:
            if k in under_kinds:
                host.root_cause = k
                break
    host.network_congested = res["network"].status == "congested"
    host.sample_sufficiency = stats.sample_sufficiency
    host.host_status = _host_status(stats, res, under_kinds)
    return host


def host_saturation_unmeasured(host: HostAssessment) -> bool:
    """포화 축(cpu·memory·disk_io) 중 하나라도 미관측인지 — is_partial·'포화 수치 미관측' 단일 진실.

    용량(누적)·네트워크(품질)는 포화 축이 아니라 뺀다. Windows perflib 미발행·구세대 viostor await
    미측정이 여기로 노출된다.
    """
    return any(host.resources[k].confidence.coverage_gap for k in _SATURATION_KINDS if k in host.resources)


_UNDER_ACTION_BASE: dict[ResourceKind, str] = {
    "cpu": "CPU 증설",
    "memory": "메모리 증설",
    "disk_capacity": "스토리지 확장",
    "disk_io": "디스크 티어 상향",
    "network": "네트워크 점검",
}
_SIZEABLE_LABEL: dict[ResourceKind, str] = {"cpu": "CPU", "memory": "메모리"}
_UNDER_ORDER: tuple[ResourceKind, ...] = (
    "memory",
    "cpu",
    "disk_io",
    "disk_capacity",
    "network",
)


def _format_sizing_target(kind: ResourceKind, target: int) -> str:
    if kind == "cpu":
        return f"{target}코어"
    if target >= 1024:
        return f"{target / 1024:.1f}GB".replace(".0GB", "GB")
    return f"{target}MB"


def prescribed_under_kinds(host: HostAssessment) -> list[ResourceKind]:
    return [k for k in _UNDER_ORDER if k in host.resources and host.resources[k].status in _ROOTABLE_UNDER]


def resource_prescription(kind: ResourceKind, ra: ResourceAssessment) -> str:
    if kind == "disk_capacity" and ra.sizing_target is not None:
        return f"스토리지: {ra.sizing_target:.0f}GB"
    if kind in _SIZEABLE_LABEL and ra.sizing_target is not None:
        return f"{_SIZEABLE_LABEL[kind]}: {_format_sizing_target(kind, ra.sizing_target)}"
    return _UNDER_ACTION_BASE[kind]


def under_prescription(host: HostAssessment) -> str:
    return " | ".join(resource_prescription(k, host.resources[k]) for k in prescribed_under_kinds(host))


def root_cause_display(host: HostAssessment) -> str:
    under = prescribed_under_kinds(host)
    if not under:
        return ""
    if host.symptom_of_root and host.root_cause:
        sym = "·".join(RESOURCE_KIND_LABEL_KO[k] for k in host.symptom_of_root)
        return f"{RESOURCE_KIND_LABEL_KO[host.root_cause]} ({sym} 유발)"
    if len(under) == 1:
        return RESOURCE_KIND_LABEL_KO[under[0]]
    return "·".join(RESOURCE_KIND_LABEL_KO[k] for k in under)


def downsize_prescribable(assessment: ResourceAssessment, stats: ResourceStats) -> bool:
    if assessment.status != "over":
        return False
    if not assessment.confidence.high:
        return False
    if assessment.confidence.nonstationary:
        return False
    return not (stats.sample_sufficiency is None or stats.sample_sufficiency < DOWNSIZE_MIN_SUFFICIENCY)


STATUS_LABEL_KO: dict[ResourceStatus, str] = {
    "under": "부족",
    "optimal": "정상",
    "over": "과다",
    "filling": "용량 임박",
    "capacity_ok": "용량 여유",
    "io_bound": "I/O 병목",
    "io_ok": "I/O 정상",
    "congested": "혼잡",
    "quality_ok": "품질 정상",
    "unmeasured": "미측정",
    "insufficient": "표본 부족",
}

HOST_STATUS_LABEL_KO: dict[HostStatus, str] = {
    "under": "자원 부족",
    "idle": "유휴",
    "over": "과다 할당",
    "optimal": "정상",
    "insufficient": "표본 부족",
}

RESOURCE_KIND_LABEL_KO: dict[ResourceKind, str] = {
    "cpu": "CPU",
    "memory": "메모리",
    "disk_capacity": "디스크 용량",
    "disk_io": "디스크 I/O",
    "network": "네트워크",
}

TRIGGER_LABEL_KO: dict[TriggerKind, str] = {
    "cpu_util": "CPU 이용률 초과",
    "cpu_saturation": "CPU 실행 큐 포화",
    "mem_util": "메모리 이용률 초과",
    "mem_saturation": "메모리 스왑/페이징 발생",
    "mem_oom": "OOM(메모리 부족) 발생",
    "disk_capacity": "디스크 용량 임박",
    "disk_io": "디스크 I/O 응답 지연",
    "net_retrans": "TCP 재전송 과다",
    "net_drop": "패킷 드롭 과다",
    "net_conntrack": "연결테이블 고갈 임박",
}
