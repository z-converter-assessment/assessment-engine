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

# 유휴(상태) 진입선 — 미사용 VM(종료·통합 후보) 판별. 활동(activity) 3축이 전부 quiescent 해야 유휴:
# CPU p95(Azure Advisor 저사용 3%) · 네트워크(2 Mbps) · 디스크 I/O baseline IOPS. 메모리 사용률·디스크 용량은
# 활동이 아닌 할당(크기)이라 유휴 신호에서 제외(baseline 메모리·큰 빈 디스크가 있어도 미사용일 수 있음).
IDLE_CPU_P95_PCT = 3
IDLE_NET_MBPS = 2
IDLE_DISK_IOPS = 5  # 디스크 I/O 거의 정지(로그·주기 flush 수준). 측정된 활동만 유휴 배제, 미측정은 불배제.
# 유휴 강도 "확실"(조치 층 — 상태 아님) — AWS Compute Optimizer idle 정의(거의 0). 종료 vs 통합 권고 문구 분기용.
IDLE_STRONG_PEAK_PCT = 1
IDLE_STRONG_NET_KBYTES_PER_S = 1  # kB/s(킬로바이트)

# "부하 변동 큼" 서술의 peak 유의미 하한 — peak 가 이 저부하선을 넘어야 burst 로 발화(저부하 지터 오탐 방지).
# 실제 다운사이즈(over) 판정 아님 — 그건 assess_cpu 의 target<cores(util/70 사이징). 표시 서술 gate 전용.
BURST_PEAK_FLOOR_CPU_PCT = 30
BURST_PEAK_FLOOR_MEM_PCT = 50

# CPU 이용률 부족선 — Kleinrock 큐잉 무릎 + AWS Balanced(<70% P95). 보고서 요약 KPI 표시 임계.
CPU_UPSIZE_P95_PCT = 70

# USE Method Saturation 임계 — utilization 외 saturation 축 평가 (Brendan Gregg 정석).
# Linux CPU saturation — 실행 큐(procs_running)/cores >= 1.0 (USE Method: vmstat "r" > CPU 수, 계층1).
# load 대신 procs_running — load 는 D-state IO 블록이 섞여 오염(Gregg). procs_running 은 R-state 만(2.5.45+ 전역).
PROCS_RUNNING_PER_CORE_SATURATION = 1.0
# Windows CPU saturation — Processor Queue Length 를 코어 수로 정규화 후 >= 2 (Microsoft "sustained > 2 per CPU").
# Linux 1.0 과 값이 다른 건 모집단이 달라서지 임계 불일치가 아니다: Linux procs_running 은 실행 중 태스크 포함
# (R-state = running + runnable), Windows Processor Queue 는 대기(ready-to-run)만 세고 실행 중 스레드는 뺀다.
# 즉 같은 포화 지점을 Windows 쪽은 코어당 +1 만큼 낮게 세므로 각 OS 정본 임계(1 vs 2)를 그대로 쓴다.
CPU_RUN_QUEUE_PER_CORE_SATURATION = 2.0
# Windows disk IO saturation — 디스크당 Avg Disk Queue Length >= 2 (Microsoft 정석 병목 기준).
# agent 가 디스크별 큐를 발행 -> ingest 에서 per-device max 축약 -> 이 임계로 바로 비교 (정규화 불요).
DISK_QUEUE_PER_DISK_SATURATION = 2.0
# Windows memory saturation — Memory\Pages Input/sec(하드 페이지 폴트율) p95 >= 20 pages/sec.
# Pages Input/sec 은 디스크에서 읽어온 하드 폴트만 세어 mmap 파일 I/O 미혼입(총 Pages/sec 과 대비 — agent 가
# 이 목적으로 별도 발행). Microsoft/업계 관례: sustained 5=증설 권고 / 20=체감 저하 / 100=thrashing.
# under-provisioned 신호는 "체감 저하" 20 채택(보수적).
WIN_PAGES_INPUT_SATURATION = 20.0
# 근본원인 인과 게이트 — procs_blocked(D-state, uninterruptible sleep) p95 >= 1 이면 "블록된 프로세스 존재".
# 디스크발 CPU 로드(디스크 I/O 대기로 프로세스가 D-state 로 쌓임)를 판별해 root_cause 를 disk_io 로 귀속. run
# queue(R-state)와 대비되는 blocked queue — 1(적어도 한 프로세스가 상시 블록)이 존재 임계(Gregg USE saturation).
PROCS_BLOCKED_DSTATE_SATURATION = 1.0


Recommendation = Literal[
    "idle",  # 유휴 — 수요≈0 미사용 상태. 조치(종료·통합)는 파생 권고 층(상태 아님).
    "over_provisioned",
    "under_provisioned",
    "optimal",
    "insufficient_data",  # cpu_p95·mem_p95 둘 다 부재 (신규/표본 부족)
]

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
    net_avg_kbytes_per_s: float | None  # kB/s(킬로바이트, 킬로비트 아님). 유휴 판정용 (saturation metric 미수집)
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

    # ─── per-resource USE 신호 (전부 default None/False — 미보유 agent 는 graceful skip) ───
    # CPU
    cpu_percore_p95_max: float | None = None  # 코어별 p95 최대 — 단일스레드 병목 감지(집계로는 낮게 보임)
    procs_blocked_p95: float | None = None  # D-state IO 블록 p95 — 근본원인: IO발 CPU 로드 분리
    procs_running_p95: float | None = None  # R-state 실행 큐 p95 — Linux CPU 포화 신호(IO 대기 미혼입)
    # 메모리
    mem_swap_paging: bool = False  # 스왑 page-out 발생(pswpin/pswpout rate > 0) — swap 호스트 포화 + 근본원인 판별
    oom_occurred: bool = False  # 창 안 OOM kill 발생 — 메모리 실패 사후 증거(강한 under 신호)
    mem_total_mb: int | None = None  # 현재 RAM — 사이징 목표 계산용
    mem_near_peak_pct: float | None = None  # near-peak(버킷별 max 의 p95) 메모리 사이징(비탄력 피크). p95 util=판정
    # 디스크 I/O
    disk_await_p95_ms: float | None = None  # 응답 지연 p95 — virtio 포화 주신호(계층3 VMware/SQL)
    disk_iops_baseline: float | None = None  # 디스크 I/O 활동량(baseline 평균 IOPS) — 유휴 판정 활동 축(포화 아님)
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
        rq_sat = (stats.cpu_run_queue_p95 / stats.cpu_cores) >= CPU_RUN_QUEUE_PER_CORE_SATURATION
    else:
        if stats.procs_running_p95 is None:
            return None
        rq_sat = (stats.procs_running_p95 / stats.cpu_cores) >= PROCS_RUNNING_PER_CORE_SATURATION
    if not rq_sat:
        return False
    # dual-gate: 실행 큐가 높아도 CPU 가 실제로 바쁠 때(util >= under 임계)만 포화. procs_running/Processor Queue
    # 는 수집기 자신(R-state)을 포함해 저활동 시스템(특히 1코어)에서 상시 >= 1/core 노이즈 -> util 이 측정됐는데
    # 유휴/저활동이면 코어 부족이 아니다(물리적 모순: 실행 큐가 실제면 util 도 높음). 메모리 dual-gate 와 동형.
    # util 미측정이면 모순 증거가 없으므로 측정된 실행 큐 신호를 신뢰(측정된 저활동만 배제).
    if stats.cpu_p95_pct is None:
        return True
    return stats.cpu_p95_pct >= RS_CPU_UNDER_PCT


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
    """디스크 I/O 포화 지수 = 현재값 / 임계. >=1.0 이면 포화 — 실시간 aggregate 통합 축.

    await(ms) / RS_DISKIO_AWAIT_MS 우선(양 OS 통일 — disk_io_saturated 와 동일 로직). Windows await 미배선/
    구세대 viostor 면 큐 깊이 / DISK_QUEUE_PER_DISK_SATURATION 폴백. 임계 정규화로 OS 무관 한 지수 랭킹.
    """
    if await_ms is not None:
        return await_ms / RS_DISKIO_AWAIT_MS
    if os_family == "windows" and disk_queue is not None:
        return disk_queue / DISK_QUEUE_PER_DISK_SATURATION
    return None


def mem_pressure_active(paging_major_rate: float | None, os_family: str | None) -> bool:
    """실시간 메모리 압박 여부 — 하드폴트(major fault) rate 기반. Windows Pages Input/sec >= 임계 / Linux refault > 0.

    Linux refault·Windows Pages Input 은 paging_major_rate 파라미터로 정규화 전달(SaturationRaw, run_queue 와
    동일한 OS-neutral 관례) — 호출자(latest_saturation SQL)가 os_family 로 물리 컬럼(Linux paging_major /
    Windows paging_in)을 미리 선택해 넘긴다. 메모리 포화는 지수 아닌 압박 불리언으로 집계(mem_saturated
    os-aware 정합).
    """
    if paging_major_rate is None:
        return False
    if os_family == "windows":
        return paging_major_rate >= WIN_PAGES_INPUT_SATURATION
    return paging_major_rate > 0


def net_signal_active(
    retrans_pct: float | None, drop_pct: float | None,
    conntrack_ratio: float | None, net_kbytes_per_s: float | None,
) -> bool:
    """실시간 네트워크 혼잡 신호 여부 — assess_network 트리거와 동일 임계·저트래픽 게이트(스냅샷용).

    retrans/drop 은 트래픽 < RS_NET_MIN_TRAFFIC_KBPS 면 억제(저트래픽 부팅기 소수 이벤트가 비율 지배 방지),
    conntrack(연결테이블 고갈)은 트래픽 무관 절대 신호라 게이트 제외 — assess_network 와 동형.
    """
    low_traffic = net_kbytes_per_s is not None and net_kbytes_per_s < RS_NET_MIN_TRAFFIC_KBPS
    if not low_traffic and retrans_pct is not None and retrans_pct > RS_NET_RETRANS_PCT:
        return True
    if not low_traffic and drop_pct is not None and drop_pct > RS_NET_DROP_PCT:
        return True
    return conntrack_ratio is not None and conntrack_ratio >= RS_CONNTRACK_SATURATION_RATIO


def mem_saturated(stats: ResourceStats) -> bool | None:
    """메모리 포화 여부 — dual-gate: 이용률 높음 AND 페이징 발생 (원칙 P2, os-aware).

    Gate0 확정: paging 단독은 mmap/프로세스 시작의 정상 하드폴트를 오탐하고(RAM 여유 많은 mmap DB 를
    포화로 오인), 이용률 단독은 페이지캐시로 90% 찬 정상을 오탐한다. 둘 다 참일 때만 포화 —
    이용률 p95 >= RS_MEM_UNDER_PCT AND 페이징 발생. oom 은 별도로 즉시 under(assess_memory).
    - Linux 페이징 = mem_swap_paging(paging_major refault rate sustained, build_resource_stats 배선).
    - Windows 페이징 = Pages Input/sec p95 >= WIN_PAGES_INPUT_SATURATION(하드 폴트, mmap 미혼입).
    이용률 미측정(None)이면 gate 불가 -> None(assess 가 unmeasured/oom 로 처리).
    assess·report·attention 이 본 helper 단일 진실 경유 (임계 재계산 금지).
    """
    if stats.mem_p95_pct is None:
        return None  # 이용률 미측정 -> dual-gate 불가
    if stats.mem_p95_pct < RS_MEM_UNDER_PCT:
        return False  # 이용률 낮음 -> 페이징 있어도 포화 아님(mmap/시작 폴트 오탐 차단)
    if stats.os_family == "windows":
        if stats.mem_pages_input_rate_p95 is None:
            return None
        return stats.mem_pages_input_rate_p95 >= WIN_PAGES_INPUT_SATURATION
    return stats.mem_swap_paging


# ─── UI 라벨 (한국어, 양식 A 사용자 친화 표시용) ──────────────────────────

LABEL_KO: dict[str, str] = {
    "idle": "유휴",
    "over_provisioned": "과다 할당",
    "under_provisioned": "자원 부족",
    "optimal": "정상",
    "insufficient_data": "표본 부족",
}

# 양식 A의 RISK 색상 매핑 — report.html `.rec-{recommendation}` CSS와 짝.
# 서로 다른 분류에 다른 클래스 — under=빨강(위험), over=파랑, optimal=녹색, idle=회색.
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
        and stats.net_avg_kbytes_per_s is not None
        and stats.net_avg_kbytes_per_s <= IDLE_STRONG_NET_KBYTES_PER_S
    )


def recommend_action(rec: Recommendation, stats: ResourceStats) -> str:
    """상태 -> per-host 조치 권고 (단일 진실). 유휴는 강도로 분기 — 확실(거의 0)=즉시 종료 / 저사용=통합·재배치.

    under_provisioned 는 근본원인 기반 처방(under_prescription)이 필요해 호출자가 먼저 분기 —
    본 함수는 그 외 상태의 조치를 담당(호출자가 under 를 먼저 분기).
    """
    if rec == "idle":
        return "즉시 종료 검토" if is_idle_strong(stats) else "통합·재배치 검토"
    return RECOMMENDATION_ACTION_KO.get(rec, "")


# ═══════════════════════════════════════════════════════════════════════════
# 자원 적정성 분류 — 자원 5개를 각각 USE 로 판정하고 인과 근본원인으로 호스트를 종합한다.
# 임계는 전부 (계층, 출처)를 명기한다. 명세·근거 단일 진실 = `docs/reference/right-sizing.md`.
# ═══════════════════════════════════════════════════════════════════════════

# ─── 신 임계 (전부 tier 근거 — 계층·출처 명기) ───
RS_CPU_UNDER_PCT = 70  # 계층2 큐잉 무릎(Kleinrock) + 계층3 AWS Compute Optimizer Balanced(<70%, P95)
RS_CPU_SIZING_TARGET_PCT = 70  # 계층3 AWS Balanced — 증설·다운사이즈 공통 이용률 목표(비대칭 없음)
RS_CPU_SAT_HEADROOM = 0.7  # 여유 기준 — 증설 시 실행큐/코어를 포화선의 0.7배 아래로
RS_CPU_PERCORE_HOLD_PCT = 85  # 여유 기준 — 어느 코어든 p95 >= 85%면 다운사이즈/유휴 보류(단일스레드 보호)
RS_CPU_STEAL_BIAS_PCT = 5  # 여유 기준 — steal p95 >= 5%면 하이퍼바이저 경합으로 util/sat 오염(충실도 편향 단서)
RS_MEM_UNDER_PCT = 90  # 계층3 Azure Advisor(CPU·메모리 >= SKU 90% 시 resize)
RS_MEM_SIZING_TARGET_PCT = 80  # near-peak 위 20% headroom 착지 — Gate0 목표%(통계=near-peak, 비탄력 피크)
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
# device 사용률(io_time 벽시계 busy 비율) 하한 — 이 미만 버킷의 await 는 병목 신호에서 제외. tick 기반 결합 await 는
# writeback 큐 잔류로 유휴 device(사용률 10%)에서도 1000ms+ 폭증하나, 90% 유휴면 병목 불가 -> util AND await 요구.
RS_DISKIO_UTIL_MIN = 0.5
RS_NET_RETRANS_PCT = 1.0  # 계층3 monitoring(재전송 >1% 성능 영향)
RS_NET_DROP_PCT = 0.5  # 계층3 monitoring(드롭 <0.5% 비즈니스 앱)
# 품질 판정 최소 트래픽 — 저트래픽 호스트에선 소수 부팅기 재전송/드롭이 비율을 지배(분모 붕괴). 이 미만이면
# congested 판정 보류(quality_ok) — 사이징 아닌 운영 경보라 오탐 비용이 크다. retrans 는 host-global TCP 재전송을
# 물리-iface tx_packets 로 근사하는 스코프 한계도 있어(agent OutSegs 미발행), 저트래픽에서 특히 불신.
RS_NET_MIN_TRAFFIC_KBPS = 10.0
# 계층3 monitoring — nf_conntrack count/max >= 80% = 연결테이블 고갈 임박(신규 연결 드롭 위험).
RS_CONNTRACK_SATURATION_RATIO = 0.8
RS_CONFIDENCE_MIN_HOURS = 30  # 계층3 AWS insufficient-data(14일 창 누적 30h) — 미만이면 통계 정밀도 하향(절대 바닥)
# fill-rate 외삽 절대 하한(신뢰도 바닥과 동일 30h) — 이 span 미만이면 어떤 외삽(365일·30일 근시)도 신뢰 안 함.
# 프로비저닝 초기 1회성 채움을 성장추세로 오외삽하는 것 차단. 미만이면 runway 미산출 -> 정적 가드(used% 85%)만.
RS_DISK_RATE_MIN_SPAN_DAYS = RS_CONFIDENCE_MIN_HOURS / 24
# 여유 기준 — 다운사이즈는 위험 방향이라 창이 충분히 관측됐을 때만 처방. 절대 시간 대신 창 대비 관측 비율로
# (관측/기대 버킷 >= 0.7) — 미세 갭 흡수 + WINDOW_DAYS 바뀌어도 문턱 불변. 0.7 = 창 30% 갭까지 허용.
RS_DOWNSIZE_MIN_SUFFICIENCY = 0.7
RS_BURST_RATIO_MAX = 2.0  # 여유 기준 — p95/median > 2 면 버스티(통계 정밀도 하향)
# 여유 기준 — 이용률 최소제곱 기울기 %/day 가 이 이상이면 유의한 상승(다운사이즈 정상성 게이트).
# Theil-Sen(강건) 대신 regr_slope(최소제곱) 산출을 임계로 이진화 — 다운사이즈 억제 방향이라 보수적 소값.
RS_UTIL_TREND_RISING_PCT_PER_DAY = 0.2

# OS별 CPU 포화선 (실행큐/코어) — 포화 판정 상수 단일 재사용(사이징 목표코어 산출도 동일 임계, 드리프트 방지).
_RS_CPU_SAT_LINE = {"windows": CPU_RUN_QUEUE_PER_CORE_SATURATION}
_RS_CPU_SAT_LINE_DEFAULT = PROCS_RUNNING_PER_CORE_SATURATION  # Linux/unknown


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
    sizing_floor: int | None = None  # 정확 목표 불가(포화 주도) 시 안전 상향 하한 — API recommended never-null
    confidence: ConfidenceNote = field(default_factory=ConfidenceNote)
    detail: str = ""


HostStatus = Literal["under", "idle", "over", "optimal", "insufficient"]


@dataclass
class HostAssessment:
    """호스트 종합 — 자원별 판정 dict + 근본원인 root(진단 근거) + 호스트 요약 상태."""

    resources: dict[str, ResourceAssessment]
    root_cause: str | None = None  # 원인 자원 kind
    # root 의 증상으로 추정되는 kind — 근본원인 표시(root_cause_display)용 라벨일 뿐, 처방 억제에는 안 쓴다(ADR 0055).
    symptom_of_root: list[str] = field(default_factory=list)
    host_status: HostStatus = "optimal"  # 정렬·배지용 호스트 요약 (조치는 root_cause·자원별에서)
    network_congested: bool = False  # 네트워크 품질 경고 (사이징 아님, 별도 플래그)
    # 창 대비 관측 비율 — 신뢰도 '창 대비 관측 부족' 노트 입력(30h 절대바닥과 별개 축)
    sample_sufficiency: float | None = None


def _precision_low(stats: ResourceStats) -> bool:
    """통계 정밀도 하향? — 이력 30h 미만 or 버스티(p95/median > 2)."""
    if stats.history_hours is not None and stats.history_hours < RS_CONFIDENCE_MIN_HOURS:
        return True
    if stats.cpu_burst_ratio is not None and stats.cpu_burst_ratio > RS_BURST_RATIO_MAX:
        return True
    return False


def _base_confidence(stats: ResourceStats, *, biased: bool = False, util_bearing: bool = False) -> ConfidenceNote:
    """자원 공통 신뢰도 뼈대 — 통계 정밀도·정상성·충실도. 커버리지는 자원별로 set.

    util_bearing = 이용률 추세(util_trend_rising)를 nonstationary 로 stamp 할지. cpu/memory 만 True —
    이용률 개념 없는 disk_capacity/disk_io/network 에 이용률 추세를 오귀속하지 않게(추세 신뢰도 축 kind-aware).
    """
    return ConfidenceNote(
        low_precision=_precision_low(stats),
        nonstationary=bool(stats.util_trend_rising) if util_bearing else False,
        biased=biased,
    )


def _run_queue_value(stats: ResourceStats) -> float | None:
    """사이징용 실행큐 값 — Linux procs_running p95 / Windows Processor Queue Length (포화 제약 사이징)."""
    return stats.cpu_run_queue_p95 if stats.os_family == "windows" else stats.procs_running_p95


def _cpu_target_cores(
    util_pct: float, cores: int, run_queue: float | None, os_family: str | None, saturated: bool = False
) -> int:
    """AWS Balanced 사이징 — 이용률 70% 목표 + 포화 headroom 목표의 큰 쪽. 증설·다운사이즈 공통.

    포화 headroom 은 실제 포화(cpu_saturated dual-gate 통과 = util 도 높음) 시에만 반영 — 저활동 노이즈성
    run_queue 로 코어를 부풀리지 않게 (idle CPU 오증설 방지).
    """
    util_cores = math.ceil(util_pct * cores / RS_CPU_SIZING_TARGET_PCT) if util_pct > 0 else 1
    sat_cores = 0
    if saturated and run_queue and run_queue > 0:
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
    conf = _base_confidence(stats, biased=steal_biased, util_bearing=True)  # steal 높으면 util/sat 오염(충실도 편향)
    if sat is None:
        conf.coverage_gap = True
    if util is None:
        conf.coverage_gap = True  # 이용률 미측정 — CPU 분류 결정적 입력 결손(assess_memory 대칭, Windows 블라인드 노출)
    if util is None and not sat:
        return ResourceAssessment("cpu", "unmeasured", confidence=conf, detail="이용률 미측정")
    if cores is None or cores <= 0:
        return ResourceAssessment("cpu", "unmeasured", confidence=conf, detail="코어 수 미상")
    target = _cpu_target_cores(util or 0.0, cores, _run_queue_value(stats), stats.os_family, saturated=bool(sat))
    triggers: list[str] = []
    if util is not None and util >= RS_CPU_UNDER_PCT:
        triggers.append("cpu_util")
    if sat:
        triggers.append("cpu_saturation")
    percore_busy = stats.cpu_percore_p95_max is not None and stats.cpu_percore_p95_max >= RS_CPU_PERCORE_HOLD_PCT
    if triggers or target > cores:
        # under 증설 목표는 현재 코어 초과여야 유효 — 포화 주도로 util 목표가 현재 이하면 정확 수치 불가.
        # 이 경우 안전 하한(floor) = 현재+1코어 로 채워 API recommended 가 null 이 되지 않게(계약 never-null).
        up = target if target > cores else None
        floor = None if up is not None else cores + 1
        return ResourceAssessment(
            "cpu", "under", triggers=triggers, sizing_target=up, sizing_floor=floor, confidence=conf,
            detail=(f"목표 {up}코어" if up else f"포화 주도 — 증설(최소 {cores + 1}코어)"),
        )
    if target < cores and not percore_busy:
        return ResourceAssessment("cpu", "over", sizing_target=target, confidence=conf, detail=f"목표 {target}코어")
    return ResourceAssessment("cpu", "optimal", sizing_target=cores, confidence=conf)


def _mem_target_mb(near_peak_pct: float, total_mb: int | None) -> int | None:
    """메모리 사이징 — near-peak(관측 피크)를 80%에 착지(Gate0 목표%). total * near_peak / 80.

    메모리는 비탄력(초과=OOM)이라 피크 대표 통계(near-peak)로 사이징 — 판정용 p95 와 별도(fit-for-purpose).
    """
    if total_mb is None or near_peak_pct <= 0:
        return None
    return math.ceil(total_mb * near_peak_pct / RS_MEM_SIZING_TARGET_PCT)


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
    """메모리 판정 — 이용률 90%(주신호) OR swap page-out 발생. 사이징은 near-peak 80% 착지(비탄력).

    swapless(다수)는 이용률이 주신호, swap 호스트는 page-out 발생이 포화. 물리 무릎 없어 임계는 advisor prior.
    """
    util = stats.mem_p95_pct
    conf = _base_confidence(stats, util_bearing=True)
    if util is None:
        # 이용률 미측정(구커널 MemAvailable 부재). OOM 은 강한 사후 증거라 그대로 under. 페이징은 이용률 확증 없이
        # major page-in 발생만으론 mmap/프로세스 시작의 정상 하드폴트·swappiness 와 구분 불가(노이즈)라 이용률
        # 없는 상태에선 under 신호로 미채택 — 오탐(과대 증설) 방지. 이용률 배선되면 dual-gate 로 정상 판정.
        if stats.oom_occurred:
            floor = (
                math.ceil(stats.mem_total_mb * (1 + RS_MEM_SATURATION_HEADROOM_PCT / 100))
                if stats.mem_total_mb is not None
                else None
            )
            return ResourceAssessment(
                "memory", "under", triggers=["mem_oom"], sizing_floor=floor, confidence=conf,
                detail="이용률 미측정, OOM 발생",
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
    # 사이징 통계는 near-peak(비탄력 피크 대표). 미측정 시 p95 로 폴백(판정 통계 재사용).
    near_peak = stats.mem_near_peak_pct if stats.mem_near_peak_pct is not None else util
    target_mb = _mem_target_mb(near_peak, stats.mem_total_mb)
    if triggers:
        # under 증설 목표 — near-peak 기반 + 포화 headroom(현재+30%) 중 큰 값으로 현재 초과 보장(_mem_under_target).
        up = _mem_under_target(target_mb, stats)
        # 정확 목표 불가 시 안전 하한(현재+30%) — API recommended never-null(floor).
        floor = None
        if up is None and stats.mem_total_mb is not None:
            floor = math.ceil(stats.mem_total_mb * (1 + RS_MEM_SATURATION_HEADROOM_PCT / 100))
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
    """디스크 용량 판정 — 소진 runway 30일 미만이면 filling. 추세 못 내면 정적 가드 85% fallback.

    확장 목표(sizing_target, GB)는 세-경로(report_aggregate 산출): 추세 신뢰(span 충분)면 1년 수명 목표 /
    소진 임박·짧은 span 이면 30일 예상 used 를 headroom 착지(근시라 현실적) / 추세 없이 임계 초과면 이용률 headroom.
    짧은 span 을 365일로 과외삽한 비현실적 값 방지 + 소진 임박이면 항상 구체적 목표 제공.
    """
    conf = _base_confidence(stats)
    runway = _min_runway(stats.disk_capacity_runway_days, stats.disk_inode_runway_days)
    used = stats.disk_used_pct
    inode_used = stats.disk_inode_used_pct
    # 목표 용량(GB)은 양의 정수로 정규화 — report_aggregate CEIL 산출이나 0/음수/float 방어(sizing_target int 계약).
    tgt = stats.disk_capacity_target_gb
    tgt = int(math.ceil(tgt)) if tgt is not None and tgt >= 1 else None
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


_GIB = 1024**3


@dataclass
class MountSizing:
    """마운트 하나의 용량 사이징 — 디스크 축소 금지(increase/keep). /api/assessment per-mount 디스크 축 입력.

    current/recommended 모두 같은 fs 총용량 기준(basis mismatch 없음). GiB(2^30) ceil(하향 오차 방지).
    """

    current_gib: int
    recommended_gib: int
    action: str  # "increase" | "keep" (디스크는 축소 없음)
    estimate_quality: str  # "exact" | "floor"
    note: str = ""  # 크기로 안 풀리는 신호(inode 소진 등) advisory -> API disk 축 note 로 노출


def assess_mount_capacity(
    total_bytes: int | None,
    target_bytes: float | None,
    byte_runway_days: float | None,
    used_pct: float | None,
    inode_runway_days: float | None,
    inode_used_pct: float | None,
) -> MountSizing | None:
    """마운트 용량 사이징 (per-mount) — 소진 임박이면 목표 크기로 확장, 아니면 유지. 축소 없음.

    assess_disk_capacity(호스트 worst-mount)의 per-mount 판(같은 filling 임계·target 산식). byte 소진 임박 ->
    target_bytes 로 확장(산출 불가면 floor). inode 소진은 용량 확장으로 안 풀려(mkfs 고정) keep + advisory note.
    total_bytes 미상이면 None(사이징 불가 — 축 생략).
    """
    if not total_bytes:
        return None
    current_gib = math.ceil(total_bytes / _GIB)
    byte_filling = (byte_runway_days is not None and byte_runway_days < RS_DISK_RUNWAY_DAYS) or (
        byte_runway_days is None and used_pct is not None and used_pct >= RS_DISK_STATIC_GUARD_PCT
    )
    inode_filling = (inode_runway_days is not None and inode_runway_days < RS_DISK_RUNWAY_DAYS) or (
        inode_runway_days is None and inode_used_pct is not None and inode_used_pct >= RS_DISK_STATIC_GUARD_PCT
    )
    if byte_filling and target_bytes is not None:
        rec_gib = max(current_gib, math.ceil(target_bytes / _GIB))
        action = "increase" if rec_gib > current_gib else "keep"
        return MountSizing(current_gib, rec_gib, action, "exact")
    if byte_filling:
        # 소진 임박인데 목표 산출 불가 — 안전 하한(현재를 headroom 목표%로 상향).
        floor_gib = max(current_gib, math.ceil(current_gib / (RS_DISK_HEADROOM_TARGET_PCT / 100)))
        return MountSizing(current_gib, floor_gib, "increase", "floor")
    if inode_filling:
        return MountSizing(
            current_gib, current_gib, "keep", "exact",
            note="inode 소진 — 파일 정리/재포맷(용량 확장 무관)",
        )
    return MountSizing(current_gib, current_gib, "keep", "exact")


def assess_disk_io(stats: ResourceStats) -> ResourceAssessment:
    """디스크 I/O 판정 — await p95 > 20ms면 io_bound(표시만, 사이징 불가). virtio 간섭이라 충실도 편향."""
    conf = _base_confidence(stats, biased=True)  # virtio 게스트 지연 = 하이퍼바이저·이웃 간섭 편향
    sat = disk_io_saturated(stats)  # 단일 진실 — await 우선(양 OS), Windows await 미배선 시 큐 폴백
    if sat is None:
        # await/큐 미산출 — device I/O 활동이 관측되면(iops baseline) 저활동=병목 아님(io_ok). await 는 device 가
        # 바쁜(사용률 >= util_min) 버킷만 산출하므로, 저활동 device 는 await None 이나 병목이 아니라 io_ok 다.
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
    """네트워크 판정 — 사이징 축 아님. 재전송 >1% or 드롭 >0.5%면 congested(품질). errors 는 virtio 0 이라 미사용."""
    conf = _base_confidence(stats)
    retrans = stats.net_retrans_pct
    drop = stats.net_drop_pct
    conntrack = stats.conntrack_ratio
    # 저트래픽 게이트 — 트래픽이 무시할 수준이면 retrans/drop 비율이 부팅기 소수 이벤트에 지배돼 신뢰 불가.
    # conntrack(연결테이블 고갈)은 트래픽량과 무관한 절대 신호라 게이트 제외.
    low_traffic = stats.net_avg_kbytes_per_s is not None and stats.net_avg_kbytes_per_s < RS_NET_MIN_TRAFFIC_KBPS
    triggers: list[str] = []
    if not low_traffic and retrans is not None and retrans > RS_NET_RETRANS_PCT:
        triggers.append("net_retrans")
    if not low_traffic and drop is not None and drop > RS_NET_DROP_PCT:
        triggers.append("net_drop")
    if conntrack is not None and conntrack >= RS_CONNTRACK_SATURATION_RATIO:
        triggers.append("net_conntrack")  # 연결테이블 고갈 임박 — 신규 연결 드롭 위험(NAT·프록시·방화벽)
    if triggers:
        parts = []
        if retrans is not None:
            parts.append(f"재전송 {retrans:.1f}%")
        if drop is not None:
            parts.append(f"드롭 {drop:.2f}%")
        if conntrack is not None and "net_conntrack" in triggers:
            parts.append(f"conntrack {conntrack * 100:.0f}%")
        return ResourceAssessment("network", "congested", triggers=triggers, confidence=conf, detail=" ".join(parts))
    if retrans is None and drop is None and conntrack is None:
        conf.coverage_gap = True
        return ResourceAssessment("network", "unmeasured", confidence=conf, detail="품질 신호 미측정")
    return ResourceAssessment("network", "quality_ok", confidence=conf)


# 처방 대상 상태 (부족·병목·소진임박) — 호스트 under/over 축. 네트워크 congested 는 제외:
# 원칙상 네트워크는 사이징(under/over) 축이 아니라 품질 신호라, 호스트를 "자원 부족"으로 분류하지 않고
# HostAssessment.network_congested 플래그로만 orthogonal 노출 (별도 "네트워크 혼잡" 경고). ADR 0052 정합.
# 호스트 under_provisioned/root_cause 는 사이징 가능 축(cpu under·memory under·disk_capacity filling)만 결정.
# io_bound(disk_io)는 크기로 안 풀리는 advisory(tier hint) — network congested 와 동일한 직교 플래그라 여기서 제외
# (사이징 처방 0인 축이 top-line 분류를 뒤집는 자기모순 방지). disk_io 는 아래 인과 로직에서 근본원인 라벨(진단
# 근거)에만 참조 — ADR 0055 이후 처방(actions/advisory) 자체를 억제하는 데는 쓰이지 않는다.
_ROOTABLE_UNDER = ("under", "filling")


def _host_status(stats: ResourceStats, res: dict[str, ResourceAssessment], under_kinds: set[str]) -> HostStatus:
    """호스트 요약 상태 — under(압박) > idle(미사용) > over > optimal > insufficient.

    조치는 root_cause·자원별 판정에서 나오고 이건 정렬·배지용 파생.
    유휴는 활동(activity) 3축 — CPU p95 · 네트워크 · 디스크 I/O — 이 전부 quiescent 할 때만(미사용 VM).
    메모리 사용률·디스크 용량은 활동 아닌 할당이라 유휴 신호 제외.
    """
    if under_kinds:
        return "under"
    # 사이징 2축(cpu·memory)이 둘 다 미측정이면 판정 불가 — disk/network 부분 데이터가 있어도 optimal 로 위장 금지
    # (명세: insufficient_data = cpu·mem 둘 다 부재). idle/over 검사보다 앞(cpu·mem 없이 idle/over 판정 불가).
    if res["cpu"].status in ("unmeasured", "insufficient") and res["memory"].status in ("unmeasured", "insufficient"):
        return "insufficient"
    cpu = stats.cpu_p95_pct
    net = stats.net_avg_kbytes_per_s  # kB/s
    net_mbps = net * 8 / 1000 if net is not None else None  # kB/s -> Mbit/s (*8 bit, /1000 mega)
    # 디스크 I/O 활동 — baseline IOPS 가 측정됐고 quiescent 초과면 미사용 아님(유휴 배제). 미측정(None)은 배제 안 함.
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

    인과: 메모리 -> 디스크 I/O -> CPU. 판별: swap 발생 / procs_blocked(D-state) / await. iowait 미사용.
    root_cause·symptom_of_root 는 진단 근거(왜 부족한가)로만 쓴다 — 처방(prescribed_under_kinds 등)은
    ADR 0055 부터 자원별 독립이라 symptom_of_root 로 억제되지 않는다(근본원인 추정이 틀려도, 즉 원인 자원만
    고쳤을 때 하류가 실제로 해소된다는 보장이 없어도 관측된 부족을 누락하지 않는 것이 안전 우선 — assessment
    API 사이징 정책과 통일). 결합 신호 없이 각자 부족이면 각자(root=최상류 부족 자원, 나열 순서만 결정).
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
    procs_blocked_high = (
        stats.procs_blocked_p95 is not None and stats.procs_blocked_p95 >= PROCS_BLOCKED_DSTATE_SATURATION
    )
    if mem_pressure and _mem_paging_active(stats) and (disk_io_pressure or cpu_pressure):
        # 메모리발 -> 동반 디스크 I/O·CPU 는 swap 트래픽·대기의 증상(symptom_of_root 라벨용, 처방 억제는 안 함).
        host.root_cause = "memory"
        host.symptom_of_root = [
            k for k in ("disk_io", "cpu") if k in under_kinds or (k == "disk_io" and disk_io_pressure)
        ]
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
    host.sample_sufficiency = stats.sample_sufficiency  # 창 대비 관측 비율 전달(신뢰도 노트용)
    host.host_status = _host_status(stats, res, under_kinds)
    return host


def host_saturation_unmeasured(host: HostAssessment) -> bool:
    """호스트 포화 축(cpu·memory·disk_io) 중 하나라도 미관측인지 — is_partial·confidence '포화 수치 미관측' 단일 진실.

    disk_capacity(용량 누적)·network(품질) 미측정은 포화 축이 아니라 제외 (saturation 축 한정).
    Windows perflib 미발행·구세대 viostor await 미측정 등이 여기로 노출된다.
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
    """자원 1개 처방 — 사이징 목표 있으면 "메모리: 22GB"·"스토리지: 500GB"(총량 목표), 없으면 기본 문구."""
    if kind == "disk_capacity" and ra.sizing_target is not None:
        return f"스토리지: {ra.sizing_target:.0f}GB"  # 1년 수명 목표 총 용량
    if kind in _SIZEABLE_LABEL and ra.sizing_target is not None:
        return f"{_SIZEABLE_LABEL[kind]}: {_fmt_sizing_target(kind, ra.sizing_target)}"
    return _UNDER_ACTION_BASE.get(kind, "")


def _under_kinds(host: HostAssessment) -> list[str]:
    return [k for k in _UNDER_ORDER if k in host.resources and host.resources[k].status in _ROOTABLE_UNDER]


def prescribed_under_kinds(host: HostAssessment) -> list[str]:
    """처방 대상 under 자원 kind (공개, 단일 진실) — 관측된 under 자원 전부, 인과에 의한 억제 없음(ADR 0055).

    근본원인(root_cause/symptom_of_root)은 진단 근거일 뿐 처방 필터가 아니다 — 원인 자원만 고쳐도 하류가
    실제로 해소된다는 보장이 없어(추정이 틀릴 수 있음), 관측된 부족을 누락하지 않는 쪽이 안전하다(assessment
    API sizing.axes 와 동일 정책 — 소비처 3곳: under_prescription 문구·right-sizing API actions·assessment
    API sizing.axes 가 전부 자원별 독립 판정을 공유해 drift 0).
    """
    return _under_kinds(host)


def resource_prescription(kind: str, ra: ResourceAssessment) -> str:
    """자원 1개 처방 문구 (공개) — under_prescription·API actions 공유. 사이징 목표 있으면 "메모리: 22GB"."""
    return _resource_prescription(kind, ra)


def under_prescription(host: HostAssessment) -> str:
    """자원 부족 처방 — 관측된 under 자원 전부를 " | " 결합(prescribed_under_kinds, 억제 없음). 근본원인은
    root_cause_display(별도 칼럼)가 "왜"를 전달 — 본 문구는 "무엇을"만 나열한다."""
    return " | ".join(resource_prescription(k, host.resources[k]) for k in prescribed_under_kinds(host))


def root_cause_display(host: HostAssessment) -> str:
    """근본원인 칼럼 표시 — under_prescription("무엇을")의 "왜"를 전달하는 진단 근거(처방 자체엔 무영향):

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


# ─── 표시 라벨 (한국어, mapper/템플릿 소비) ───
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
