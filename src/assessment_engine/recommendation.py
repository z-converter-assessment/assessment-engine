"""Right-sizing 분류 — USE Method (Brendan Gregg) + 공식 cloud advisor 임계값.

명세·근거 단일 진실: docs/reference/right-sizing.md (분류 정의·임계 출처·OS 분기·한계).

evidence 기반 분류: 자원(CPU/Mem/Disk)별로 "가진 축"을 평가해 신호(trigger)를 모으고,
under(위험) 우선 우선순위로 단일 분류 하나 + 근거(triggers) + 미관측 축(unmeasured)을 산출한다.
가진 데이터로 항상 결론을 내며("C 로 판단" 설명 가능), saturation 축은 OS별 실측 신호로 정규화하되
(Linux procs_running/swap/await, Windows run queue/paging/disk queue) 해당 카운터를 못 읽어 값이 없으면
분류를 막지 않고 confidence 단서(unmeasured)로만 노출한다.

분류 enum(상태): idle(유휴) / over_provisioned / under_provisioned / optimal / insufficient_data.

UI badge 임계값(`mappers._USAGE_DANGER_PCT`/`_USAGE_WARN_PCT`)과는 별 도메인:
- mapper 90/75 = 시점 사용량 시각 신호 (위험·주의·정상)
- 본 모듈 = WINDOW_DAYS(14일) 통계 기반 right-sizing 결정 (idle/over/under 등)

합성 규칙 (단일 진실):
- under = 위험 신호 OR (어떤 자원이든 고이용·포화·용량초과 하나라도 -> 누락 0)
- over  = 가용 이용률 AND (cpu·mem p95 가 둘 다 있고 둘 다 낮을 때만 -> 보수적)
- insufficient_data = cpu_p95·mem_p95 가 둘 다 None (진짜 평가 불가 = 신규/표본 부족)
"""

import math
from dataclasses import dataclass, field
from typing import Literal

# ─── 임계값 ─────────────

# 관찰 윈도우 — 평가·차트·보고서 공통 표준 기간 (F10 단일 진실).
# 14일 = AWS Compute Optimizer 기본 lookback (계층3) — 최근 대표 부하. 분류·신뢰도 입력이 모두 이 창.
# runway(용량 추세)만 별도로 가용 이력 전체를 쓴다(누적 신호라 길수록 정확, report_aggregate mount_span).
WINDOW_DAYS = 14

# 유휴(상태) 진입선 — Azure Advisor 저사용 정의(cpu p95 <= 3%). 미사용 상태 단일축 — 종료·통합은 조치 층 파생.
IDLE_CPU_P95_PCT = 3
IDLE_NET_MBPS = 2
# 유휴 강도 "확실"(조치 층 — 상태 아님) — AWS Compute Optimizer idle 정의(거의 0). 종료 vs 통합 권고 문구 분기용.
IDLE_STRONG_PEAK_PCT = 1
IDLE_STRONG_NET_KBPS = 1

# Over-provisioned (다운사이즈) — AWS Compute Optimizer + GCP Recommender (headroom 30%)
CPU_DOWNSIZE_P95_PCT = 30
MEM_DOWNSIZE_P95_PCT = 50
HEADROOM_PCT = 30

# Under-provisioned (업사이즈) — USE Method utilization 임계
CPU_UPSIZE_P95_PCT = 70  # Kleinrock — Queueing Systems (1975), Google SRE Book
MEM_UPSIZE_P95_PCT = 80  # Linux page cache 압박 시작점

# USE Method Saturation 임계 — utilization 외 saturation 축 평가 (Brendan Gregg 정석).
# Linux CPU saturation — 실행 큐(procs_running)/cores >= 1.0 (USE Method: vmstat "r" > CPU 수, 계층1).
# load 대신 procs_running — load 는 D-state IO 블록이 섞여 오염(Gregg). procs_running 은 R-state 만(2.5.45+ 전역).
PROCS_RUNNING_PER_CORE_SATURATION = 1.0
# Windows CPU saturation — Processor Queue Length 를 코어 수로 정규화 후 >= 2 (Microsoft "sustained > 2 per CPU").
# Linux loadavg 와 스케일이 다르다(loadavg = running+runnable+uninterruptible, run queue = ready 만)라 별도 상수.
CPU_RUN_QUEUE_PER_CORE_SATURATION = 2.0
# Windows disk IO saturation — 디스크당 Avg Disk Queue Length >= 2 (Microsoft 정석 병목 기준).
# agent 가 디스크별 큐를 발행 -> ingest 에서 per-device max 축약 -> 이 임계로 바로 비교 (정규화 불요).
DISK_QUEUE_PER_DISK_SATURATION = 2.0
# Windows memory saturation — Memory\Pages Input/sec(하드 페이지 폴트율) p95 >= 20 pages/sec.
# Pages Input/sec 은 디스크에서 읽어온 하드 폴트만 세어 mmap 파일 I/O 미혼입(총 Pages/sec 과 대비 — agent 가
# 이 목적으로 별도 발행). Microsoft/업계 관례: sustained 5=증설 권고 / 20=체감 저하 / 100=thrashing.
# under-provisioned 신호는 "체감 저하" 20 채택(보수적). 총 Pages/sec 1000 은 카운터·자릿수 이중 오류라 폐기.
WIN_PAGES_INPUT_SATURATION = 20.0
DISK_CAPACITY_UPSIZE_PCT = 85  # worst mount used_pct >= 85% — storage capacity utilization

# 표본 충분성 — 측정 축(cpu/mem) 실측 5분 버킷 / 윈도우 기대 버킷(period_days*288, cagg 5분) 비율이 이 미만이면
# 표본 부족(low_sample). 분류를 막지 않고(원칙1) confidence 단서로만 노출(원칙2). 0.5 = 실질 관측 절반 미만.
SAMPLE_SUFFICIENCY_RATIO = 0.5


Recommendation = Literal[
    "idle",  # 유휴 — 수요≈0 미사용 상태. 조치(종료·통합)는 파생 권고 층(상태 아님).
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
    net_avg_kbps: float | None  # 유휴 판정용 (saturation metric 미수집)
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
    # mem_pages_input_rate_p95 = Memory\Pages Input/sec rate p95 (하드 페이지 폴트율, mmap 미혼입).
    mem_pages_input_rate_p95: float | None = None

    # ─── ADR 0052 신 모델 신호 (전부 default None/False — 기존 호출처 무손상 additive) ───
    # CPU
    cpu_percore_p95_max: float | None = None  # 코어별 p95 최대 — 단일스레드 병목 감지(집계로는 낮게 보임)
    procs_blocked_p95: float | None = None  # D-state IO 블록 p95 — 근본원인: IO발 CPU 로드 분리
    procs_running_p95: float | None = None  # R-state 실행 큐 p95 — Linux CPU 포화 신호(load 대체, IO 오염 없음)
    # 메모리
    mem_swap_paging: bool = False  # 스왑 page-out 발생(pswpin/pswpout rate > 0) — swap 호스트 포화 + 근본원인 판별
    oom_occurred: bool = False  # 창 안 OOM kill 발생 — 메모리 실패 사후 증거(강한 under 신호)
    mem_total_mb: int | None = None  # 현재 RAM — 사이징 목표 계산용
    # 디스크 I/O
    disk_await_p95_ms: float | None = None  # 응답 지연 p95 — virtio 포화 주신호(계층3 VMware/SQL)
    # 디스크 용량 (엔진이 mount 이력 전체 span 의 2점 fill_rate 로 산출 — report_aggregate mount_span)
    disk_capacity_runway_days: float | None = None  # 바이트 소진까지 남은 일수(하락·수평이면 None=안 참)
    disk_inode_runway_days: float | None = None  # inode 소진까지 남은 일수
    # worst mount inode 사용률 % — 정적 가드(바이트 85% 대칭, 수평 추세 소진 임박 발화)
    disk_inode_used_pct: float | None = None
    disk_capacity_target_gb: float | None = None  # 1년 수명 목표 총 용량(GB) — 소진 마운트 확장 목표
    # 네트워크 (품질 신호)
    net_retrans_pct: float | None = None  # TCP 재전송률 %
    net_drop_pct: float | None = None  # 드롭률 %
    conntrack_ratio: float | None = None  # nf_conntrack count/max — 연결테이블 고갈 임박(모듈 미로드 시 None=미측정)
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


def disk_io_saturated(stats: ResourceStats) -> bool | None:
    """디스크 I/O 포화 여부 — OS별 raw 신호를 지연 축으로 정규화 (원칙 P2, os-aware).

    Linux: await_p95 > RS_DISKIO_AWAIT_MS (IO 한 건당 응답 지연). iowait 대신 await —
           iowait 는 게스트 CPU 스케줄링 왜곡에 오염(virtio), await 는 디바이스 지연 직접 신호(계층3).
    Windows: 가장 바쁜 디스크의 큐 깊이(disk_queue_p95) >= DISK_QUEUE_PER_DISK_SATURATION —
             Windows 는 await(disk read/write time) 미발행이라 diskperf 큐 깊이를 지연 대리 신호로 사용.
             agent 가 디스크별 큐를 발행 -> ingest 에서 per-device max 축약(정규화 불요).
    await(응답 지연) 단일 축 통일 (ADR 0052 Phase 0): Linux/Windows 모두 disk_await_p95_ms > RS_DISKIO_AWAIT_MS.
    Windows 도 IOCTL_DISK_PERFORMANCE ReadTime/WriteTime 로 await 산출(에이전트 발행, 같은 IOCTL 라 큐와
    커버리지 동일). await 미배선/구세대 viostor(IOCTL 미부착)면 Windows 는 큐 깊이로 임시 폴백 —
    await 배선·검증 후 Phase E 에서 큐 폐기(DISK_QUEUE_PER_DISK_SATURATION·disk_queue_p95 제거).
    측정 불가(값 None)면 None -> assess 가 unmeasured("disk_io")로 표시.
    assess·assess_disk_io·report·attention 이 본 helper 단일 진실 경유 (임계 재계산 금지).
    """
    if stats.disk_await_p95_ms is not None:
        return stats.disk_await_p95_ms > RS_DISKIO_AWAIT_MS
    if stats.os_family == "windows" and stats.disk_queue_p95 is not None:
        return stats.disk_queue_p95 >= DISK_QUEUE_PER_DISK_SATURATION
    return None


def cpu_saturated(stats: ResourceStats) -> bool | None:
    """CPU run queue 포화 여부 — OS별 raw 신호를 통일 축으로 정규화 (원칙 P2, os-aware).

    Linux: procs_running p95 / cpu_cores >= PROCS_RUNNING_PER_CORE_SATURATION (실행 큐 R-state, USE).
           load 대신 procs_running — load 는 D-state IO 블록 오염(Gregg). 미발행(구 agent) 시 None(unmeasured).
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
    if stats.procs_running_p95 is None:
        return None
    return (stats.procs_running_p95 / stats.cpu_cores) >= PROCS_RUNNING_PER_CORE_SATURATION


def cpu_saturation_index(run_queue: float | None, cores: int | None, os_family: str | None) -> float | None:
    """CPU 포화 지수 = (실행 큐 / 코어) / os별 임계. >=1.0 이면 포화 — 실시간 현황 aggregate 통합 축.

    실행 큐 = Linux procs_running / Windows Processor Queue Length(COALESCE gauge, 분류 cpu_saturated 와 동일 신호).
    임계로 나눠 정규화하므로 Linux(임계 1.0)·Windows(임계 2.0)를 한 지수로 비교·랭킹(OS 분기 없이 aggregate).
    측정 불가(run_queue None·cores 0)면 None.
    """
    if run_queue is None or not cores:
        return None
    threshold = CPU_RUN_QUEUE_PER_CORE_SATURATION if os_family == "windows" else PROCS_RUNNING_PER_CORE_SATURATION
    return (run_queue / cores) / threshold


def disk_io_saturation_index(await_ms: float | None, disk_queue: float | None, os_family: str | None) -> float | None:
    """디스크 I/O 포화 지수 = 현재값 / os별 임계. >=1.0 이면 포화 — 실시간 aggregate 통합 축.

    Windows: Avg Disk Queue Length / DISK_QUEUE_PER_DISK_SATURATION. Linux: await(ms) / RS_DISKIO_AWAIT_MS.
    분류(disk_io_saturated)와 동일 신호. 임계 정규화로 OS 무관 한 지수 랭킹.
    """
    if os_family == "windows":
        return disk_queue / DISK_QUEUE_PER_DISK_SATURATION if disk_queue is not None else None
    return await_ms / RS_DISKIO_AWAIT_MS if await_ms is not None else None


def mem_pressure_active(pages_input_rate: float | None, pageout_delta: int | None, os_family: str | None) -> bool:
    """실시간 메모리 압박 여부 — Linux page-out 발생(pswpout delta>0) / Windows Pages Input/sec rate >= 임계.

    메모리 포화는 Linux 가 불리언(page-out 발생)이라 지수 아닌 압박 카운트로 집계(mem_saturated os-aware 정합).
    """
    if os_family == "windows":
        return pages_input_rate is not None and pages_input_rate >= WIN_PAGES_INPUT_SATURATION
    return bool(pageout_delta and pageout_delta > 0)


def mem_saturated(stats: ResourceStats) -> bool | None:
    """메모리 포화 여부 — OS별 raw 신호를 통일 축으로 정규화 (원칙 P2, os-aware).

    Linux: active page-out 발생(mem_swap_paging = pswpin/pswpout rate > 0). 정적 스왑 점유(swap_used)가
           아니다 — Linux 는 swappiness 로 여유 RAM 에도 유휴 페이지를 스왑아웃하므로 점유는 압박 신호가
           아니고, 실제 페이징 발생만이 포화(USE Method: saturation = paging rate, 계층1). swapless 는
           page-out 이 없어 항상 False(이용률이 주신호). page-out 은 항상 관측되므로 None 없음(측정됨).
    Windows: Memory\\Pages Input/sec rate p95 >= WIN_PAGES_INPUT_SATURATION(하드 폴트, mmap 미혼입).
             pagefile 사용량은 여유 RAM 에도 상시 baseline 이라 사용량이 아닌 하드폴트율을 신호로 사용.
    Windows 에서 하드폴트율 None 이면 None -> assess 가 unmeasured("mem_saturation")로 표시.
    assess·report·attention 이 본 helper 단일 진실 경유 (임계 재계산 금지).
    """
    if stats.os_family == "windows":
        if stats.mem_pages_input_rate_p95 is None:
            return None
        return stats.mem_pages_input_rate_p95 >= WIN_PAGES_INPUT_SATURATION
    return stats.mem_swap_paging


def assess(stats: ResourceStats) -> Assessment:
    """USE Method evidence 분류 — 자원별 가용 축을 신호로 모아 단일 분류 + 근거 산출.

    판정 순서: under(위험 신호 OR) → idle(유휴) → insufficient(데이터 없음) → over(이용률 AND) → optimal.
    under 가 유휴 보다 우선 — 어떤 위험 신호든 하나면 발화(누락 0). CPU 가 낮아도 스왑·iowait·load·
    mem·disk 압박이 있으면 "미사용(유휴)"이 아니라 자원 부족이다. over 는 cpu·mem 둘 다 낮을 때만(보수적).
    insufficient_data 는 utilization 도 없고 under 신호도 없을 때만 — swap·iowait 등 saturation 신호가
    있으면 util 부재여도 under 로 결론낸다(데이터로 반드시 판단). 못 본 saturation 축(예: Windows perflib
    미발행)은 unmeasured 에 기록 — 분류를 막지 않고 confidence 로만 노출.
    """
    cpu = stats.cpu_p95_pct
    mem = stats.mem_p95_pct

    # 못 본 saturation 축 기록 (confidence 단서) — os-aware helper 가 None 을 돌려준 축만.
    # 세 축 OS별 신호 정규화(Linux procs_running/swap/await, Windows run_queue/pages-input/disk_queue) helper 경유.
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

    # under_provisioned — 위험 신호 수집(OR). 유휴 보다 먼저 — 어떤 위험 신호든 하나면 발화(누락 0).
    # CPU 가 낮아도 스왑·iowait·load·mem·disk 압박이 있으면 "미사용"이 아니라 자원 부족이다
    # (예: CPU idle 인데 page-out = 메모리 부족). 가진 축만 평가해 hit 된 신호를 근거로 모은다.
    triggers: list[str] = []
    if cpu is not None and cpu >= CPU_UPSIZE_P95_PCT:
        triggers.append("cpu_util")
    if mem is not None and mem >= MEM_UPSIZE_P95_PCT:
        triggers.append("mem_util")
    if stats.disk_used_pct is not None and stats.disk_used_pct >= DISK_CAPACITY_UPSIZE_PCT:
        triggers.append("disk_capacity")
    # saturation 3축 — os-aware helper 단일 진실(Linux procs_running/swap/await, Windows run_queue/paging/disk_queue).
    if cpu_sat:
        triggers.append("cpu_saturation")
    if mem_sat:
        triggers.append("mem_saturation")
    if disk_sat:
        triggers.append("disk_io")

    if triggers:
        return Assessment("under_provisioned", triggers=triggers, unmeasured=unmeasured, low_sample=low_sample)

    # 유휴 — 위험 신호 0 일 때만 (진짜 미사용). Azure 저사용 정의(cpu p95<=3%, net<=2Mbps). net+cpu 의존, 없으면
    # fall-through. net_avg_kbps(KB/s) → Mbps: x 8 / 1000. 강도(AWS 1% 이하)는 상태 아닌 조치 층에서 분기.
    if (
        stats.net_avg_kbps is not None
        and cpu is not None
        and cpu <= IDLE_CPU_P95_PCT
        and (stats.net_avg_kbps * 8 / 1000) <= IDLE_NET_MBPS
    ):
        return Assessment("idle", unmeasured=unmeasured, low_sample=low_sample)

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
    "over_provisioned": "과다 할당",
    "under_provisioned": "자원 부족",
    "optimal": "정상",
    "insufficient_data": "표본 부족",
}

# 양식 A의 RISK 색상 매핑 — report.html `.rec-{recommendation}` CSS와 짝.
# 서로 다른 분류에 다른 클래스 — over=노랑(비용), under=빨강(위험), optimal=녹색.
BADGE_CLASS: dict[str, str] = {
    "idle": "rec-idle",
    "over_provisioned": "rec-over_provisioned",
    "under_provisioned": "rec-under_provisioned",
    "optimal": "rec-optimal",
    "insufficient_data": "rec-insufficient_data",
}

# host_status(rollup 5상태) -> Recommendation(표시 5상태). 카드 편입·도넛·배지가 rollup 단일 모델을 쓰게 해
# classify(옛 flat)와의 불일치(카드엔 있는데 근본원인·권고 빔)를 제거 — 편입 == under_kinds 존재 == root_cause 존재.
_HOST_STATUS_TO_REC: dict[str, Recommendation] = {
    "under": "under_provisioned",
    "idle": "idle",
    "over": "over_provisioned",
    "optimal": "optimal",
    "insufficient": "insufficient_data",
}


def classify_host(stats: ResourceStats) -> Recommendation:
    """rollup_host 기반 분류 — host_status 를 표시 5상태로. 근본원인·권고(rollup)와 항상 정합(classify 대체)."""
    return _HOST_STATUS_TO_REC[rollup_host(stats).host_status]


def host_status_to_recommendation(status: str) -> Recommendation:
    """HostAssessment.host_status -> 표시 Recommendation (host 재계산 없이 변환 — 통합 조치 표용)."""
    return _HOST_STATUS_TO_REC[status]


# 포화 축(USE saturation) 자원 — is_partial/confidence '포화 수치 미관측' 판정 대상. 용량(누적)·네트워크(품질)는 제외.
# 실제 판정 helper host_saturation_unmeasured 는 HostAssessment 정의 이후(rollup_host 근처) — forward-ref 회피.
_SATURATION_KINDS = ("cpu", "memory", "disk_io")


# 서버별 자원 적정성 표 최초 정렬 순서 — 전 서버(모든 분류). 자원 부족(시급) > 과다 > 유휴 > 정상 > 표본 부족.
CLASSIFICATION_ORDER: dict[str, int] = {
    "under_provisioned": 0,
    "over_provisioned": 1,
    "idle": 2,
    "optimal": 3,
    "insufficient_data": 4,
}


# ─── 조치 권고 — 상태에서 파생하는 단일 진실 (상태 vs 조치 분리) ──────────────
# 상태(LABEL_KO)는 "무엇인가", 본 층은 "그래서 뭘 하나". 표시 계층(report·environment_report)이 소비.
# class-level 기본 문구 — 분포 막대·분류 요약(per-host stats 없는 맥락). 유휴 per-host 세분은 recommend_action.
RECOMMENDATION_ACTION_KO: dict[str, str] = {
    "under_provisioned": "증설 검토",
    "over_provisioned": "축소 검토",
    "idle": "종료·통합 검토",
    "optimal": "적정 — 유지",
    "insufficient_data": "표본 부족 — 관측 지속",
}


def is_idle_strong(stats: ResourceStats) -> bool:
    """확실 유휴 — 거의 0 사용(AWS Compute Optimizer idle 정의: peak<=1% AND net<=1kB/s). 상태 아닌 조치 강도."""
    return (
        stats.cpu_peak_pct is not None
        and stats.cpu_peak_pct <= IDLE_STRONG_PEAK_PCT
        and stats.net_avg_kbps is not None
        and stats.net_avg_kbps <= IDLE_STRONG_NET_KBPS
    )


def recommend_action(rec: Recommendation, stats: ResourceStats) -> str:
    """상태 -> per-host 조치 권고 (단일 진실). 유휴는 강도로 분기 — 확실(거의 0)=즉시 종료 / 저사용=통합·재배치.

    under_provisioned 는 근본원인 기반 처방(under_prescription)이 필요해 호출자가 먼저 분기 —
    본 함수는 그 외 상태의 조치를 담당(호출자가 under 를 먼저 분기).
    """
    if rec == "idle":
        return "즉시 종료 검토" if is_idle_strong(stats) else "통합·재배치 검토"
    return RECOMMENDATION_ACTION_KO.get(rec, "")


# under_provisioned 신호 키 -> 한국어 라벨 (표시용). 처방은 under_prescription(root 기반) 단일 진실.
# triggers 를 사람용 근거로 변환할 때 참조. 표시 순서는 mapper 가 결정.
TRIGGER_LABEL_KO: dict[str, str] = {
    "cpu_util": "CPU 이용률 초과",
    "cpu_saturation": "CPU run queue 포화",
    "mem_util": "메모리 이용률 초과",
    "mem_saturation": "메모리 페이징 압박",  # Linux swap page-out / Windows Pages Input/sec 하드폴트 (os-aware)
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
RS_DISK_TARGET_RUNWAY_DAYS = 365  # 확장 목표 수명 — 현재 성장률로 1년 버티는 총 용량 산출(report_aggregate)
# 성장률 외삽을 신뢰할 최소 관측 span — 사이징 창(WINDOW_DAYS)만큼은 봐야 rate 를 1년으로 외삽. 짧으면 spike 과외삽.
RS_DISK_TREND_MIN_SPAN_DAYS = WINDOW_DAYS
# 근시 지평 — 짧은 span 에도 신뢰 가능한 근시 외삽 기간. 30일 예상 표시 + 짧은 span 확장 목표(30일 예상->headroom) 공통.
RS_DISK_NEAR_HORIZON_DAYS = 30
# 자료 부족 + 임계 초과 시 확장 목표 이용률 — 확장 후 used 착지값(headroom). CPU/메모리 사이징 착지(70%)와 정합.
RS_DISK_HEADROOM_TARGET_PCT = 70
RS_DISK_STATIC_GUARD_PCT = 85  # 계층3 monitoring 표준(major) — 추세 신뢰도 낮을 때 fallback
RS_DISKIO_AWAIT_MS = 20  # 계층3 VMware(read >20ms critical) / SQL Server(~10-15ms)
RS_NET_RETRANS_PCT = 1.0  # 계층3 monitoring(재전송 >1% 성능 영향)
RS_NET_DROP_PCT = 0.5  # 계층3 monitoring(드롭 <0.5% 비즈니스 앱)
# 계층3 monitoring — nf_conntrack count/max >= 80% = 연결테이블 고갈 임박(신규 연결 드롭 위험).
RS_CONNTRACK_SATURATION_RATIO = 0.8
RS_CONFIDENCE_MIN_HOURS = 30  # 계층3 AWS insufficient-data(14일 창 누적 30h) — 미만이면 통계 정밀도 하향(절대 바닥)
# 여유 기준 — 다운사이즈는 위험 방향이라 창이 충분히 관측됐을 때만 처방. 절대 시간 대신 창 대비 관측 비율로
# (관측/기대 버킷 >= 0.7) — 미세 갭 흡수 + WINDOW_DAYS 바뀌어도 문턱 불변. 0.7 = 창 30% 갭까지 허용.
RS_DOWNSIZE_MIN_SUFFICIENCY = 0.7
RS_BURST_RATIO_MAX = 2.0  # 여유 기준 — p95/median > 2 면 버스티(통계 정밀도 하향)
# 여유 기준 — 이용률 최소제곱 기울기 %/day 가 이 이상이면 유의한 상승(다운사이즈 정상성 게이트).
# Theil-Sen(강건) 대신 regr_slope(최소제곱) 산출을 임계로 이진화 — 다운사이즈 억제 방향이라 보수적 소값.
RS_UTIL_TREND_RISING_PCT_PER_DAY = 0.2

# OS별 CPU 포화선 (실행큐/코어) — Linux load 1.0 / Windows Processor Queue Length 2.0 (스케일 상이, ADR 0029 계승)
_RS_CPU_SAT_LINE = {"windows": 2.0}
_RS_CPU_SAT_LINE_DEFAULT = 1.0  # Linux/unknown


def util_trend_rising_from_slopes(cpu_slope: float | None, mem_slope: float | None) -> bool | None:
    """cpu·mem 이용률 기울기(%/day)로 상승 추세 판정 — 다운사이즈 정상성 게이트(도메인 임계 단일, 원칙 P2).

    둘 중 하나라도 임계 이상이면 상승(보수적 — 어느 코어 자원이든 성장하면 다운사이즈 보류).
    둘 다 None(표본<2 등 추세 산출 불가)이면 None -> nonstationary 미설정(다른 신뢰도 축이 짧은 이력 방어).
    """
    slopes = [s for s in (cpu_slope, mem_slope) if s is not None]
    if not slopes:
        return None
    return any(s >= RS_UTIL_TREND_RISING_PCT_PER_DAY for s in slopes)


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


HostStatus = Literal["under", "idle", "over", "optimal", "insufficient"]


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
    """사이징용 실행큐 값 — Linux procs_running p95 / Windows Processor Queue Length (포화 제약 사이징)."""
    return stats.cpu_run_queue_p95 if stats.os_family == "windows" else stats.procs_running_p95


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
        # under 증설 목표는 현재 코어 초과여야 유효 — 포화 주도로 util 목표가 현재 이하면 수치 사이징 불가(None).
        up = target if target > cores else None
        return ResourceAssessment(
            "cpu", "under", triggers=triggers, sizing_target=up, confidence=conf,
            detail=(f"목표 {up}코어" if up else "포화 주도 — 증설(수치 미상)"),
        )
    if target < cores and not percore_busy:
        return ResourceAssessment("cpu", "over", sizing_target=target, confidence=conf, detail=f"목표 {target}코어")
    return ResourceAssessment("cpu", "optimal", sizing_target=cores, confidence=conf)


def _mem_target_mb(util_pct: float, total_mb: int | None) -> int | None:
    """메모리 사이징 — 이용률 70% 착지. total * util / 70 (절벽이라 CPU보다 여유 큼)."""
    if total_mb is None or util_pct <= 0:
        return None
    return math.ceil(total_mb * util_pct / RS_MEM_SIZING_TARGET_PCT)


# 포화 주도 under 증설 headroom — swap/OOM 인데 util 이 낮아 util 사이징이 현재 이하일 때 현재 총량 + 이 비율로 상향.
# 30% = AWS/GCP advisor headroom prior(다운사이즈 headroom 과 동일 근거, 상향 방향 적용).
RS_MEM_SATURATION_HEADROOM_PCT = 30


def _mem_paging_active(stats: ResourceStats) -> bool:
    """메모리 페이징 포화 os-aware — mem_saturated 단일 진실 위임 (Windows Pages Input/sec rate, Linux page-out).

    mem_saturated 가 os-aware bool|None 을 돌려주고(Windows 미측정 None), 여기선 압박 발생 여부 bool 로 축약
    (None=미측정 -> False, 근본원인 종합·사이징의 "페이징 발생" 판정용). 임계·신호 해석은 mem_saturated 단일.
    """
    return bool(mem_saturated(stats))


def _mem_under_target(util_target: int | None, stats: ResourceStats) -> int | None:
    """메모리 under 증설 목표 — util 기반 목표와 포화 headroom(현재+30%) 중 큰 값. 현재 이하/총량 미상이면 None.

    이용률이 낮아도 swap/OOM 이면 현재 사양이 부족하다는 증거 — util 목표에 '현재+headroom' 을 덧대 현재 초과 보장.
    """
    total = stats.mem_total_mb
    candidates = [t for t in (util_target,) if t is not None]
    if total is not None and (_mem_paging_active(stats) or stats.oom_occurred):
        candidates.append(math.ceil(total * (1 + RS_MEM_SATURATION_HEADROOM_PCT / 100)))
    if not candidates:
        return None
    up = max(candidates)
    return up if (total is None or up > total) else None


def assess_memory(stats: ResourceStats) -> ResourceAssessment:
    """메모리 판정 — 이용률 90%(주신호) OR swap page-out 발생. 사이징 목표 70%.

    swapless(다수)는 이용률이 주신호, swap 호스트는 page-out 발생이 포화. 물리 무릎 없어 임계는 advisor prior.
    """
    util = stats.mem_p95_pct
    conf = _base_confidence(stats)
    if util is None:
        # 이용률 없어도 swap 발생·OOM 이면 under(데이터로 판단), 아니면 unmeasured
        signals = (("mem_saturation", _mem_paging_active(stats)), ("mem_oom", stats.oom_occurred))
        pressure = [t for t, hit in signals if hit]
        if pressure:
            return ResourceAssessment(
                "memory", "under", triggers=pressure, confidence=conf, detail="이용률 미측정, 압박 발생"
            )
        conf.coverage_gap = True
        return ResourceAssessment("memory", "unmeasured", confidence=conf, detail="이용률 미측정")
    triggers: list[str] = []
    if util >= RS_MEM_UNDER_PCT:
        triggers.append("mem_util")
    if _mem_paging_active(stats):
        triggers.append("mem_saturation")
    if stats.oom_occurred:
        triggers.append("mem_oom")  # OOM = 메모리 실패 사후 증거 (강한 under)
    target_mb = _mem_target_mb(util, stats.mem_total_mb)
    if triggers:
        # under 증설 목표 — util 기반 + 포화 headroom(현재+30%) 중 큰 값으로 현재 초과 보장(_mem_under_target).
        up = _mem_under_target(target_mb, stats)
        return ResourceAssessment(
            "memory",
            "under",
            triggers=triggers,
            sizing_target=up,
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
    """디스크 용량 판정 — 소진 runway 30일 미만이면 filling. 추세 못 내면 정적 가드 85% fallback.

    확장 목표(sizing_target, GB)는 세-경로(report_aggregate 산출): 추세 신뢰(span 충분)면 1년 수명 목표 /
    소진 임박·짧은 span 이면 30일 예상 used 를 headroom 착지(근시라 현실적) / 추세 없이 임계 초과면 이용률 headroom.
    짧은 span 을 365일로 과외삽한 비현실적 값 방지 + 소진 임박이면 항상 구체적 목표 제공.
    """
    conf = _base_confidence(stats)
    runway = _min_runway(stats.disk_capacity_runway_days, stats.disk_inode_runway_days)
    used = stats.disk_used_pct
    inode_used = stats.disk_inode_used_pct
    tgt = stats.disk_capacity_target_gb
    if runway is not None and runway < RS_DISK_RUNWAY_DAYS:
        # 소진 임박(추세 주도). 목표는 경로1(1년 수명, span 충분 시만). inode 소진이 먼저면 목표 없음(용량 확장 무관).
        rtgt = tgt if runway == stats.disk_capacity_runway_days else None
        detail = f"{runway:.0f}일 후 소진" + (f", 목표 {rtgt:.0f}GB" if rtgt else "")
        return ResourceAssessment(
            "disk_capacity", "filling", triggers=["disk_capacity"], sizing_target=rtgt, confidence=conf, detail=detail
        )
    # 정적 가드 — 축별로 그 축 추세가 없을 때(runway None = 수평·하락·데이터 부족) 사용률 >= 85%. 바이트/inode 대칭.
    # 수평 추세 + inode 95% 소진처럼 추세 runway 로 안 잡히는 임박을 포착(계층3 monitoring 표준).
    _guard = RS_DISK_STATIC_GUARD_PCT
    byte_static = stats.disk_capacity_runway_days is None and used is not None and used >= _guard
    inode_static = stats.disk_inode_runway_days is None and inode_used is not None and inode_used >= _guard
    if byte_static:
        detail = f"used {used:.0f}% (정적 가드)" + (f", 목표 {tgt:.0f}GB" if tgt else "")
        return ResourceAssessment(
            "disk_capacity", "filling", triggers=["disk_capacity"], sizing_target=tgt, confidence=conf, detail=detail
        )
    if inode_static:
        # inode 소진은 용량 확장(GB)으로 안 풀림(mkfs 고정) -> 목표 없음, 파일 정리·재포맷 처방 층에서.
        return ResourceAssessment(
            "disk_capacity", "filling", triggers=["disk_capacity"], confidence=conf,
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


def assess_disk_io(stats: ResourceStats) -> ResourceAssessment:
    """디스크 I/O 판정 — await p95 > 20ms면 io_bound(표시만, 사이징 불가). virtio 간섭이라 충실도 편향."""
    conf = _base_confidence(stats, biased=True)  # virtio 게스트 지연 = 하이퍼바이저·이웃 간섭 편향
    sat = disk_io_saturated(stats)  # 단일 진실 — await 우선(양 OS), Windows await 미배선 시 큐 폴백
    if sat is None:
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
    """네트워크 판정 — 사이징 축 아님. 재전송 >1% or 드롭 >0.5%면 congested(품질). errors 는 virtio 0 이라 미사용."""
    conf = _base_confidence(stats)
    retrans = stats.net_retrans_pct
    drop = stats.net_drop_pct
    conntrack = stats.conntrack_ratio
    triggers: list[str] = []
    if retrans is not None and retrans > RS_NET_RETRANS_PCT:
        triggers.append("net_retrans")
    if drop is not None and drop > RS_NET_DROP_PCT:
        triggers.append("net_drop")
    if conntrack is not None and conntrack >= RS_CONNTRACK_SATURATION_RATIO:
        triggers.append("net_conntrack")  # 연결테이블 고갈 임박 — 신규 연결 드롭 위험(NAT·프록시·방화벽)
    if triggers:
        parts = []
        if retrans is not None:
            parts.append(f"재전송 {retrans:.1f}%")
        if drop is not None:
            parts.append(f"드롭 {drop:.2f}%")
        if "net_conntrack" in triggers:
            parts.append(f"conntrack {conntrack * 100:.0f}%")
        return ResourceAssessment("network", "congested", triggers=triggers, confidence=conf, detail=" ".join(parts))
    if retrans is None and drop is None and conntrack is None:
        conf.coverage_gap = True
        return ResourceAssessment("network", "unmeasured", confidence=conf, detail="품질 신호 미측정")
    return ResourceAssessment("network", "quality_ok", confidence=conf)


# 처방 대상 상태 (부족·병목·소진임박) — 호스트 under/over 축. 네트워크 congested 는 제외:
# 원칙상 네트워크는 사이징(under/over) 축이 아니라 품질 신호라, 호스트를 "자원 부족"으로 분류하지 않고
# HostAssessment.network_congested 플래그로만 orthogonal 노출 (별도 "네트워크 혼잡" 경고). ADR 0052 정합.
_ROOTABLE_UNDER = ("under", "io_bound", "filling")


def _host_status(stats: ResourceStats, res: dict[str, ResourceAssessment], under_kinds: set[str]) -> HostStatus:
    """호스트 요약 상태 — under(압박) > idle(미사용) > over > optimal > insufficient.

    조치는 root_cause·자원별 판정에서 나오고 이건 정렬·배지용 파생.
    유휴는 호스트 레벨(CPU p95 + net, Azure 저사용 정의) 파생.
    """
    if under_kinds:
        return "under"
    cpu = stats.cpu_p95_pct
    net = stats.net_avg_kbps
    if net is not None and cpu is not None and cpu <= IDLE_CPU_P95_PCT and (net * 8 / 1000) <= IDLE_NET_MBPS:
        return "idle"
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
    if mem_pressure and _mem_paging_active(stats) and (disk_io_pressure or cpu_pressure):
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


def host_saturation_unmeasured(host: HostAssessment) -> bool:
    """호스트 포화 축(cpu·memory·disk_io) 중 하나라도 미관측인지 — is_partial·confidence '포화 수치 미관측' 단일 진실.

    disk_capacity(용량 누적)·network(품질) 미측정은 포화 축이 아니라 제외 — 구 assess.is_partial(saturation 축 한정)
    의 신 모델 대응. Windows perflib 미발행·구세대 viostor await 미측정 등이 여기로 노출된다.
    """
    return any(host.resources[k].confidence.coverage_gap for k in _SATURATION_KINDS if k in host.resources)


# 자원 부족 처방 기본 문구 (root 자원별). 사이징 축(cpu/memory)은 목표 있으면 "-> 총량 목표"로 대체.
_UNDER_ACTION_BASE: dict[str, str] = {
    "cpu": "CPU 증설",
    "memory": "메모리 증설",
    "disk_capacity": "스토리지 확장",
    "disk_io": "디스크 티어 상향",
    "network": "네트워크 점검",
}
_SIZEABLE_LABEL: dict[str, str] = {"cpu": "CPU", "memory": "메모리"}
_UNDER_ORDER = ("memory", "cpu", "disk_io", "disk_capacity", "network")  # 나열 순(인과 상류 우선)


def _fmt_sizing_target(kind: str, target: int) -> str:
    """사이징 목표 총량 표시 — cpu 코어, memory 는 1024MB 이상이면 GB(소수1, .0 제거)."""
    if kind == "cpu":
        return f"{target}코어"
    if target >= 1024:
        return f"{target / 1024:.1f}GB".replace(".0GB", "GB")
    return f"{target}MB"


def _resource_prescription(kind: str, ra: ResourceAssessment) -> str:
    """자원 1개 처방 — 사이징 목표 있으면 "메모리 -> 22GB"·"스토리지 -> 500GB"(총량 목표), 없으면 기본 문구."""
    if kind == "disk_capacity" and ra.sizing_target is not None:
        return f"스토리지 -> {ra.sizing_target:.0f}GB"  # 1년 수명 목표 총 용량
    if kind in _SIZEABLE_LABEL and ra.sizing_target is not None:
        return f"{_SIZEABLE_LABEL[kind]} -> {_fmt_sizing_target(kind, ra.sizing_target)}"
    return _UNDER_ACTION_BASE.get(kind, "")


def _under_kinds(host: HostAssessment) -> list[str]:
    return [k for k in _UNDER_ORDER if k in host.resources and host.resources[k].status in _ROOTABLE_UNDER]


def under_prescription(host: HostAssessment) -> str:
    """자원 부족 처방 (root_cause 정합) — 인과 결합이면 root 만 처방(하류는 근본원인 칼럼이 전달), 독립 부족이면 전부.

    root 에만 처방해 삼중 처방 방지(ADR 0052). 근본원인 칼럼(root_cause_display)과 어휘 정합.
    """
    under = _under_kinds(host)
    if not under:
        return ""
    if host.symptom_of_root and host.root_cause:
        return _resource_prescription(host.root_cause, host.resources[host.root_cause])
    return " / ".join(_resource_prescription(k, host.resources[k]) for k in under)


def root_cause_display(host: HostAssessment) -> str:
    """근본원인 칼럼 표시 (under_prescription 과 정합):

    - 단일 부족: 그 자원명 ("CPU") — 원인이 자명.
    - 인과 결합: "메모리 (CPU·디스크 I/O 유발)" — root + 하류 증상.
    - 복수 독립: "CPU·디스크 I/O" — 각자 원인(단일 root 없음, 인과 함의 없이 나열).
    - 부족 없음: "".
    """
    under = _under_kinds(host)
    if not under:
        return ""
    if host.symptom_of_root and host.root_cause:
        sym = "·".join(RS_RESOURCE_KIND_LABEL_KO[k] for k in host.symptom_of_root)
        return f"{RS_RESOURCE_KIND_LABEL_KO[host.root_cause]} ({sym} 유발)"
    if len(under) == 1:
        return RS_RESOURCE_KIND_LABEL_KO[under[0]]
    return "·".join(RS_RESOURCE_KIND_LABEL_KO[k] for k in under)


def downsize_prescribable(assessment: ResourceAssessment, stats: ResourceStats) -> bool:
    """다운사이즈 '처방' 게이트 (ADR 0052) — over 분류는 늘 뜨나 구체 처방은 조건 만족 시만.

    잘못된 다운사이즈가 최악(전제2). 신뢰도 높음(정밀·커버리지·충실도) AND 상승추세 아님 AND 창이 충분히 관측됨.
    이력 문턱은 창 대비 관측 비율(sample_sufficiency >= 0.7) — 절대 시간 아님(WINDOW_DAYS 바뀌어도 불변, 미세 갭 흡수).
    sufficiency None(측정 축 부재)이면 처방 불가(관찰만). 미충족이면 분류는 over 유지하되 권고는 "관찰만".
    """
    if assessment.status != "over":
        return False
    if not assessment.confidence.high:
        return False
    if assessment.confidence.nonstationary:
        return False
    if stats.sample_sufficiency is None or stats.sample_sufficiency < RS_DOWNSIZE_MIN_SUFFICIENCY:
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
    "over": "과다 할당",
    "optimal": "정상",
    "insufficient": "표본 부족",
}

# 자원 kind -> 한국어 (rollup_host.root_cause 표시 — "어느 자원발인가"). 근본원인 컬럼 단일 진실.
RS_RESOURCE_KIND_LABEL_KO: dict[str, str] = {
    "cpu": "CPU",
    "memory": "메모리",
    "disk_capacity": "디스크 용량",
    "disk_io": "디스크 I/O",
    "network": "네트워크",
}

RS_TRIGGER_LABEL_KO: dict[str, str] = {
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
