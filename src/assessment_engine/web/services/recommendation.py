"""Right-sizing 분류 — USE Method (Brendan Gregg) + 공식 cloud advisor 임계값.

전체 근거: docs/ai_roadmap.md §3.B (USE Method) + §3.C (룰 기반 추천 엔진).
deliverables.md §5 "RISK 분류" 표 참조.

UI badge 임계값(`mappers._USAGE_DANGER_PCT`/`_USAGE_WARN_PCT`)과는 별 도메인:
- mapper 90/75 = 시점 사용량 시각 신호 (위험·주의·정상)
- 본 모듈 = 14일 통계 기반 right-sizing 결정 (idle/over/under 등)
"""
from dataclasses import dataclass
from typing import Literal


# ─── 임계값 (모두 ai_roadmap.md §3.C 출처. 변경 시 양쪽 동기화) ─────────────

# 관찰 윈도우 — AWS Compute Optimizer 기본값
WINDOW_DAYS = 14

# Idle 판정 — AWS Compute Optimizer
IDLE_CPU_PEAK_PCT  = 1
IDLE_NET_KBPS      = 1
IDLE_DURATION_DAYS = 14

# Shutdown 권장 — Azure Advisor
SHUTDOWN_CPU_P95_PCT = 3
SHUTDOWN_NET_MBPS    = 2

# Over-provisioned (다운사이즈) — AWS Compute Optimizer + GCP Recommender (headroom 30%)
CPU_DOWNSIZE_P95_PCT = 30
MEM_DOWNSIZE_P95_PCT = 50
HEADROOM_PCT         = 30

# Under-provisioned (업사이즈)
CPU_UPSIZE_P95_PCT = 70  # Kleinrock — Queueing Systems (1975), Google SRE Book
MEM_UPSIZE_P95_PCT = 80  # Linux page cache 압박 시작점


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
    """USE Method 통계 입력. None은 데이터 부재."""
    cpu_p95_pct: float | None
    cpu_peak_pct: float | None
    mem_p95_pct: float | None
    swap_used: bool
    net_avg_kbps: float | None  # 1차 MVP에서 None — net 집계 미구현 시 idle/shutdown 판정 skip


def classify(stats: ResourceStats) -> Recommendation:
    """판정 순서: Idle → Shutdown → Swap → Over → Under → Optimal.

    필수 데이터(cpu_p95·mem_p95) 부재 시 `insufficient_data` 반환 — UI에서 "—" 표시.
    """
    if stats.cpu_p95_pct is None or stats.mem_p95_pct is None:
        return "insufficient_data"

    # Idle / Shutdown — net 의존. net_avg_kbps None이면 skip (다음 단계로 fall-through)
    if stats.net_avg_kbps is not None:
        if stats.cpu_peak_pct is not None \
           and stats.cpu_peak_pct <= IDLE_CPU_PEAK_PCT \
           and stats.net_avg_kbps <= IDLE_NET_KBPS:
            return "idle"
        # net_avg_kbps(KB/s) → Mbps 변환: × 8 / 1000
        if stats.cpu_p95_pct <= SHUTDOWN_CPU_P95_PCT \
           and (stats.net_avg_kbps * 8 / 1000) <= SHUTDOWN_NET_MBPS:
            return "shutdown"

    # Swap 사용 = 메모리 부족 신호 → 업사이즈로 short-circuit (ai_roadmap.md §3.C 판정 순서)
    if stats.swap_used:
        return "under_provisioned"

    if stats.cpu_p95_pct <= CPU_DOWNSIZE_P95_PCT and stats.mem_p95_pct <= MEM_DOWNSIZE_P95_PCT:
        return "over_provisioned"

    if stats.cpu_p95_pct >= CPU_UPSIZE_P95_PCT or stats.mem_p95_pct >= MEM_UPSIZE_P95_PCT:
        return "under_provisioned"

    return "optimal"


# ─── UI 라벨 (한국어, 양식 A 사용자 친화 표시용) ──────────────────────────

LABEL_KO: dict[str, str] = {
    "idle":               "유휴",
    "shutdown":           "종료 권장",
    "over_provisioned":   "과다 프로비저닝",
    "under_provisioned":  "리소스 부족",
    "optimal":            "정상",
    "insufficient_data":  "데이터 부족",
}

# 양식 A의 RISK 컬러 매핑 (high/mid/low/normal — UI badge 색상)
BADGE_CLASS: dict[str, str] = {
    "idle":               "badge-cat-unknown",
    "shutdown":           "badge-cat-unknown",
    "over_provisioned":   "badge-cat-cache",       # 노란 — 비용 낭비
    "under_provisioned":  "badge-cat-cache",       # 빨간 — 성능 위험 (UI 정의에 맞춰)
    "optimal":            "badge-cat-online",      # 녹색
    "insufficient_data":  "",
}
