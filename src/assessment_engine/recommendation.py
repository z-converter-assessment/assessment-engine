"""Right-sizing 분류 — USE Method (Brendan Gregg) + 공식 cloud advisor 임계값.

명세·근거 단일 진실: docs/architecture/right-sizing.md (분류 정의·임계 출처·OS 분기·한계).

evidence 기반 분류: 자원(CPU/Mem/Disk)별로 "가진 축"을 평가해 신호(trigger)를 모으고,
under(위험) 우선 우선순위로 단일 분류 하나 + 근거(triggers) + 미관측 축(unmeasured)을 산출한다.
가진 데이터로 항상 결론을 내며("C 로 판단" 설명 가능), saturation 축은 OS별 실측 신호로 정규화하되
(Linux load/swap/iowait, Windows run queue/paging/disk queue) 해당 카운터를 못 읽어 값이 없으면
분류를 막지 않고 confidence 단서(unmeasured)로만 노출한다.

분류 enum: idle / shutdown / over_provisioned / under_provisioned / optimal / insufficient_data.

UI badge 임계값(`mappers._USAGE_DANGER_PCT`/`_USAGE_WARN_PCT`)과는 별 도메인:
- mapper 90/75 = 시점 사용량 시각 신호 (위험·주의·정상)
- 본 모듈 = WINDOW_DAYS(7일) 통계 기반 right-sizing 결정 (idle/over/under 등)

합성 규칙 (단일 진실):
- under = 위험 신호 OR (어떤 자원이든 고이용·포화·용량초과 하나라도 -> 누락 0)
- over  = 가용 이용률 AND (cpu·mem p95 가 둘 다 있고 둘 다 낮을 때만 -> 보수적)
- insufficient_data = cpu_p95·mem_p95 가 둘 다 None (진짜 평가 불가 = 신규/표본 부족)
"""

import math
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
CPU_SATURATION_LOAD_RATIO = 1.0  # load_15m / cpu_cores >= 1.0 — run queue saturation (Linux)
# Windows CPU saturation — Processor Queue Length 를 코어 수로 정규화 후 >= 2 (Microsoft "sustained > 2 per CPU").
# Linux loadavg 와 스케일이 다르다(loadavg = running+runnable+uninterruptible, run queue = ready 만)라 별도 상수.
CPU_RUN_QUEUE_PER_CORE_SATURATION = 2.0
IOWAIT_UPSIZE_PCT = 20  # iowait_p95 >= 20% — disk IO saturation (Linux)
# Windows disk IO saturation — 디스크당 Avg Disk Queue Length >= 2 (Microsoft 정석 병목 기준).
# agent 가 디스크별 큐를 발행 -> ingest 에서 per-device max 축약 -> 이 임계로 바로 비교 (정규화 불요).
DISK_QUEUE_PER_DISK_SATURATION = 2.0
# Windows memory saturation — Memory\Pages/sec(하드 페이지 폴트율) p95 >= 1000 pages/sec.
# 잠정 임계 (Microsoft rule-of-thumb "sustained > 1000"). disk_queue/cpu_run_queue 의 MS 표준 채택과 달리
# 절대 임계 근거가 약해 보수적으로 두고 실측 튜닝 대상 — 근거·한계는 docs/architecture/right-sizing.md.
MEM_PAGING_RATE_SATURATION = 1000.0
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
    "insufficient_data",  # cpu_p95·mem_p95 둘 다 부재 (신규/표본 부족)
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
    # Windows CPU saturation (Linux load 등가 축) — cpu_saturated 가 os-aware 로 소비.
    # cpu_run_queue_p95 = Processor Queue Length p95 (ready 상태 스레드 큐 깊이 gauge, per-core 정규화 후 비교).
    cpu_run_queue_p95: float | None = None
    # Windows Memory saturation (Linux swap page-out 등가 축) — mem_saturated 가 os-aware 로 소비.
    # mem_paging_rate_p95 = Memory\Pages/sec rate p95 (누적 counter -> pages/sec 환산된 하드 페이지 폴트율).
    mem_paging_rate_p95: float | None = None

    # ─── ADR 0052 신 모델 신호 (전부 default None/False — 기존 호출처 무손상 additive) ───
    # CPU
    cpu_percore_p95_max: float | None = None  # 코어별 p95 최대 — 단일스레드 병목 감지(집계로는 낮게 보임)
    procs_blocked_p95: float | None = None  # D-state IO 블록 p95 — 근본원인: IO발 CPU 로드 분리
    # 메모리
    mem_swap_paging: bool = False  # 스왑 page-out 발생(pswpin/pswpout rate > 0) — swap 호스트 포화 + 근본원인 판별
    mem_total_mb: int | None = None  # 현재 RAM — 사이징 목표 계산용
    # 디스크 I/O
    disk_await_p95_ms: float | None = None  # 응답 지연 p95 — virtio 포화 주신호(계층3 VMware/SQL)
    # 디스크 용량 (엔진이 mount 이력 Theil-Sen 으로 산출)
    disk_capacity_runway_days: float | None = None  # 바이트 소진까지 남은 일수(하락·수평이면 None=안 참)
    disk_inode_runway_days: float | None = None  # inode 소진까지 남은 일수
    # 네트워크 (품질 신호)
    net_retrans_pct: float | None = None  # TCP 재전송률 %
    net_drop_pct: float | None = None  # 드롭률 %
    # 신뢰도 입력 (4종 불확실성)
    history_hours: float | None = None  # 관측 이력 시간 — 통계 정밀도 바닥(계층3 AWS insufficient-data)
    cpu_burst_ratio: float | None = None  # p95/median — 버스티면 통계 정밀도 하향
    util_trend_rising: bool | None = None  # 이용률 유의한 상승 추세 — 다운사이즈 정상성 게이트
    cpu_steal_p95_pct: float | None = None  # 가상화 steal — 높으면 CPU 이용률·포화 오염(충실도 편향 단서)


@dataclass
class Assessment:
    """evidence 기반 분류 결과 — 분류 1개 + 근거(triggers) + 미관측 축(unmeasured).

    - triggers: under_provisioned 를 유발한 hit 신호 키 목록 (그 외 분류는 빈 목록).
                "어떤 데이터로 under 판정인가"의 근거. mapper 가 한국어 권고로 변환.
    - unmeasured: 평가하지 못한 saturation 축 키 목록 (os-aware helper 가 None 을 돌려준 축).
                  예: Windows perflib 미발행 -> ["cpu_saturation"] 등. confidence 단서(분류는 완결).
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
             Windows cpu_iowait 는 OS 개념 부재로 null 발행 — disk_queue 를 신호로 사용.
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


def cpu_saturated(stats: ResourceStats) -> bool | None:
    """CPU run queue 포화 여부 — OS별 raw 신호를 통일 축으로 정규화 (원칙 P2, os-aware).

    Linux: load_15m / cpu_cores >= CPU_SATURATION_LOAD_RATIO (run queue saturation).
    Windows: Processor Queue Length p95 / cpu_cores >= CPU_RUN_QUEUE_PER_CORE_SATURATION.
             Windows 는 loadavg 개념 부재 -> agent 가 Processor Queue Length 를 발행(loadavg 등가 축).
    측정 불가(값 None·cores 0)면 None -> assess 가 unmeasured("cpu_saturation")로 표시.
    assess·report·attention 이 본 helper 단일 진실 경유 (임계 재계산 금지).
    """
    if stats.cpu_cores is None or stats.cpu_cores <= 0:
        return None
    if stats.os_family == "windows":
        if stats.cpu_run_queue_p95 is None:
            return None
        return (stats.cpu_run_queue_p95 / stats.cpu_cores) >= CPU_RUN_QUEUE_PER_CORE_SATURATION
    if stats.cpu_load_15m_max is None:
        return None
    return (stats.cpu_load_15m_max / stats.cpu_cores) >= CPU_SATURATION_LOAD_RATIO


def mem_saturated(stats: ResourceStats) -> bool | None:
    """메모리 포화 여부 — OS별 raw 신호를 통일 축으로 정규화 (원칙 P2, os-aware).

    Linux: swap page-out 발생(swap_saturation). swap 은 항상 관측되므로 None 없음(측정됨).
    Windows: Memory\\Pages/sec rate p95 >= MEM_PAGING_RATE_SATURATION. pagefile 은 여유 RAM 에도
             상시 baseline 이라 swap 사용량이 아닌 페이징 rate 를 saturation 신호로 사용.
    Windows 에서 mem_paging_rate None 이면 None -> assess 가 unmeasured("mem_saturation")로 표시.
    assess·report·attention 이 본 helper 단일 진실 경유 (임계 재계산 금지).
    """
    if stats.os_family == "windows":
        if stats.mem_paging_rate_p95 is None:
            return None
        return stats.mem_paging_rate_p95 >= MEM_PAGING_RATE_SATURATION
    return swap_saturation(stats.os_family, stats.swap_used)


def assess(stats: ResourceStats) -> Assessment:
    """USE Method evidence 분류 — 자원별 가용 축을 신호로 모아 단일 분류 + 근거 산출.

    판정 순서: under(위험 신호 OR) → idle → shutdown → insufficient(데이터 없음) → over(이용률 AND) → optimal.
    under 가 idle/shutdown 보다 우선 — 어떤 위험 신호든 하나면 발화(누락 0). CPU 가 낮아도 스왑·iowait·load·
    mem·disk 압박이 있으면 "미사용(idle/shutdown)"이 아니라 자원 부족이다. over 는 cpu·mem 둘 다 낮을 때만(보수적).
    insufficient_data 는 utilization 도 없고 under 신호도 없을 때만 — swap·iowait 등 saturation 신호가
    있으면 util 부재여도 under 로 결론낸다(데이터로 반드시 판단). 못 본 saturation 축(예: Windows perflib
    미발행)은 unmeasured 에 기록 — 분류를 막지 않고 confidence 로만 노출.
    """
    cpu = stats.cpu_p95_pct
    mem = stats.mem_p95_pct

    # 못 본 saturation 축 기록 (confidence 단서) — os-aware helper 가 None 을 돌려준 축만.
    # 세 축 모두 OS별 신호 정규화(Linux load/swap/iowait, Windows run_queue/paging/disk_queue) — helper 단일 진실.
    # Linux 는 swap 이 항상 관측돼 mem_saturation 은 None 없음(측정됨). Windows 는 해당 perflib 못 읽으면 None.
    unmeasured: list[str] = []
    cpu_sat = cpu_saturated(stats)
    if cpu_sat is None:
        unmeasured.append("cpu_saturation")
    mem_sat = mem_saturated(stats)
    if mem_sat is None:
        unmeasured.append("mem_saturation")
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
    # saturation 3축 — os-aware helper 단일 진실(Linux load/swap/iowait, Windows run_queue/paging/disk_queue).
    if cpu_sat:
        triggers.append("cpu_saturation")
    if mem_sat:
        triggers.append("mem_saturation")
    if disk_sat:
        triggers.append("disk_io")

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

    # 평가 불가 — utilization(cpu·mem) 둘 다 부재 + 위 under 위험 신호도 없음 (신규/표본 부족).
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
    "mem_saturation": "메모리 페이징 압박",  # Linux swap page-out / Windows Pages/sec (os-aware)
    "disk_capacity": "디스크 용량 임박",
    "disk_io": "디스크 I/O 포화",
}


# ═══════════════════════════════════════════════════════════════════════════
# ADR 0052 — 자원 적정성 분류 재설계 (per-resource USE + 근본원인 종합 + 신뢰도 4종)
#
# 신 모델: 자원 5개를 각각 USE 로 판정하고 인과 근본원인으로 호스트 종합. 모든 임계는
# (계층, 출처) 선언. 구현 중 — 위 assess/classify(단일 분류)는 호출처 호환으로 유지하고
# Phase C/D 이관 후 Phase E 제거. 설계 단일 진실 = ADR 0052.
# ═══════════════════════════════════════════════════════════════════════════

# ─── 신 임계 (전부 tier 근거 — 계층·출처 명기) ───
RS_CPU_UNDER_PCT = 70  # 계층2 큐잉 무릎(Kleinrock) + 계층3 AWS Compute Optimizer Balanced(<70%, P95)
RS_CPU_SIZING_TARGET_PCT = 70  # 계층3 AWS Balanced — 증설·다운사이즈 공통 이용률 목표(비대칭 없음)
RS_CPU_SAT_HEADROOM = 0.7  # 여유 기준 — 증설 시 실행큐/코어를 포화선의 0.7배 아래로
RS_CPU_PERCORE_HOLD_PCT = 85  # 여유 기준 — 어느 코어든 p95 >= 85%면 다운사이즈/유휴 보류(단일스레드 보호)
RS_CPU_STEAL_BIAS_PCT = 5  # 여유 기준 — steal p95 >= 5%면 하이퍼바이저 경합으로 util/sat 오염(충실도 편향 단서)
RS_MEM_UNDER_PCT = 90  # 계층3 Azure Advisor(CPU·메모리 >= SKU 90% 시 resize)
RS_MEM_SIZING_TARGET_PCT = 70  # 계층3 AWS 최보수(30% headroom)
RS_DISK_RUNWAY_DAYS = 30  # 여유 기준 — 소진 30일 전 스토리지 추가 권고(lead time)
RS_DISK_STATIC_GUARD_PCT = 85  # 계층3 monitoring 표준(major) — 추세 신뢰도 낮을 때 fallback
RS_DISKIO_AWAIT_MS = 20  # 계층3 VMware(read >20ms critical) / SQL Server(~10-15ms)
RS_NET_RETRANS_PCT = 1.0  # 계층3 monitoring(재전송 >1% 성능 영향)
RS_NET_DROP_PCT = 0.5  # 계층3 monitoring(드롭 <0.5% 비즈니스 앱)
RS_CONFIDENCE_MIN_HOURS = 30  # 계층3 AWS insufficient-data(14일 창 누적 30h) — 미만이면 표본 부족
RS_DOWNSIZE_MIN_HOURS = 24 * 14  # 여유 기준 — 다운사이즈는 위험 방향이라 바닥보다 넉넉히(2주)
RS_BURST_RATIO_MAX = 2.0  # 여유 기준 — p95/median > 2 면 버스티(통계 정밀도 하향)

# OS별 CPU 포화선 (실행큐/코어) — Linux load 1.0 / Windows Processor Queue Length 2.0 (스케일 상이, ADR 0029 계승)
_RS_CPU_SAT_LINE = {"windows": 2.0}
_RS_CPU_SAT_LINE_DEFAULT = 1.0  # Linux/unknown


ResourceKind = Literal["cpu", "memory", "disk_capacity", "disk_io", "network"]
ResourceStatus = Literal[
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
    "unmeasured",  # 측정 불가
]


@dataclass
class ConfidenceNote:
    """신뢰도 4종 불확실성 — 종류별 하향 사유 (ADR 0052 신뢰도 축). 종류가 다르면 대응도 다르다."""

    low_precision: bool = False  # 통계: 표본 부족(<30h) or 버스티(p95/median>2)
    coverage_gap: bool = False  # 커버리지: 필요 축 미측정
    biased: bool = False  # 충실도: virtio·근사 계통 편향(표본으로 안 줄어듦)
    nonstationary: bool = False  # 정상성: 상승 추세(forward-looking 결정에만)

    @property
    def high(self) -> bool:
        """다운사이즈 처방 가능 수준 — 정밀·커버리지·충실도 온전(정상성은 별도 게이트)."""
        return not (self.low_precision or self.coverage_gap or self.biased)


@dataclass
class ResourceAssessment:
    """자원 하나의 판정 — 상태 + 근거 triggers + 사이징(가능 시) + 신뢰도."""

    kind: ResourceKind
    status: ResourceStatus
    triggers: list[str] = field(default_factory=list)
    sizing_target: int | None = None  # 목표 크기 (cpu=코어, memory=MB). None=사이징 불가/불요
    confidence: ConfidenceNote = field(default_factory=ConfidenceNote)
    detail: str = ""


HostStatus = Literal["under", "idle", "shutdown", "over", "optimal", "insufficient"]


@dataclass
class HostAssessment:
    """호스트 종합 — 자원별 판정 dict + 근본원인 root + 증상 억제 + 호스트 요약 상태."""

    resources: dict[str, ResourceAssessment]
    root_cause: str | None = None  # 원인 자원 kind
    symptom_of_root: list[str] = field(default_factory=list)  # root 의 증상으로 처방 억제된 kind
    host_status: HostStatus = "optimal"  # 정렬·배지용 호스트 요약 (조치는 root_cause·자원별에서)
    network_congested: bool = False  # 네트워크 품질 경고 (사이징 아님, 별도 플래그)


def _precision_low(stats: ResourceStats) -> bool:
    """통계 정밀도 하향? — 이력 30h 미만 or 버스티(p95/median > 2)."""
    if stats.history_hours is not None and stats.history_hours < RS_CONFIDENCE_MIN_HOURS:
        return True
    if stats.cpu_burst_ratio is not None and stats.cpu_burst_ratio > RS_BURST_RATIO_MAX:
        return True
    return False


def _base_confidence(stats: ResourceStats, *, biased: bool = False) -> ConfidenceNote:
    """자원 공통 신뢰도 뼈대 — 통계 정밀도·정상성·충실도. 커버리지는 자원별로 set."""
    return ConfidenceNote(
        low_precision=_precision_low(stats),
        nonstationary=bool(stats.util_trend_rising),
        biased=biased,
    )


def _run_queue_value(stats: ResourceStats) -> float | None:
    """사이징용 실행큐 값 — Linux load_15m / Windows Processor Queue Length."""
    return stats.cpu_run_queue_p95 if stats.os_family == "windows" else stats.cpu_load_15m_max


def _cpu_target_cores(util_pct: float, cores: int, run_queue: float | None, os_family: str | None) -> int:
    """AWS Balanced 사이징 — 이용률 70% 목표 + 포화 headroom 목표의 큰 쪽. 증설·다운사이즈 공통."""
    util_cores = math.ceil(util_pct * cores / RS_CPU_SIZING_TARGET_PCT) if util_pct > 0 else 1
    sat_cores = 0
    if run_queue and run_queue > 0:
        sat_line = _RS_CPU_SAT_LINE.get(os_family or "", _RS_CPU_SAT_LINE_DEFAULT)
        sat_cores = math.ceil(run_queue / (sat_line * RS_CPU_SAT_HEADROOM))  # 포화선의 0.7배 아래로
    return max(1, util_cores, sat_cores)


def assess_cpu(stats: ResourceStats) -> ResourceAssessment:
    """CPU 판정 — 이용률 70%/실행큐 포화로 under, AWS Balanced 사이징으로 target 산출.

    target > current -> under, target < current -> over(단일스레드 보호 시 보류), == -> optimal.
    """
    util = stats.cpu_p95_pct
    cores = stats.cpu_cores
    sat = cpu_saturated(stats)  # os-aware run queue (기존 helper 재사용)
    steal_biased = stats.cpu_steal_p95_pct is not None and stats.cpu_steal_p95_pct >= RS_CPU_STEAL_BIAS_PCT
    conf = _base_confidence(stats, biased=steal_biased)  # steal 높으면 util/sat 오염(충실도 편향)
    if sat is None:
        conf.coverage_gap = True
    if util is None and not sat:
        return ResourceAssessment("cpu", "unmeasured", confidence=conf, detail="이용률 미측정")
    if cores is None or cores <= 0:
        return ResourceAssessment("cpu", "unmeasured", confidence=conf, detail="코어 수 미상")
    target = _cpu_target_cores(util or 0.0, cores, _run_queue_value(stats), stats.os_family)
    triggers: list[str] = []
    if util is not None and util >= RS_CPU_UNDER_PCT:
        triggers.append("cpu_util")
    if sat:
        triggers.append("cpu_saturation")
    percore_busy = stats.cpu_percore_p95_max is not None and stats.cpu_percore_p95_max >= RS_CPU_PERCORE_HOLD_PCT
    if triggers or target > cores:
        return ResourceAssessment(
            "cpu", "under", triggers=triggers, sizing_target=target, confidence=conf, detail=f"목표 {target}코어"
        )
    if target < cores and not percore_busy:
        return ResourceAssessment("cpu", "over", sizing_target=target, confidence=conf, detail=f"목표 {target}코어")
    return ResourceAssessment("cpu", "optimal", sizing_target=cores, confidence=conf)


def _mem_target_mb(util_pct: float, total_mb: int | None) -> int | None:
    """메모리 사이징 — 이용률 70% 착지. total * util / 70 (절벽이라 CPU보다 여유 큼)."""
    if total_mb is None or util_pct <= 0:
        return None
    return math.ceil(total_mb * util_pct / RS_MEM_SIZING_TARGET_PCT)


def assess_memory(stats: ResourceStats) -> ResourceAssessment:
    """메모리 판정 — 이용률 90%(주신호) OR swap page-out 발생. 사이징 목표 70%.

    swapless(다수)는 이용률이 주신호, swap 호스트는 page-out 발생이 포화. 물리 무릎 없어 임계는 advisor prior.
    """
    util = stats.mem_p95_pct
    conf = _base_confidence(stats)
    if util is None:
        # 이용률 없어도 swap 발생이면 under(데이터로 판단), 아니면 unmeasured
        if stats.mem_swap_paging:
            return ResourceAssessment(
                "memory", "under", triggers=["mem_saturation"], confidence=conf, detail="이용률 미측정, 스왑 발생"
            )
        conf.coverage_gap = True
        return ResourceAssessment("memory", "unmeasured", confidence=conf, detail="이용률 미측정")
    triggers: list[str] = []
    if util >= RS_MEM_UNDER_PCT:
        triggers.append("mem_util")
    if stats.mem_swap_paging:
        triggers.append("mem_saturation")
    target_mb = _mem_target_mb(util, stats.mem_total_mb)
    if triggers:
        return ResourceAssessment(
            "memory",
            "under",
            triggers=triggers,
            sizing_target=target_mb,
            confidence=conf,
            detail=(f"목표 {target_mb}MB" if target_mb else "증설"),
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
    """디스크 용량 판정 — 소진까지 남은 시간(runway) 30일 미만이면 filling. 추세 못 내면 정적 가드 85% fallback."""
    conf = _base_confidence(stats)
    runway = _min_runway(stats.disk_capacity_runway_days, stats.disk_inode_runway_days)
    used = stats.disk_used_pct
    if runway is None:
        # 추세 못 냄(하락·수평·데이터 부족) -> 정적 가드
        if used is None:
            conf.coverage_gap = True
            return ResourceAssessment("disk_capacity", "unmeasured", confidence=conf, detail="용량 미측정")
        if used >= RS_DISK_STATIC_GUARD_PCT:
            return ResourceAssessment(
                "disk_capacity",
                "filling",
                triggers=["disk_capacity"],
                confidence=conf,
                detail=f"used {used:.0f}% (정적 가드)",
            )
        return ResourceAssessment("disk_capacity", "capacity_ok", confidence=conf, detail=f"used {used:.0f}%")
    if runway < RS_DISK_RUNWAY_DAYS:
        return ResourceAssessment(
            "disk_capacity", "filling", triggers=["disk_capacity"], confidence=conf, detail=f"{runway:.0f}일 후 소진"
        )
    return ResourceAssessment("disk_capacity", "capacity_ok", confidence=conf, detail=f"{runway:.0f}일 여유")


def assess_disk_io(stats: ResourceStats) -> ResourceAssessment:
    """디스크 I/O 판정 — await p95 > 20ms면 io_bound(표시만, 사이징 불가). virtio 간섭이라 충실도 편향."""
    conf = _base_confidence(stats, biased=True)  # virtio 게스트 await = 하이퍼바이저·이웃 간섭 편향
    await_ms = stats.disk_await_p95_ms
    if await_ms is None:
        conf.coverage_gap = True
        return ResourceAssessment(
            "disk_io", "unmeasured", confidence=conf, detail="응답 지연 미측정(구세대 viostor 등)"
        )
    if await_ms > RS_DISKIO_AWAIT_MS:
        return ResourceAssessment(
            "disk_io", "io_bound", triggers=["disk_io"], confidence=conf, detail=f"await p95 {await_ms:.0f}ms"
        )
    return ResourceAssessment("disk_io", "io_ok", confidence=conf, detail=f"await p95 {await_ms:.0f}ms")


def assess_network(stats: ResourceStats) -> ResourceAssessment:
    """네트워크 판정 — 사이징 축 아님. 재전송 >1% or 드롭 >0.5%면 congested(품질). errors 는 virtio 0 이라 미사용."""
    conf = _base_confidence(stats)
    retrans = stats.net_retrans_pct
    drop = stats.net_drop_pct
    triggers: list[str] = []
    if retrans is not None and retrans > RS_NET_RETRANS_PCT:
        triggers.append("net_retrans")
    if drop is not None and drop > RS_NET_DROP_PCT:
        triggers.append("net_drop")
    if triggers:
        parts = []
        if retrans is not None:
            parts.append(f"재전송 {retrans:.1f}%")
        if drop is not None:
            parts.append(f"드롭 {drop:.2f}%")
        return ResourceAssessment("network", "congested", triggers=triggers, confidence=conf, detail=" ".join(parts))
    if retrans is None and drop is None:
        conf.coverage_gap = True
        return ResourceAssessment("network", "unmeasured", confidence=conf, detail="품질 신호 미측정")
    return ResourceAssessment("network", "quality_ok", confidence=conf)


_ROOTABLE_UNDER = ("under", "io_bound")  # 처방 대상 상태 (자원별 부족 라벨)


def _host_status(stats: ResourceStats, res: dict[str, ResourceAssessment], under_kinds: set[str]) -> HostStatus:
    """호스트 요약 상태 — under(압박) > idle/shutdown > over > optimal > insufficient.

    조치는 root_cause·자원별 판정에서 나오고 이건 정렬·배지용 파생.
    idle/shutdown 은 호스트 레벨(CPU+net, 기존 임계 재사용).
    """
    if under_kinds:
        return "under"
    peak = stats.cpu_peak_pct
    net = stats.net_avg_kbps
    if net is not None:
        if peak is not None and peak <= IDLE_CPU_PEAK_PCT and net <= IDLE_NET_KBPS:
            return "idle"
        cpu = stats.cpu_p95_pct
        if cpu is not None and cpu <= SHUTDOWN_CPU_P95_PCT and (net * 8 / 1000) <= SHUTDOWN_NET_MBPS:
            return "shutdown"
    if any(res[k].status == "over" for k in ("cpu", "memory")):
        return "over"
    if all(a.status == "unmeasured" for a in res.values()):
        return "insufficient"
    return "optimal"


def rollup_host(stats: ResourceStats) -> HostAssessment:
    """호스트 종합 — 5자원 판정 후 인과 근본원인으로 root 를 짚고 하류(증상) 처방 억제.

    인과: 메모리 -> 디스크 I/O -> CPU. 판별: swap 발생 / procs_blocked(D-state) / await. iowait 미사용.
    root 만 처방, 하류는 "root 해결 후 재평가". 결합 신호 없이 각자 부족이면 각자(root=최상류, 증상 억제 없음).
    """
    res = {
        "cpu": assess_cpu(stats),
        "memory": assess_memory(stats),
        "disk_capacity": assess_disk_capacity(stats),
        "disk_io": assess_disk_io(stats),
        "network": assess_network(stats),
    }
    host = HostAssessment(resources=res)
    under_kinds = {k for k, a in res.items() if a.status in _ROOTABLE_UNDER}
    mem_pressure = res["memory"].status == "under"
    disk_io_pressure = res["disk_io"].status == "io_bound"
    cpu_pressure = res["cpu"].status == "under"
    procs_blocked_high = stats.procs_blocked_p95 is not None and stats.procs_blocked_p95 >= 1.0  # D-state 존재
    if mem_pressure and stats.mem_swap_paging and (disk_io_pressure or cpu_pressure):
        # 메모리발 -> 동반 디스크 I/O·CPU 는 swap 트래픽·대기의 증상
        host.root_cause = "memory"
        host.symptom_of_root = [k for k in ("disk_io", "cpu") if k in under_kinds]
    elif disk_io_pressure and cpu_pressure and procs_blocked_high:
        # 디스크발 -> CPU 로드는 D-state 블록의 증상
        host.root_cause = "disk_io"
        host.symptom_of_root = ["cpu"] if "cpu" in under_kinds else []
    elif under_kinds:
        # 단일 원인 또는 결합 신호 없음 -> 각자 처방 (root = 최상류 부족 자원, 증상 억제 없음)
        for k in ("memory", "disk_io", "cpu", "disk_capacity"):
            if k in under_kinds:
                host.root_cause = k
                break
    host.network_congested = res["network"].status == "congested"
    host.host_status = _host_status(stats, res, under_kinds)
    return host


def downsize_prescribable(assessment: ResourceAssessment, stats: ResourceStats) -> bool:
    """다운사이즈 '처방' 게이트 (ADR 0052) — over 분류는 늘 뜨나 구체 처방은 조건 만족 시만.

    잘못된 다운사이즈가 최악(전제2). 신뢰도 높음(정밀·커버리지·충실도) AND 상승추세 아님 AND 넉넉한 이력.
    미충족이면 분류는 over 유지하되 권고는 "관찰만".
    """
    if assessment.status != "over":
        return False
    if not assessment.confidence.high:
        return False
    if assessment.confidence.nonstationary:
        return False
    if stats.history_hours is not None and stats.history_hours < RS_DOWNSIZE_MIN_HOURS:
        return False
    return True


# ─── 신 모델 표시 라벨 (한국어, mapper/템플릿 표시용 — Phase D 에서 소비) ───
RS_STATUS_LABEL_KO: dict[str, str] = {
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

RS_HOST_STATUS_LABEL_KO: dict[str, str] = {
    "under": "자원 부족",
    "idle": "유휴",
    "shutdown": "종료 권장",
    "over": "과다 할당",
    "optimal": "정상",
    "insufficient": "표본 부족",
}

RS_TRIGGER_LABEL_KO: dict[str, str] = {
    "cpu_util": "CPU 이용률 초과",
    "cpu_saturation": "CPU 실행 큐 포화",
    "mem_util": "메모리 이용률 초과",
    "mem_saturation": "메모리 스왑/페이징 발생",
    "disk_capacity": "디스크 용량 임박",
    "disk_io": "디스크 I/O 응답 지연",
    "net_retrans": "TCP 재전송 과다",
    "net_drop": "패킷 드롭 과다",
}
