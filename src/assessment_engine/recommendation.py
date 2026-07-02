"""Right-sizing 분류 — USE Method (Brendan Gregg) + 공식 cloud advisor 임계값.

명세·근거 단일 진실: docs/architecture/right-sizing.md (분류 정의·임계 출처·OS 분기·한계).

evidence 기반 분류: 자원(CPU/Mem/Disk)별로 "가진 축"을 평가해 신호(trigger)를 모으고,
under(위험) 우선 우선순위로 단일 분류 하나 + 근거(triggers) + 미관측 축(unmeasured)을 산출한다.
가진 데이터로 항상 결론을 내며("C 로 판단" 설명 가능), OS 비대칭(Windows 의 saturation 축 부재)은
분류를 막지 않고 confidence 단서(unmeasured)로만 노출한다.

분류 enum: idle / shutdown / over_provisioned / under_provisioned / optimal / insufficient_data.

UI badge 임계값(`mappers._USAGE_DANGER_PCT`/`_USAGE_WARN_PCT`)과는 별 도메인:
- mapper 90/75 = 시점 사용량 시각 신호 (위험·주의·정상)
- 본 모듈 = WINDOW_DAYS(7일) 통계 기반 right-sizing 결정 (idle/over/under 등)

합성 규칙 (단일 진실):
- under = 위험 신호 OR (어떤 자원이든 고이용·포화·용량초과 하나라도 -> 누락 0)
- over  = 가용 이용률 AND (cpu·mem p95 가 둘 다 있고 둘 다 낮을 때만 -> 보수적)
- insufficient_data = cpu_p95·mem_p95 가 둘 다 None (진짜 평가 불가 = 신규/표본 부재)
"""

from dataclasses import dataclass, field
from typing import Literal

# ─── 임계값 ─────────────

# 관찰 윈도우 — 평가·차트·보고서 공통 표준 기간 (F10 단일 진실)
WINDOW_DAYS = 7

# Idle 판정 — AWS Compute Optimizer
IDLE_CPU_PEAK_PCT = 1
IDLE_NET_KBPS = 1

# Shutdown 권장 — Azure Advisor
SHUTDOWN_CPU_P95_PCT = 3
SHUTDOWN_NET_MBPS = 2

# Over-provisioned (다운사이즈) — AWS Compute Optimizer + GCP Recommender (headroom 30%)
CPU_DOWNSIZE_P95_PCT = 30
MEM_DOWNSIZE_P95_PCT = 50
HEADROOM_PCT = 30

# Under-provisioned (업사이즈) — USE Method utilization 임계
CPU_UPSIZE_P95_PCT = 70  # Kleinrock — Queueing Systems (1975), Google SRE Book
MEM_UPSIZE_P95_PCT = 80  # Linux page cache 압박 시작점

# USE Method Saturation 임계 — utilization 외 saturation 축 평가 (Brendan Gregg 정석).
CPU_SATURATION_LOAD_RATIO = 1.0  # load_15m / cpu_cores >= 1.0 — run queue saturation
IOWAIT_UPSIZE_PCT = 20  # iowait_p95 >= 20% — disk IO saturation (Linux)
# Windows disk IO saturation — 디스크당 Avg Disk Queue Length >= 2 (Microsoft 정석 병목 기준).
# agent 가 디스크별 큐를 발행 -> ingest 에서 per-device max 축약 -> 이 임계로 바로 비교 (정규화 불요).
DISK_QUEUE_PER_DISK_SATURATION = 2.0
DISK_CAPACITY_UPSIZE_PCT = 85  # worst mount used_pct >= 85% — storage capacity utilization

# 표본 충분성 — 측정 축(cpu/mem) 실측 5분 버킷 / 윈도우 기대 버킷(period_days*288, cagg 5분) 비율이 이 미만이면
# 표본 부족(low_sample). 분류를 막지 않고(원칙1) confidence 단서로만 노출(원칙2). 0.5 = 실질 관측 절반 미만.
SAMPLE_SUFFICIENCY_RATIO = 0.5


Recommendation = Literal[
    "idle",
    "shutdown",
    "over_provisioned",
    "under_provisioned",
    "optimal",
    "insufficient_data",  # cpu_p95·mem_p95 둘 다 부재 (신규/표본 부재)
]

# under_provisioned 유발 신호 키 — 도메인 식별자(머신용). mapper 가 한국어 권고로 변환(P2).
# attention 5종 trigger + iowait(disk_io) 정합. 표시 순서·라벨은 mapper 단일 진실.
Trigger = Literal["cpu_util", "cpu_saturation", "mem_util", "mem_saturation", "disk_capacity", "disk_io"]


@dataclass
class ResourceStats:
    """USE Method 통계 입력 — Utilization·Saturation 정석 6 자원축.

    None 은 데이터 부재 (해당 축 평가 skip — 분류를 막지 않고 unmeasured 로 기록).
    """

    # CPU
    cpu_p95_pct: float | None  # utilization
    cpu_peak_pct: float | None
    cpu_load_15m_max: float | None  # saturation 원자료 (saturation_ratio = load / cores)
    cpu_cores: int | None
    # Memory
    mem_p95_pct: float | None  # utilization
    swap_used: bool  # saturation (page-out 발생)
    # Disk
    disk_used_pct: float | None  # storage capacity utilization (worst mount)
    iowait_p95_pct: float | None  # disk IO saturation (cpu wait on IO)
    # Network
    net_avg_kbps: float | None  # idle/shutdown 판정용 (saturation metric 미수집)
    # OS family — 신호 의미 분기 (원칙 P2). default None = unknown -> Linux 의미(엔진 fallback 정합).
    os_family: str | None = None
    # 표본 충분성 — 측정 축(cpu/mem) 실측/기대 샘플 비율. None = 측정 축 부재(판정 불가, low_sample 무관).
    sample_sufficiency: float | None = None
    # Windows 디스크 I/O saturation (Linux iowait 등가 축) — disk_io_saturated 가 os-aware 로 소비.
    # disk_queue_p95 = 가장 바쁜 디스크의 큐 깊이 p95 (agent 가 디스크별 발행 -> ingest 에서 per-device max 축약).
    disk_queue_p95: float | None = None


@dataclass
class Assessment:
    """evidence 기반 분류 결과 — 분류 1개 + 근거(triggers) + 미관측 축(unmeasured).

    - triggers: under_provisioned 를 유발한 hit 신호 키 목록 (그 외 분류는 빈 목록).
                "어떤 데이터로 under 판정인가"의 근거. mapper 가 한국어 권고로 변환.
    - unmeasured: 평가하지 못한 saturation 축 키 목록 (값이 None 이라 skip 된 축).
                  Windows 는 load 부재 -> ["cpu_saturation"]. confidence 단서(분류는 완결).
    """

    recommendation: Recommendation
    triggers: list[str] = field(default_factory=list)
    unmeasured: list[str] = field(default_factory=list)
    # 표본 부족 — 측정 축 sufficiency < SAMPLE_SUFFICIENCY_RATIO. 분류는 완결, 신뢰도 단서로만 (원칙2).
    # is_partial(축 미관측)과 별개 confidence 축 — 둘 다 confidence_notes 로 표시 계층에 통합 노출.
    low_sample: bool = False

    @property
    def is_partial(self) -> bool:
        """saturation 축 일부를 못 본 "부분 평가" 인지 — confidence 단서 (원칙 P4).

        분류 자체는 utilization 으로 완결됐고, 못 본 축이 있다는 사실만 표시 계층에 노출.
        """
        return bool(self.unmeasured)


def swap_saturation(os_family: str | None, swap_used: bool) -> bool:
    """swap/pagefile 사용이 메모리 saturation 신호인지 — Linux 한정 (원칙 P2).

    Windows pagefile 은 여유 RAM 에도 상시 사용되는 baseline 이라 saturation 신호 아님.
    Linux swap 사용은 page-out = 메모리 압박 신호. os_family None(unknown)은 Linux 로 취급.
    assess·report mapper 의 swap 해석 단일 진실 — 본 helper 경유.
    """
    return swap_used and os_family != "windows"


def disk_io_saturated(stats: ResourceStats) -> bool | None:
    """디스크 I/O 포화 여부 — OS별 raw 신호를 통일 축으로 정규화 (원칙 P2, os-aware).

    Linux: iowait_p95 >= IOWAIT_UPSIZE_PCT (cpu 의 IO 대기 비율).
    Windows: 가장 바쁜 디스크의 큐 깊이(disk_queue_p95) >= DISK_QUEUE_PER_DISK_SATURATION.
             agent 가 디스크별 큐를 발행 -> ingest 에서 per-device max 축약(정규화 불요).
             Windows cpu_iowait 는 더미 0이라 신뢰 금지 — disk_queue 를 신호로 사용.
    측정 불가(값 None)면 None -> assess 가 unmeasured("disk_io")로 표시.
    assess·report·attention 이 본 helper 단일 진실 경유 (임계 재계산 금지).
    """
    if stats.os_family == "windows":
        if stats.disk_queue_p95 is None:
            return None
        return stats.disk_queue_p95 >= DISK_QUEUE_PER_DISK_SATURATION
    if stats.iowait_p95_pct is None:
        return None
    return stats.iowait_p95_pct >= IOWAIT_UPSIZE_PCT


def assess(stats: ResourceStats) -> Assessment:
    """USE Method evidence 분류 — 자원별 가용 축을 신호로 모아 단일 분류 + 근거 산출.

    판정 순서: under(위험 신호 OR) → idle → shutdown → insufficient(데이터 없음) → over(이용률 AND) → optimal.
    under 가 idle/shutdown 보다 우선 — 어떤 위험 신호든 하나면 발화(누락 0). CPU 가 낮아도 스왑·iowait·load·
    mem·disk 압박이 있으면 "미사용(idle/shutdown)"이 아니라 자원 부족이다. over 는 cpu·mem 둘 다 낮을 때만(보수적).
    insufficient_data 는 utilization 도 없고 under 신호도 없을 때만 — swap·iowait 등 saturation 신호가
    있으면 util 부재여도 under 로 결론낸다(데이터로 반드시 판단). 못 본 saturation 축(예: Windows load)은
    unmeasured 에 기록 — 분류를 막지 않고 confidence 로만 노출.
    """
    cpu = stats.cpu_p95_pct
    mem = stats.mem_p95_pct

    # 못 본 saturation 축 기록 (confidence 단서) — 값이 None 인 축만. swap 은 Windows 의도 제외라
    # "미관측"이 아니므로 제외하지 않는다(제외 != 미관측).
    unmeasured: list[str] = []
    if stats.cpu_load_15m_max is None or stats.cpu_cores is None:
        unmeasured.append("cpu_saturation")
    # disk_io 는 OS별 신호 정규화(Linux iowait / Windows disk_queue) — helper 단일 진실.
    disk_sat = disk_io_saturated(stats)
    if disk_sat is None:
        unmeasured.append("disk_io")

    # 표본 부족 — 측정 축 sufficiency < 임계. 분류를 막지 않고 confidence 단서로만 동반 (원칙2).
    low_sample = stats.sample_sufficiency is not None and stats.sample_sufficiency < SAMPLE_SUFFICIENCY_RATIO

    # under_provisioned — 위험 신호 수집(OR). idle/shutdown 보다 먼저 — 어떤 위험 신호든 하나면 발화(누락 0).
    # CPU 가 낮아도 스왑·iowait·load·mem·disk 압박이 있으면 "미사용"이 아니라 자원 부족이다
    # (예: CPU idle 인데 page-out = 메모리 부족). 가진 축만 평가해 hit 된 신호를 근거로 모은다.
    triggers: list[str] = []
    if cpu is not None and cpu >= CPU_UPSIZE_P95_PCT:
        triggers.append("cpu_util")
    if mem is not None and mem >= MEM_UPSIZE_P95_PCT:
        triggers.append("mem_util")
    if stats.disk_used_pct is not None and stats.disk_used_pct >= DISK_CAPACITY_UPSIZE_PCT:
        triggers.append("disk_capacity")
    if (
        stats.cpu_load_15m_max is not None
        and stats.cpu_cores is not None
        and stats.cpu_cores > 0
        and (stats.cpu_load_15m_max / stats.cpu_cores) >= CPU_SATURATION_LOAD_RATIO
    ):
        triggers.append("cpu_saturation")
    if disk_sat:
        triggers.append("disk_io")
    if swap_saturation(stats.os_family, stats.swap_used):
        triggers.append("mem_saturation")

    if triggers:
        return Assessment("under_provisioned", triggers=triggers, unmeasured=unmeasured, low_sample=low_sample)

    # Idle / Shutdown — 위험 신호 0 일 때만 (진짜 미사용). net + cpu 의존, 없으면 fall-through.
    if stats.net_avg_kbps is not None:
        if (
            stats.cpu_peak_pct is not None
            and stats.cpu_peak_pct <= IDLE_CPU_PEAK_PCT
            and stats.net_avg_kbps <= IDLE_NET_KBPS
        ):
            return Assessment("idle", unmeasured=unmeasured, low_sample=low_sample)
        # net_avg_kbps(KB/s) → Mbps: x 8 / 1000
        if cpu is not None and cpu <= SHUTDOWN_CPU_P95_PCT and (stats.net_avg_kbps * 8 / 1000) <= SHUTDOWN_NET_MBPS:
            return Assessment("shutdown", unmeasured=unmeasured, low_sample=low_sample)

    # 평가 불가 — utilization(cpu·mem) 둘 다 부재 + 위 under 위험 신호도 없음 (신규/표본 부재).
    # sufficiency 도 None(측정 축 부재)이라 low_sample 무관 — insufficient_data 가 "관측 자체 부재"를 이미 표현.
    if cpu is None and mem is None:
        return Assessment("insufficient_data")

    # over_provisioned — 보수적: cpu·mem p95 가 둘 다 있고 둘 다 다운사이즈 임계 이하일 때만.
    # 한쪽이라도 None 이거나 높으면 over 로 단정하지 않는다(saturation 못 본 Windows 오판 회피).
    if cpu is not None and mem is not None and cpu <= CPU_DOWNSIZE_P95_PCT and mem <= MEM_DOWNSIZE_P95_PCT:
        return Assessment("over_provisioned", unmeasured=unmeasured, low_sample=low_sample)

    return Assessment("optimal", unmeasured=unmeasured, low_sample=low_sample)


def classify(stats: ResourceStats) -> Recommendation:
    """분류 enum 만 — 기존 호출처 호환 wrapper (근거·confidence 필요 시 assess 사용)."""
    return assess(stats).recommendation


def is_partial_evaluation(stats: ResourceStats) -> bool:
    """saturation 축 일부 미관측 여부 — 호환 wrapper (assess().is_partial)."""
    return assess(stats).is_partial


# ─── UI 라벨 (한국어, 양식 A 사용자 친화 표시용) ──────────────────────────

LABEL_KO: dict[str, str] = {
    "idle": "유휴",
    "shutdown": "종료 권장",
    "over_provisioned": "과다 할당",
    "under_provisioned": "자원 부족",
    "optimal": "정상",
    "insufficient_data": "표본 부족",
}

# 양식 A의 RISK 색상 매핑 — report.html `.rec-{recommendation}` CSS와 짝.
# 서로 다른 분류에 다른 클래스 — over=노랑(비용), under=빨강(위험), optimal=녹색.
BADGE_CLASS: dict[str, str] = {
    "idle": "rec-idle",
    "shutdown": "rec-shutdown",
    "over_provisioned": "rec-over_provisioned",
    "under_provisioned": "rec-under_provisioned",
    "optimal": "rec-optimal",
    "insufficient_data": "rec-insufficient_data",
}

# under_provisioned 신호 키 -> 한국어 권고 구문 (mapper._build_under_provisioned_reason 단일 진실 보조).
# triggers 를 사람용 근거로 변환할 때 참조. 표시 순서는 mapper 가 결정.
TRIGGER_LABEL_KO: dict[str, str] = {
    "cpu_util": "CPU 이용률 초과",
    "cpu_saturation": "CPU run queue 포화",
    "mem_util": "메모리 이용률 초과",
    "mem_saturation": "스왑 page-out(메모리 압박)",
    "disk_capacity": "디스크 용량 임박",
    "disk_io": "디스크 I/O 포화",
}
