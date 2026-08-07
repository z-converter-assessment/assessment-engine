"""Right-sizing 분류 — USE Method + cloud advisor 임계. web·repo 공용 도메인(표시 계층 역의존 0).

명세·판정 순서·OS 분기·한계는 docs/reference/right-sizing.md, 임계 수치와 그 출처는
docs/reference/right-sizing-thresholds.md 가 정본이다.

UI badge 임계(`mappers/constants.py` `_USAGE_DANGER_PCT`)와 수치가 겹쳐도 다른 도메인이다 — 저쪽은
시점 사용량 시각 신호, 여기는 창 통계 기반 사이징 판정이라 같은 90 을 한 상수로 합치면 둘 다 깨진다.
"""

import math
from dataclasses import dataclass, field
from typing import Literal

# 14일 = AWS Compute Optimizer 기본 lookback. 분류·신뢰도 입력이 모두 이 창을 쓰고,
# runway(용량 추세)만 누적 신호라 가용 이력 전체를 쓴다.
WINDOW_DAYS = 14

# 유휴 진입선 — 활동 3축(CPU p95 · 네트워크 · 디스크 baseline IOPS). CPU 3% 는 Azure Advisor 저사용.
# 메모리 사용률·디스크 용량은 활동이 아니라 할당이라 제외 — baseline 메모리·큰 빈 디스크가 있어도 미사용일 수 있다.
IDLE_CPU_P95_PCT = 3
IDLE_NET_MBPS = 2
IDLE_DISK_IOPS = 5  # 로그·주기 flush 수준 — 사실상 정지
# 확실 유휴 — AWS Compute Optimizer idle 정의(거의 0). 상태가 아니라 종료 vs 통합 권고를 가르는 조치 강도.
IDLE_STRONG_PEAK_PCT = 1
IDLE_STRONG_NET_KBYTES_PER_S = 1

# 표시 서술("부하 변동 큼") 발화 하한 — peak 가 이 저부하선을 넘어야 burst 로 본다(저부하 지터 오탐 방지).
# 다운사이즈 판정과는 무관하다 — over 는 assess_cpu 의 사이징 목표가 낸다.
BURST_PEAK_FLOOR_CPU_PCT = 30
BURST_PEAK_FLOOR_MEM_PCT = 50

# 실행 큐/코어 포화선 (USE Method: vmstat "r" > CPU 수). load 대신 procs_running — load 는 D-state IO
# 블록이 섞여 오염되고(Gregg), procs_running 은 R-state 만 센다.
PROCS_RUNNING_PER_CORE_SATURATION = 1.0
# Windows Processor Queue Length/코어 (Microsoft "sustained > 2 per CPU"). Linux 1.0 과 값이 다른 건
# 모집단 차이다 — Windows 큐는 ready 대기만 세고 실행 중 스레드를 빼서 같은 포화 지점을 코어당 +1 낮게
# 센다. 두 임계를 한 값으로 맞추지 않는다.
CPU_RUN_QUEUE_PER_CORE_SATURATION = 2.0
# Windows 디스크당 Avg Disk Queue Length (Microsoft 병목 기준). 이미 디스크당 값이라 CPU 실행 큐처럼
# 코어로 나누지 않고 바로 비교한다.
DISK_QUEUE_PER_DISK_SATURATION = 2.0
# Windows Memory\Pages Input/sec p95 — 총 Pages/sec 과 달리 하드 폴트만 세어 mmap 파일 I/O 가 안 섞인다.
# Microsoft·업계 관례인 5(증설 권고)/20(체감 저하)/100(thrashing) 중 보수적으로 "체감 저하" 채택.
WIN_PAGES_INPUT_SATURATION = 20.0
# procs_blocked(D-state) p95 — 1 이면 적어도 한 프로세스가 상시 IO 블록(Gregg USE). 디스크 I/O 대기로
# 쌓인 CPU 로드를 가려내 root_cause 를 disk_io 로 귀속하는 인과 게이트.
PROCS_BLOCKED_DSTATE_SATURATION = 1.0


type Recommendation = Literal[
    "idle",
    "over_provisioned",
    "under_provisioned",
    "optimal",
    "insufficient_data",  # cpu_p95·mem_p95 둘 다 부재
]


@dataclass
class ResourceStats:
    """USE Method 통계 입력. None = 데이터 부재 — 그 축 평가만 skip 하고 분류는 막지 않는다."""

    cpu_p95_pct: float | None
    cpu_peak_pct: float | None
    cpu_cores: int | None
    mem_p95_pct: float | None
    disk_used_pct: float | None  # worst mount
    # kB/s(킬로바이트 — 킬로비트 아님). 유휴 판정용 — vNIC 은 link_speed 가 없어 이용률로는 못 쓴다.
    net_avg_kbytes_per_s: float | None
    os_family: str | None = None  # None(unknown)은 Linux 의미로 해석
    sample_sufficiency: float | None = None  # 측정 축(cpu/mem) 실측/기대 샘플 비율. None = 측정 축 부재
    disk_queue_p95: float | None = None  # agent 가 디스크별로 발행한 큐를 ingest 가 per-device max 로 축약한 값
    cpu_run_queue_p95: float | None = None  # Windows Processor Queue Length p95 (Linux procs_running 등가)
    mem_pages_input_rate_p95: float | None = None  # Windows Pages Input/sec p95 (Linux 하드폴트 등가)
    cpu_percore_p95_max: float | None = None  # 집계로는 낮게 보이는 단일스레드 병목 감지
    procs_blocked_p95: float | None = None  # D-state IO 블록 p95 — IO 발 CPU 로드 분리
    procs_running_p95: float | None = None  # R-state 실행 큐 p95 (IO 대기 미혼입)
    mem_swap_paging: bool = False  # 페이징 발생 — mem_saturated dual-gate 입력
    oom_occurred: bool = False  # 창 안 신규 OOM kill 발생
    mem_total_mb: int | None = None
    mem_near_peak_pct: float | None = None  # 버킷별 max 의 p95 — 비탄력 피크 사이징용(판정은 p95)
    disk_await_p95_ms: float | None = None
    disk_iops_baseline: float | None = None  # 유휴 판정용 활동량 — 포화 신호가 아니다
    # runway·목표 용량은 엔진이 마운트 이력 전체 span 의 2점 fill rate 로 산출한다 (get_report_aggregate).
    disk_capacity_runway_days: float | None = None  # 바이트 소진까지 남은 일수. 하락·수평이면 None(안 참)
    disk_inode_runway_days: float | None = None
    disk_inode_used_pct: float | None = None  # worst mount
    disk_capacity_target_gb: float | None = None  # 1년 수명 목표 총 용량
    net_retrans_pct: float | None = None
    net_drop_pct: float | None = None
    conntrack_ratio: float | None = None  # nf_conntrack count/max. 모듈 미로드면 None(미측정)
    history_hours: float | None = None
    cpu_burst_ratio: float | None = None  # p95/median
    util_trend_rising: bool | None = None  # 다운사이즈 정상성 게이트
    cpu_steal_p95_pct: float | None = None  # 높으면 이용률·포화 수치가 오염된다(충실도 편향)


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
    # dual-gate — 수집기 자신이 R-state 에 잡혀 저활동 시스템(특히 1코어)에서 실행 큐가 상시 1/core 를 넘는다.
    # util 이 측정됐는데 저활동이면 코어 부족이 아니다. util 미측정이면 모순 증거가 없어 실행 큐를 신뢰한다.
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
    """실시간 네트워크 혼잡 신호 — assess_network 와 같은 임계·저트래픽 게이트(스냅샷용)."""
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
    """표시 배지용 분류 — rollup_host 의 host_status 를 옮긴다(근본원인·권고와 항상 정합)."""
    return _HOST_STATUS_TO_REC[rollup_host(stats).host_status]


def host_status_to_recommendation(status: HostStatus) -> Recommendation:
    """host_status -> 표시 Recommendation (rollup 재계산 없이 변환)."""
    return _HOST_STATUS_TO_REC[status]


# 판정 helper(host_saturation_unmeasured)는 HostAssessment 정의 뒤에 둔다 — forward-ref 회피.
_SATURATION_KINDS: tuple[ResourceKind, ...] = ("cpu", "memory", "disk_io")


# 서버별 자원 적정성 표 기본 정렬 — 시급한 분류부터.
CLASSIFICATION_ORDER: dict[Recommendation, int] = {
    "under_provisioned": 0,
    "over_provisioned": 1,
    "idle": 2,
    "optimal": 3,
    "insufficient_data": 4,
}


# 상태(RECOMMENDATION_LABEL_KO)가 "무엇인가"면 이쪽은 "그래서 뭘 하나"다.
# per-host stats 가 없는 맥락(분포 막대·분류 요약)의 기본 문구 — 유휴 세분은 recommend_action.
RECOMMENDATION_ACTION_KO: dict[Recommendation, str] = {
    "under_provisioned": "증설 검토",
    "over_provisioned": "축소 검토",
    "idle": "종료·통합 검토",
    "optimal": "적정 — 유지",
    "insufficient_data": "표본 부족 — 관측 지속",
}


def is_idle_strong(stats: ResourceStats) -> bool:
    """확실 유휴 — AWS Compute Optimizer idle 정의(거의 0). 상태가 아니라 조치 강도."""
    return (
        stats.cpu_peak_pct is not None
        and stats.cpu_peak_pct <= IDLE_STRONG_PEAK_PCT
        and stats.net_avg_kbytes_per_s is not None
        and stats.net_avg_kbytes_per_s <= IDLE_STRONG_NET_KBYTES_PER_S
    )


def recommend_action(rec: Recommendation, stats: ResourceStats) -> str:
    """상태 -> per-host 조치 권고. under_provisioned 는 호출자가 under_prescription 으로 먼저 분기한다."""
    if rec == "idle":
        return "즉시 종료 검토" if is_idle_strong(stats) else "통합·재배치 검토"
    return RECOMMENDATION_ACTION_KO.get(rec, "")


CPU_UNDER_PCT = 70  # 큐잉 무릎(Kleinrock) + AWS Compute Optimizer Balanced(<70%, P95)
CPU_SIZING_TARGET_PCT = 70  # AWS Balanced — 증설·다운사이즈 공통 목표(비대칭 없음)
CPU_SAT_HEADROOM = 0.7  # 증설 목표 — 실행큐/코어를 포화선의 0.7배 아래로
CPU_PERCORE_HOLD_PCT = 85  # 어느 코어든 p95 가 이 이상이면 다운사이즈 보류(단일스레드 병목 보호)
CPU_STEAL_BIAS_PCT = 5  # steal p95 가 이 이상이면 하이퍼바이저 경합 — 이용률·포화가 오염(충실도 편향)
MEM_UNDER_PCT = 90  # Azure Advisor(CPU·메모리 >= SKU 90% 시 resize)
MEM_SIZING_TARGET_PCT = 80  # near-peak 를 여기에 착지(20% headroom)
DISK_RUNWAY_DAYS = 30  # 소진 전 스토리지 추가 권고 — 프로비저닝 lead time
DISK_TARGET_RUNWAY_DAYS = 365  # 확장 목표 수명 — 현재 성장률로 1년 버티는 총 용량
# 성장률 외삽을 신뢰할 최소 관측 span — 사이징 창만큼은 봐야 1년으로 외삽한다(짧으면 spike 과외삽).
DISK_TREND_MIN_SPAN_DAYS = WINDOW_DAYS
# 근시 지평 — 짧은 span 에도 신뢰 가능한 외삽 기간. 30일 예상 표시와 짧은 span 확장 목표가 공유.
DISK_NEAR_HORIZON_DAYS = 30
# 자료 부족 + 임계 초과 시 확장 후 used 착지값 — CPU·메모리 사이징 착지(70%)와 정합.
DISK_HEADROOM_TARGET_PCT = 70
DISK_STATIC_GUARD_PCT = 85  # monitoring 표준(major) — 추세 신뢰도 낮을 때 fallback
DISKIO_AWAIT_MS = 20  # VMware(read >20ms critical) / SQL Server(~10-15ms)
# device 사용률(io_time 벽시계 busy 비율) 하한 — 이 미만 버킷의 await 는 병목 신호에서 제외. tick 기반 결합
# await 는 writeback 큐 잔류로 유휴 device 에서도 1000ms+ 로 튀지만 90% 유휴면 병목일 수 없다.
DISKIO_UTIL_MIN = 0.5
NET_RETRANS_PCT = 1.0  # monitoring 관례(재전송 >1% 성능 영향)
NET_DROP_PCT = 0.5  # monitoring 관례(드롭 <0.5% 비즈니스 앱)
# 품질 판정 최소 트래픽 — 저트래픽 호스트에선 부팅기 소수 이벤트가 비율을 지배한다(분모 붕괴). 사이징이 아닌
# 운영 경보라 오탐 비용이 커서 미만이면 판정을 보류한다. retrans 는 host-global TCP 재전송을 물리 iface
# tx_packets 로 나눈 스코프 근사(agent OutSegs 미발행)라 저트래픽에서 특히 못 믿는다.
NET_MIN_TRAFFIC_KBPS = 10.0
# monitoring 관례 — nf_conntrack count/max 80% = 연결테이블 고갈 임박(신규 연결 드롭 위험).
CONNTRACK_SATURATION_RATIO = 0.8
CONFIDENCE_MIN_HOURS = 30  # AWS insufficient-data(14일 창 누적 30h) — 통계 정밀도 절대 바닥
# fill-rate 외삽 절대 하한 — 프로비저닝 초기 1회성 채움을 성장 추세로 오외삽하는 것을 막는다.
# 미만이면 runway 를 안 내고 정적 가드(used%)만 남는다.
DISK_RATE_MIN_SPAN_DAYS = CONFIDENCE_MIN_HOURS / 24
# 다운사이즈는 위험 방향이라 창이 충분히 관측됐을 때만 처방한다. 절대 시간이 아닌 창 대비 관측 비율이라
# WINDOW_DAYS 가 바뀌어도 문턱이 불변이고 미세 갭을 흡수한다.
DOWNSIZE_MIN_SUFFICIENCY = 0.7
BURST_RATIO_MAX = 2.0  # p95/median 이 이 이상이면 버스티(통계 정밀도 하향)
# 이용률 최소제곱 기울기 %/day 가 이 이상이면 유의한 상승. Theil-Sen(강건) 대신 regr_slope 산출을 이진화한
# 값이라, 다운사이즈 억제 방향인 만큼 보수적으로 작게 잡는다.
UTIL_TREND_RISING_PCT_PER_DAY = 0.2

# 포화 판정 상수를 그대로 재사용한다 — 사이징 목표 코어 산출이 임계를 따로 두면 드리프트가 난다.
_CPU_SAT_LINE = {"windows": CPU_RUN_QUEUE_PER_CORE_SATURATION}
_CPU_SAT_LINE_DEFAULT = PROCS_RUNNING_PER_CORE_SATURATION  # Linux/unknown


def util_trend_rising_from_slopes(cpu_slope: float | None, mem_slope: float | None) -> bool | None:
    """cpu·mem 이용률 기울기(%/day)로 상승 추세 판정 — 다운사이즈 정상성 게이트.

    하나라도 임계 이상이면 상승으로 본다(보수적). 둘 다 None(추세 산출 불가)이면 None 을 돌려
    nonstationary 를 세우지 않는다 — 짧은 이력은 다른 신뢰도 축이 막는다.
    """
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
    "insufficient",  # cpu·memory
    "filling",
    "capacity_ok",  # disk_capacity
    "io_bound",
    "io_ok",  # disk_io
    "congested",
    "quality_ok",  # network
    "unmeasured",
]


@dataclass
class ConfidenceNote:
    """신뢰도 4종 불확실성 — 종류가 다르면 대응도 다르다."""

    low_precision: bool = False  # 통계 — 표본 부족·버스티
    coverage_gap: bool = False  # 커버리지 — 필요 축 미측정
    biased: bool = False  # 충실도 — 계통 편향(표본을 늘려도 안 줄어듦)
    nonstationary: bool = False  # 정상성 — 상승 추세(forward-looking 결정에만)

    @property
    def high(self) -> bool:
        """다운사이즈 처방 가능 수준 — 정밀·커버리지·충실도 온전(정상성은 별도 게이트)."""
        return not (self.low_precision or self.coverage_gap or self.biased)


@dataclass
class ResourceAssessment:
    """자원 하나의 USE 판정 결과."""

    kind: ResourceKind
    status: ResourceStatus
    triggers: list[TriggerKind] = field(default_factory=list[TriggerKind])
    sizing_target: int | None = None  # cpu=코어, memory=MB. None=사이징 불가/불요
    sizing_floor: int | None = None  # 정확 목표 불가(포화 주도) 시 안전 상향 하한 — API recommended never-null
    confidence: ConfidenceNote = field(default_factory=ConfidenceNote)
    detail: str = ""


type HostStatus = Literal["under", "idle", "over", "optimal", "insufficient"]


@dataclass
class HostAssessment:
    """호스트 종합 — 자원별 판정 + 근본원인 + 호스트 요약 상태."""

    resources: dict[ResourceKind, ResourceAssessment]
    root_cause: ResourceKind | None = None
    # root 의 증상으로 추정되는 kind — 표시 라벨용일 뿐 처방 억제에는 쓰지 않는다.
    symptom_of_root: list[ResourceKind] = field(default_factory=list[ResourceKind])
    host_status: HostStatus = "optimal"  # 정렬·배지용 요약 (조치는 root_cause·자원별에서)
    network_congested: bool = False  # 사이징 축이 아닌 품질 경고라 별도 플래그
    sample_sufficiency: float | None = None  # 창 대비 관측 비율 (30h 절대 바닥과 별개 축)


def _low_precision(stats: ResourceStats) -> bool:
    if stats.history_hours is not None and stats.history_hours < CONFIDENCE_MIN_HOURS:
        return True
    return bool(stats.cpu_burst_ratio is not None and stats.cpu_burst_ratio > BURST_RATIO_MAX)


def _base_confidence(stats: ResourceStats, *, biased: bool = False, util_bearing: bool = False) -> ConfidenceNote:
    """자원 공통 신뢰도 뼈대 — 커버리지는 자원별로 채운다.

    util_bearing 은 cpu·memory 만 True — 이용률 개념이 없는 disk·network 에 이용률 추세를 오귀속하지 않는다.
    """
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
    """AWS Balanced 사이징 — 이용률 목표와 포화 headroom 목표 중 큰 쪽. 증설·다운사이즈 공통.

    포화 headroom 은 실제 포화(dual-gate 통과)일 때만 반영한다 — 저활동 노이즈성 실행 큐로 코어를
    부풀리지 않게.
    """
    util_cores = math.ceil(util_pct * cores / CPU_SIZING_TARGET_PCT) if util_pct > 0 else 1
    sat_cores = 0
    if saturated and run_queue and run_queue > 0:
        sat_line = _CPU_SAT_LINE.get(os_family or "", _CPU_SAT_LINE_DEFAULT)
        sat_cores = math.ceil(run_queue / (sat_line * CPU_SAT_HEADROOM))
    return max(1, util_cores, sat_cores)


def assess_cpu(stats: ResourceStats) -> ResourceAssessment:
    """CPU 판정 — 이용률·실행 큐 포화로 under, AWS Balanced 사이징 목표로 over/optimal 을 가른다."""
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
        # 포화 주도라 이용률 목표가 현재 코어 이하로 나오면 정확 수치를 못 낸다 — API recommended 가 null 이
        # 되지 않게 안전 하한(현재+1코어)으로 채운다.
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
    """메모리 사이징 — 비탄력(초과=OOM)이라 판정용 p95 가 아니라 near-peak 를 목표%에 착지시킨다."""
    if total_mb is None or near_peak_pct <= 0:
        return None
    return math.ceil(total_mb * near_peak_pct / MEM_SIZING_TARGET_PCT)


# 포화 주도 under 증설 headroom — 이용률이 낮아 util 사이징이 현재 이하일 때 현재 총량에 이 비율을 얹는다.
# 30% = AWS/GCP advisor headroom prior.
MEM_SATURATION_HEADROOM_PCT = 30


def _mem_paging_active(stats: ResourceStats) -> bool:
    """페이징 압박 발생 여부 — mem_saturated(os-aware)를 bool 로 축약(미측정 None -> False)."""
    return bool(mem_saturated(stats))


def _mem_under_target(util_target: int | None, stats: ResourceStats) -> int | None:
    """메모리 under 증설 목표 — 현재 총량을 넘는 목표가 안 나오면 None.

    이용률이 낮아도 swap·OOM 이면 현재 사양이 부족하다는 증거라 '현재+headroom' 을 후보에 덧댄다.
    """
    total = stats.mem_total_mb
    candidates = [t for t in (util_target,) if t is not None]
    if total is not None and (_mem_paging_active(stats) or stats.oom_occurred):
        candidates.append(math.ceil(total * (1 + MEM_SATURATION_HEADROOM_PCT / 100)))
    if not candidates:
        return None
    up = max(candidates)
    return up if (total is None or up > total) else None


def assess_memory(stats: ResourceStats) -> ResourceAssessment:
    """메모리 판정 — 이용률 임계 초과 OR OOM 이면 under, 사이징은 near-peak 착지.

    물리적 무릎이 없는 자원이라 임계는 advisor prior 다. swapless 호스트가 다수라 이용률이 주신호다.
    """
    util = stats.mem_p95_pct
    conf = _base_confidence(stats, util_bearing=True)
    if util is None:
        # 이용률 미측정(구커널 MemAvailable 부재). OOM 은 강한 사후 증거라 그대로 under 로 두지만, 페이징은
        # 이용률 확증 없이는 mmap·프로세스 시작의 정상 하드폴트와 구분이 안 돼 under 신호로 안 쓴다(과대 증설 방지).
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
        floor = None  # 정확 목표 불가 시 안전 하한(현재+headroom)
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
    """가장 빨리 차는 축 — None(안 참) 제외 최소. 전부 None 이면 None."""
    vals = [r for r in runways if r is not None]
    return min(vals) if vals else None


def assess_disk_capacity(stats: ResourceStats) -> ResourceAssessment:
    """디스크 용량 판정 — 소진 runway 임박이면 filling, 추세를 못 내면 정적 가드 used% 로 본다.

    확장 목표(sizing_target, GB)는 상류 집계(get_report_aggregate)가 산출한 값을 싣기만 한다 — 짧은 span 을
    1년으로 과외삽하지 않도록 경로가 거기서 갈린다.
    """
    conf = _base_confidence(stats)
    runway = _min_runway(stats.disk_capacity_runway_days, stats.disk_inode_runway_days)
    used = stats.disk_used_pct
    inode_used = stats.disk_inode_used_pct
    # 상류가 CEIL 로 내지만 0·음수·float 를 방어한다 (sizing_target 은 양의 정수 계약).
    tgt = stats.disk_capacity_target_gb
    tgt = math.ceil(tgt) if tgt is not None and tgt >= 1 else None
    if runway is not None and runway < DISK_RUNWAY_DAYS:
        # inode 소진이 먼저면 용량 확장으로 안 풀려 목표를 싣지 않는다.
        rtgt = tgt if runway == stats.disk_capacity_runway_days else None
        detail = f"{runway:.0f}일 후 소진" + (f", 목표 {rtgt:.0f}GB" if rtgt else "")
        return ResourceAssessment(
            "disk_capacity", "filling", triggers=["disk_capacity"], sizing_target=rtgt, confidence=conf, detail=detail
        )
    # 정적 가드 — 축별로 runway 가 None(수평·하락·데이터 부족)일 때만. 수평 추세 + inode 95% 처럼 추세로는
    # 안 잡히는 임박을 바이트·inode 대칭으로 포착한다.
    _guard = DISK_STATIC_GUARD_PCT
    byte_static = stats.disk_capacity_runway_days is None and used is not None and used >= _guard
    inode_static = stats.disk_inode_runway_days is None and inode_used is not None and inode_used >= _guard
    if byte_static:
        detail = f"used {used:.0f}% (정적 가드)" + (f", 목표 {tgt:.0f}GB" if tgt else "")
        return ResourceAssessment(
            "disk_capacity", "filling", triggers=["disk_capacity"], sizing_target=tgt, confidence=conf, detail=detail
        )
    if inode_static:
        # inode 소진은 용량 확장(GB)으로 안 풀린다(mkfs 고정) — 목표 없이 파일 정리·재포맷 처방 층으로.
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
    action: str  # "increase" | "keep" (디스크는 축소 없음)
    estimate_quality: str  # "exact" | "floor"
    note: str = ""  # 크기로 안 풀리는 신호(inode 소진 등) advisory


def assess_mount_capacity(
    total_bytes: int | None,
    target_bytes: float | None,
    byte_runway_days: float | None,
    used_pct: float | None,
    inode_runway_days: float | None,
    inode_used_pct: float | None,
) -> MountSizing | None:
    """마운트 용량 사이징 — assess_disk_capacity(호스트 worst-mount)의 per-mount 판(같은 임계·산식).

    total_bytes 미상이면 None — 사이징 불가라 축을 생략한다.
    """
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
        # 소진 임박인데 목표 산출 불가 — 현재를 headroom 목표%로 올린 안전 하한.
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
    """디스크 I/O 판정 — await 임계 초과면 io_bound(표시만, 사이징 불가). virtio 간섭이라 충실도 편향."""
    conf = _base_confidence(stats, biased=True)
    sat = disk_io_saturated(stats)
    if sat is None:
        # await 는 device 가 바쁜 버킷에서만 산출된다 — I/O 활동이 관측된 저활동 device 는 미산출이어도
        # 병목이 아니라 io_ok 다.
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
    """네트워크 판정 — 사이징 축이 아닌 품질 신호. errors 는 virtio 에서 항상 0 이라 쓰지 않는다."""
    conf = _base_confidence(stats)
    retrans = stats.net_retrans_pct
    drop = stats.net_drop_pct
    conntrack = stats.conntrack_ratio
    # conntrack 은 트래픽량과 무관한 절대 신호라 저트래픽 게이트를 받지 않는다.
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


# 호스트 under/root_cause 를 결정하는 자원 상태 — 사이징 가능 축만. io_bound·congested 는 크기로 안 풀리는
# 직교 신호라 뺀다(처방이 0 인 축이 top-line 분류를 뒤집지 않게).
_ROOTABLE_UNDER: tuple[ResourceStatus, ...] = ("under", "filling")


def _host_status(
    stats: ResourceStats,
    res: dict[ResourceKind, ResourceAssessment],
    under_kinds: set[ResourceKind],
) -> HostStatus:
    """호스트 요약 상태 — under > insufficient > idle > over > optimal 중 먼저 맞는 것으로 확정."""
    if under_kinds:
        return "under"
    # 사이징 2축이 둘 다 미측정이면 disk·network 부분 데이터가 있어도 optimal 로 위장하지 않는다.
    # idle/over 검사보다 앞이다 — cpu·mem 없이는 둘 다 판정 불가.
    if res["cpu"].status in ("unmeasured", "insufficient") and res["memory"].status in ("unmeasured", "insufficient"):
        return "insufficient"
    cpu = stats.cpu_p95_pct
    net = stats.net_avg_kbytes_per_s
    net_mbps = net * 8 / 1000 if net is not None else None  # kB/s -> Mbit/s
    # 측정된 활동만 유휴를 배제한다 — 미측정(None)은 배제하지 않는다.
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
        # 메모리발 — 동반 디스크 I/O·CPU 는 swap 트래픽·대기의 증상이다.
        host.root_cause = "memory"
        symptoms: tuple[ResourceKind, ...] = ("disk_io", "cpu")
        host.symptom_of_root = [k for k in symptoms if k in under_kinds or (k == "disk_io" and disk_io_pressure)]
    elif disk_io_pressure and cpu_pressure and procs_blocked_high:
        # 디스크발 — CPU 로드는 D-state 블록의 증상이다.
        host.root_cause = "disk_io"
        host.symptom_of_root = ["cpu"] if "cpu" in under_kinds else []
    elif under_kinds:
        # 결합 신호가 없으면 각자 원인 — root 는 최상류 부족 자원이고 나열 순서만 정한다.
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


# 자원 부족 처방 기본 문구 — 사이징 축은 목표가 있으면 resource_prescription 이 총량 목표로 대체한다.
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
)  # 나열 순(인과 상류 우선)


def _format_sizing_target(kind: ResourceKind, target: int) -> str:
    if kind == "cpu":
        return f"{target}코어"
    if target >= 1024:
        return f"{target / 1024:.1f}GB".replace(".0GB", "GB")
    return f"{target}MB"


def prescribed_under_kinds(host: HostAssessment) -> list[ResourceKind]:
    """처방 대상 under 자원 — 관측된 under 전부, 인과에 의한 억제 없음.

    근본원인은 진단 근거일 뿐 처방 필터가 아니다 — 원인 자원만 고쳐도 하류가 해소된다는 보장이 없어
    관측된 부족을 누락하지 않는 쪽이 안전하다(assessment API sizing.axes 와 동일 정책).
    """
    return [k for k in _UNDER_ORDER if k in host.resources and host.resources[k].status in _ROOTABLE_UNDER]


def resource_prescription(kind: ResourceKind, ra: ResourceAssessment) -> str:
    """자원 1개 처방 문구 — 사이징 목표가 있으면 총량 목표로, 없으면 기본 문구로."""
    if kind == "disk_capacity" and ra.sizing_target is not None:
        return f"스토리지: {ra.sizing_target:.0f}GB"  # 1년 수명 목표 총 용량
    if kind in _SIZEABLE_LABEL and ra.sizing_target is not None:
        return f"{_SIZEABLE_LABEL[kind]}: {_format_sizing_target(kind, ra.sizing_target)}"
    return _UNDER_ACTION_BASE[kind]


def under_prescription(host: HostAssessment) -> str:
    """자원 부족 처방 — "무엇을" 만 나열한다. "왜" 는 root_cause_display 가 따로 전달."""
    return " | ".join(resource_prescription(k, host.resources[k]) for k in prescribed_under_kinds(host))


def root_cause_display(host: HostAssessment) -> str:
    """근본원인 칼럼 — under_prescription 의 "왜" 를 전달하는 진단 근거(처방 자체엔 무영향).

    인과 결합이면 "메모리 (CPU 유발)", 복수 독립이면 나열, 부족이 없으면 빈 문자열.
    """
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
    """다운사이즈 처방 게이트 — over 분류는 늘 뜨나 구체 처방은 조건 충족 시만.

    잘못된 다운사이즈가 최악이라 신뢰도·정상성·관측 충분성을 모두 요구한다. 미충족이면 분류는 over 로
    두고 권고만 "관찰만" 으로 낮춘다.
    """
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
