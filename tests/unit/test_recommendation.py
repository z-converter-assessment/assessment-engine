"""recommendation.classify 판정 순서 검증 — USE Method 정공 + 임계 catalog 보증.

10 분류 + short-circuit 흐름 (위 우선순위 1 ~ 10) 모두 case 화. 임계값 변경 시 본 test 회귀 가시화.
참고자료: docs/architecture/web/static-assets.md "분류 기준 / 판정 순서" 절 (right_sizing_thresholds.html).
"""

import pytest

from assessment_engine.recommendation import (
    CPU_DOWNSIZE_P95_PCT,
    CPU_SATURATION_LOAD_RATIO,
    CPU_UPSIZE_P95_PCT,
    DISK_CAPACITY_UPSIZE_PCT,
    IDLE_CPU_PEAK_PCT,
    IDLE_NET_KBPS,
    IOWAIT_UPSIZE_PCT,
    MEM_DOWNSIZE_P95_PCT,
    MEM_UPSIZE_P95_PCT,
    SHUTDOWN_CPU_P95_PCT,
    SHUTDOWN_NET_MBPS,
    ResourceStats,
    classify,
)


def _stats(**overrides) -> ResourceStats:
    """기본 optimal 분류 stats — 각 test 가 override 로 trigger 활성화."""
    base: dict = {
        "cpu_p95_pct": 40.0,  # under 70, above 30 (downsize 임계 위)
        "cpu_peak_pct": 50.0,
        "cpu_load_15m_max": 0.5,
        "cpu_cores": 4,
        "mem_p95_pct": 60.0,  # under 80, above 50
        "swap_used": False,
        "disk_used_pct": 50.0,
        "iowait_p95_pct": 5.0,
        "net_avg_kbps": 100.0,
    }
    base.update(overrides)
    return ResourceStats(**base)


# 우선순위 1: insufficient_data — cpu_p95 or mem_p95 None
def test_insufficient_data_cpu_p95_none():
    assert classify(_stats(cpu_p95_pct=None)) == "insufficient_data"


def test_insufficient_data_mem_p95_none():
    assert classify(_stats(mem_p95_pct=None)) == "insufficient_data"


# 우선순위 2: idle — cpu_peak <= 1% AND net <= 1 KB/s
def test_idle_cpu_peak_and_net_below_threshold():
    assert classify(_stats(cpu_peak_pct=IDLE_CPU_PEAK_PCT, net_avg_kbps=IDLE_NET_KBPS)) == "idle"


def test_idle_skipped_when_net_unknown():
    """net_avg_kbps None 이면 idle 판정 skip (fall-through)."""
    # cpu_peak=1, net=None → idle 안 됨. 다른 trigger 없으면 optimal.
    assert classify(_stats(cpu_peak_pct=IDLE_CPU_PEAK_PCT, net_avg_kbps=None)) == "optimal"


# 우선순위 3: shutdown — cpu_p95 <= 3% AND net <= 2 Mbps (= 250 KB/s)
def test_shutdown_cpu_p95_and_net_below_threshold():
    # net_avg_kbps(KB/s) * 8 / 1000 <= 2 Mbps → 250 KB/s 이하
    assert classify(_stats(cpu_p95_pct=SHUTDOWN_CPU_P95_PCT, cpu_peak_pct=2.0, net_avg_kbps=200.0)) == "shutdown"


def test_shutdown_not_triggered_when_net_above_2mbps():
    # 250 KB/s 이상 (= 2 Mbps 초과) → shutdown 아님. cpu_p95=3 + cpu_peak=2 + net=300 KB/s.
    # 다른 trigger 없으니 optimal (cpu 30 미만 + mem 50 미만 over_provisioned 가능).
    rec = classify(_stats(cpu_p95_pct=SHUTDOWN_CPU_P95_PCT, cpu_peak_pct=2.0, mem_p95_pct=40.0, net_avg_kbps=300.0))
    assert rec == "over_provisioned"


# 우선순위 4: under — swap_used short-circuit
def test_under_swap_used_short_circuit():
    """swap_used = True 면 cpu/mem 무관 즉시 under_provisioned."""
    assert classify(_stats(cpu_p95_pct=20.0, mem_p95_pct=30.0, swap_used=True)) == "under_provisioned"


# 우선순위 5: under — disk capacity >= 85%
def test_under_disk_capacity_above_threshold():
    assert classify(_stats(disk_used_pct=DISK_CAPACITY_UPSIZE_PCT)) == "under_provisioned"


def test_under_disk_capacity_just_below_threshold():
    rec = classify(_stats(disk_used_pct=DISK_CAPACITY_UPSIZE_PCT - 0.1))
    # 84.9% — disk 아래, 다른 trigger 없으면 optimal
    assert rec == "optimal"


# 우선순위 6: under — iowait_p95 >= 20%
def test_under_iowait_above_threshold():
    assert classify(_stats(iowait_p95_pct=IOWAIT_UPSIZE_PCT)) == "under_provisioned"


# 우선순위 7: under — CPU saturation (load_15m / cpu_cores >= 1.0)
def test_under_cpu_saturation():
    # 4 cores, load_15m = 4.0 → ratio 1.0
    assert classify(_stats(cpu_cores=4, cpu_load_15m_max=4.0 * CPU_SATURATION_LOAD_RATIO)) == "under_provisioned"


def test_no_saturation_when_cores_zero_or_none():
    """cpu_cores = 0 또는 None 이면 saturation 판정 skip (div-by-zero 회피)."""
    assert classify(_stats(cpu_cores=None, cpu_load_15m_max=100.0)) == "optimal"
    assert classify(_stats(cpu_cores=0, cpu_load_15m_max=100.0)) == "optimal"


# 우선순위 8: over — cpu_p95 <= 30% AND mem_p95 <= 50%
def test_over_provisioned_both_low():
    assert classify(_stats(cpu_p95_pct=CPU_DOWNSIZE_P95_PCT, mem_p95_pct=MEM_DOWNSIZE_P95_PCT)) == "over_provisioned"


def test_over_not_triggered_when_mem_above_50():
    # cpu=30 + mem=51 → over 안 됨. 다른 under trigger 없으니 optimal.
    assert classify(_stats(cpu_p95_pct=CPU_DOWNSIZE_P95_PCT, mem_p95_pct=51.0)) == "optimal"


# 우선순위 9: under — cpu_p95 >= 70% OR mem_p95 >= 80%
def test_under_cpu_high():
    assert classify(_stats(cpu_p95_pct=CPU_UPSIZE_P95_PCT)) == "under_provisioned"


def test_under_mem_high():
    assert classify(_stats(mem_p95_pct=MEM_UPSIZE_P95_PCT)) == "under_provisioned"


# 우선순위 10: optimal — 위 trigger 미해당
def test_optimal_default():
    """모든 임계 미해당 = optimal."""
    assert classify(_stats()) == "optimal"


# Short-circuit 검증 — 우선순위 위 trigger 가 아래 trigger 보다 먼저 평가
def test_swap_short_circuits_cpu_high():
    """swap_used = True + cpu_p95 >= 70% — swap (우선순위 4) 이 먼저 → under (단일 trigger 표시)."""
    rec = classify(_stats(swap_used=True, cpu_p95_pct=80.0, mem_p95_pct=90.0))
    assert rec == "under_provisioned"


def test_idle_short_circuits_over_provisioned():
    """cpu_peak=1 + net=1 (idle trigger) + cpu/mem 모두 낮음 (over trigger) — idle (우선순위 2) 우선."""
    rec = classify(
        _stats(cpu_p95_pct=1.0, cpu_peak_pct=IDLE_CPU_PEAK_PCT, mem_p95_pct=10.0, net_avg_kbps=IDLE_NET_KBPS)
    )
    assert rec == "idle"


def test_disk_capacity_short_circuits_cpu_low():
    """disk 85%+ + cpu 30 미만 + mem 50 미만 — disk (우선순위 5) 우선 → under (over 아님)."""
    rec = classify(_stats(cpu_p95_pct=20.0, mem_p95_pct=30.0, disk_used_pct=DISK_CAPACITY_UPSIZE_PCT))
    assert rec == "under_provisioned"


@pytest.mark.parametrize(
    "case",
    [
        # 임계 boundary — = 임계 면 trigger 발화 (`<=` / `>=` 포함)
        # idle: cpu_peak <= 1 AND net <= 1 — boundary 발화
        (_stats(cpu_peak_pct=IDLE_CPU_PEAK_PCT, net_avg_kbps=IDLE_NET_KBPS), "idle"),
        # shutdown: cpu_p95 <= 3 AND net*8/1000 <= 2 — boundary 발화
        (
            _stats(cpu_p95_pct=SHUTDOWN_CPU_P95_PCT, cpu_peak_pct=2.0, net_avg_kbps=SHUTDOWN_NET_MBPS * 1000 / 8),
            "shutdown",
        ),
        # disk: 85% boundary
        (_stats(disk_used_pct=DISK_CAPACITY_UPSIZE_PCT), "under_provisioned"),
        # iowait: 20% boundary
        (_stats(iowait_p95_pct=IOWAIT_UPSIZE_PCT), "under_provisioned"),
        # cpu downsize: 30%, mem 50% boundary
        (_stats(cpu_p95_pct=CPU_DOWNSIZE_P95_PCT, mem_p95_pct=MEM_DOWNSIZE_P95_PCT), "over_provisioned"),
        # cpu upsize: 70% boundary
        (_stats(cpu_p95_pct=CPU_UPSIZE_P95_PCT), "under_provisioned"),
        # mem upsize: 80% boundary
        (_stats(mem_p95_pct=MEM_UPSIZE_P95_PCT), "under_provisioned"),
    ],
)
def test_threshold_boundary_inclusive(case):
    stats, expected = case
    assert classify(stats) == expected
