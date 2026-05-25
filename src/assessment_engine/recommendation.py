"""Right-sizing 분류 — USE Method (Brendan Gregg) + 공식 cloud advisor 임계값.

분류 enum: idle / shutdown / over_provisioned / under_provisioned / optimal.

UI badge 임계값(`mappers._USAGE_DANGER_PCT`/`_USAGE_WARN_PCT`)과는 별 도메인:
- mapper 90/75 = 시점 사용량 시각 신호 (위험·주의·정상)
- 본 모듈 = 14일 통계 기반 right-sizing 결정 (idle/over/under 등)
"""

from dataclasses import dataclass
from typing import Literal

# ─── 임계값 ─────────────

# 관찰 윈도우 — AWS Compute Optimizer 기본값
WINDOW_DAYS = 14

# Idle 판정 — AWS Compute Optimizer
IDLE_CPU_PEAK_PCT = 1
IDLE_NET_KBPS = 1
IDLE_DURATION_DAYS = 14

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
IOWAIT_UPSIZE_PCT = 20  # iowait_p95 >= 20% — disk IO saturation
DISK_CAPACITY_UPSIZE_PCT = 85  # worst mount used_pct >= 85% — storage capacity utilization


Recommendation = Literal[
    "idle",
    "shutdown",
    "over_provisioned",
    "under_provisioned",
    "optimal",
    "insufficient_data",  # 14일 미만 또는 metric 부재
]


@dataclass
class ResourceStats:
    """USE Method 통계 입력 — Utilization·Saturation 정석 6 자원축.

    None 은 데이터 부재 (해당 축 평가 skip, fall-through).
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


def classify(stats: ResourceStats) -> Recommendation:
    """USE Method 정석 분류 — Utilization + Saturation 두 축 평가.

    판정 순서: Idle → Shutdown → Swap → Disk capacity → Disk IO → CPU saturation →
              CPU util → Mem util → Over → Optimal.
    필수 데이터(cpu_p95·mem_p95) 부재 시 `insufficient_data` 반환.
    """
    if stats.cpu_p95_pct is None or stats.mem_p95_pct is None:
        return "insufficient_data"

    # Idle / Shutdown — net 의존. net_avg_kbps None이면 skip (다음 단계로 fall-through)
    if stats.net_avg_kbps is not None:
        if (
            stats.cpu_peak_pct is not None
            and stats.cpu_peak_pct <= IDLE_CPU_PEAK_PCT
            and stats.net_avg_kbps <= IDLE_NET_KBPS
        ):
            return "idle"
        # net_avg_kbps(KB/s) → Mbps 변환: x 8 / 1000
        if stats.cpu_p95_pct <= SHUTDOWN_CPU_P95_PCT and (stats.net_avg_kbps * 8 / 1000) <= SHUTDOWN_NET_MBPS:
            return "shutdown"

    # Swap 사용 = 메모리 saturation → 업사이즈 short-circuit
    if stats.swap_used:
        return "under_provisioned"

    # Disk capacity (storage utilization) >= 85% → 업사이즈 (Storage 부족)
    if stats.disk_used_pct is not None and stats.disk_used_pct >= DISK_CAPACITY_UPSIZE_PCT:
        return "under_provisioned"

    # Disk IO saturation (iowait >= 20%) → 업사이즈 (Disk IO 병목)
    if stats.iowait_p95_pct is not None and stats.iowait_p95_pct >= IOWAIT_UPSIZE_PCT:
        return "under_provisioned"

    # CPU saturation — run queue >= core 수 (load_15m / cpu_cores >= 1.0)
    if (
        stats.cpu_load_15m_max is not None
        and stats.cpu_cores is not None
        and stats.cpu_cores > 0
        and (stats.cpu_load_15m_max / stats.cpu_cores) >= CPU_SATURATION_LOAD_RATIO
    ):
        return "under_provisioned"

    if stats.cpu_p95_pct <= CPU_DOWNSIZE_P95_PCT and stats.mem_p95_pct <= MEM_DOWNSIZE_P95_PCT:
        return "over_provisioned"

    if stats.cpu_p95_pct >= CPU_UPSIZE_P95_PCT or stats.mem_p95_pct >= MEM_UPSIZE_P95_PCT:
        return "under_provisioned"

    return "optimal"


# ─── UI 라벨 (한국어, 양식 A 사용자 친화 표시용) ──────────────────────────

LABEL_KO: dict[str, str] = {
    "idle": "유휴",
    "shutdown": "종료 권장",
    "over_provisioned": "과다 프로비저닝",
    "under_provisioned": "리소스 부족",
    "optimal": "정상",
    "insufficient_data": "데이터 부족",
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
