"""recommendation.classify 판정 순서 검증 — USE Method 정공 + 임계 catalog 보증.

10 분류 + short-circuit 흐름 (위 우선순위 1 ~ 10) 모두 case 화. 임계값 변경 시 본 test 회귀 가시화.
참고자료: docs/architecture/web/static-assets.md "분류 기준 / 판정 순서" 절 (right_sizing_thresholds.html).
"""

import pytest

from assessment_engine.recommendation import (
    CPU_DOWNSIZE_P95_PCT,
    CPU_RUN_QUEUE_PER_CORE_SATURATION,
    CPU_UPSIZE_P95_PCT,
    DISK_CAPACITY_UPSIZE_PCT,
    DISK_QUEUE_PER_DISK_SATURATION,
    IDLE_CPU_P95_PCT,
    IDLE_NET_MBPS,
    MEM_DOWNSIZE_P95_PCT,
    MEM_UPSIZE_P95_PCT,
    PROCS_RUNNING_PER_CORE_SATURATION,
    RS_DISKIO_AWAIT_MS,
    WIN_PAGES_INPUT_SATURATION,
    ResourceStats,
    assess,
    classify,
    cpu_saturated,
    disk_io_saturated,
    is_partial_evaluation,
    mem_saturated,
)


def _stats(**overrides) -> ResourceStats:
    """기본 optimal 분류 stats — 각 test 가 override 로 trigger 활성화."""
    base: dict = {
        "cpu_p95_pct": 40.0,  # under 70, above 30 (downsize 임계 위)
        "cpu_peak_pct": 50.0,
        "cpu_load_15m_max": 0.5,
        "procs_running_p95": 0.5,  # Linux CPU 포화 신호(load 대체) — 미포화 기본
        "cpu_cores": 4,
        "mem_p95_pct": 60.0,  # under 80, above 50
        "swap_used": False,
        "disk_used_pct": 50.0,
        "iowait_p95_pct": 5.0,
        "disk_await_p95_ms": 5.0,  # await 5ms < 20 -> io_ok(측정됨)
        "net_avg_kbps": 100.0,
    }
    base.update(overrides)
    return ResourceStats(**base)


# 우선순위 1: insufficient_data — cpu_p95 AND mem_p95 둘 다 None (한 자원이라도 있으면 평가)
def test_insufficient_data_both_p95_none():
    assert classify(_stats(cpu_p95_pct=None, mem_p95_pct=None)) == "insufficient_data"


def test_partial_metric_still_classified():
    # 한쪽 utilization 만 있어도 그것으로 결론 (OR->AND 좁힘, 데이터로 반드시 판단) — insufficient 아님
    assert classify(_stats(cpu_p95_pct=None)) != "insufficient_data"
    assert classify(_stats(mem_p95_pct=None)) != "insufficient_data"


# 우선순위 2: 유휴 — cpu_p95 <= 3% AND net <= 2 Mbps (Azure 저사용, idle+shutdown 통합)
def test_idle_cpu_p95_and_net_below_threshold():
    # net_avg_kbps(KB/s) * 8 / 1000 <= 2 Mbps → 250 KB/s 이하
    assert classify(_stats(cpu_p95_pct=IDLE_CPU_P95_PCT, net_avg_kbps=200.0)) == "idle"


def test_idle_skipped_when_net_unknown():
    """net_avg_kbps None 이면 유휴 판정 skip (fall-through)."""
    assert classify(_stats(cpu_p95_pct=IDLE_CPU_P95_PCT, net_avg_kbps=None)) == "optimal"


def test_idle_not_triggered_when_net_above_2mbps():
    # 250 KB/s 이상 (= 2 Mbps 초과) → 유휴 아님. 다른 trigger 없으면 over_provisioned.
    rec = classify(_stats(cpu_p95_pct=IDLE_CPU_P95_PCT, mem_p95_pct=40.0, net_avg_kbps=300.0))
    assert rec == "over_provisioned"


# 우선순위 4: under — swap_used short-circuit
def test_under_page_out_short_circuit():
    """활성 page-out(mem_swap_paging) 이면 cpu/mem 무관 즉시 under_provisioned (ADR 0052 — 정적 swap 점유 아님)."""
    assert classify(_stats(cpu_p95_pct=20.0, mem_p95_pct=30.0, mem_swap_paging=True)) == "under_provisioned"
    # 정적 스왑 점유만(page-out 없음) 은 포화 아님 -> under 아님
    occupied = _stats(cpu_p95_pct=20.0, mem_p95_pct=30.0, swap_used=True, mem_swap_paging=False)
    assert classify(occupied) != "under_provisioned"


# 우선순위 5: under — disk capacity >= 85%
def test_under_disk_capacity_above_threshold():
    assert classify(_stats(disk_used_pct=DISK_CAPACITY_UPSIZE_PCT)) == "under_provisioned"


def test_under_disk_capacity_just_below_threshold():
    rec = classify(_stats(disk_used_pct=DISK_CAPACITY_UPSIZE_PCT - 0.1))
    # 84.9% — disk 아래, 다른 trigger 없으면 optimal
    assert rec == "optimal"


# 우선순위 6: under — await_p95 > 20ms (Linux disk IO 지연)
def test_under_await_above_threshold():
    assert classify(_stats(disk_await_p95_ms=RS_DISKIO_AWAIT_MS + 1)) == "under_provisioned"


# 우선순위 7: under — CPU saturation (procs_running / cpu_cores >= 1.0)
def test_under_cpu_saturation():
    # 4 cores, procs_running = 4.0 → ratio 1.0
    assert (
        classify(_stats(cpu_cores=4, procs_running_p95=4.0 * PROCS_RUNNING_PER_CORE_SATURATION)) == "under_provisioned"
    )


def test_no_saturation_when_cores_zero_or_none():
    """cpu_cores = 0 또는 None 이면 saturation 판정 skip (div-by-zero 회피)."""
    assert classify(_stats(cpu_cores=None, procs_running_p95=100.0)) == "optimal"
    assert classify(_stats(cpu_cores=0, procs_running_p95=100.0)) == "optimal"


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
    rec = classify(_stats(cpu_p95_pct=1.0, mem_p95_pct=10.0, net_avg_kbps=1.0))
    assert rec == "idle"


def test_disk_capacity_short_circuits_cpu_low():
    """disk 85%+ + cpu 30 미만 + mem 50 미만 — disk (우선순위 5) 우선 → under (over 아님)."""
    rec = classify(_stats(cpu_p95_pct=20.0, mem_p95_pct=30.0, disk_used_pct=DISK_CAPACITY_UPSIZE_PCT))
    assert rec == "under_provisioned"


@pytest.mark.parametrize(
    "case",
    [
        # 임계 boundary — = 임계 면 trigger 발화 (`<=` / `>=` 포함)
        # 유휴: cpu_p95 <= 3 AND net*8/1000 <= 2 — boundary 발화 (Azure)
        (
            _stats(cpu_p95_pct=IDLE_CPU_P95_PCT, net_avg_kbps=IDLE_NET_MBPS * 1000 / 8),
            "idle",
        ),
        # disk: 85% boundary
        (_stats(disk_used_pct=DISK_CAPACITY_UPSIZE_PCT), "under_provisioned"),
        # await: 20ms 초과
        (_stats(disk_await_p95_ms=RS_DISKIO_AWAIT_MS + 1), "under_provisioned"),
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


# ─── OS family 분기 (원칙 P2 — Windows pagefile != Linux swap saturation) ───


def test_mem_saturated_linux_uses_page_out_not_static_swap():
    # ADR 0052: Linux 메모리 포화 = active page-out(mem_swap_paging), 정적 스왑 점유(swap_used) 아님.
    # swappiness 로 여유 RAM 에도 유휴 페이지 스왑아웃하므로 점유는 압박 신호가 아님.
    assert mem_saturated(_stats(os_family="linux", swap_used=True, mem_swap_paging=False)) is False
    assert mem_saturated(_stats(os_family="linux", swap_used=False, mem_swap_paging=True)) is True


def test_mem_saturated_windows_excludes_pagefile_uses_hardfault_rate():
    # Windows pagefile 상시 사용은 신호 아님 → 하드폴트율(pages_input rate) 로만 판정.
    assert mem_saturated(_stats(os_family="windows", swap_used=True, mem_swap_paging=True)) is None
    assert (
        mem_saturated(
            _stats(os_family="windows", swap_used=True, mem_swap_paging=True, mem_pages_input_rate_p95=50.0)
        )
        is True
    )


def test_classify_windows_swap_not_under_provisioned():
    """동일 통계라도 Windows 는 swap 축 제외 → under_provisioned 로 왜곡 안 됨 (Linux 와 분기)."""
    # cpu 20% / mem 30% (낮음) + swap_used=True:
    #   Linux  → swap short-circuit → under_provisioned
    #   Windows→ swap 제외 → cpu/mem 낮으니 over_provisioned
    linux = _stats(cpu_p95_pct=20.0, mem_p95_pct=30.0, mem_swap_paging=True, os_family="linux")
    windows = _stats(cpu_p95_pct=20.0, mem_p95_pct=30.0, mem_swap_paging=True, os_family="windows")
    assert classify(linux) == "under_provisioned"
    assert classify(windows) == "over_provisioned"


def test_classify_windows_still_under_on_real_utilization():
    """Windows 라도 실제 utilization 신호(cpu/mem 높음)는 그대로 under_provisioned — 과소평가 안 함."""
    windows_busy = _stats(cpu_p95_pct=CPU_UPSIZE_P95_PCT, swap_used=True, os_family="windows")
    assert classify(windows_busy) == "under_provisioned"


def test_is_partial_evaluation_by_unmeasured_saturation():
    """부분 평가 = saturation 축 미관측(데이터 기반). Windows 는 load None 이라 자연히 True,
    Windows agent 가 등가 카운터를 발행하면 자동 해제 — os 단정이 아니라 실제 관측 여부."""
    assert is_partial_evaluation(_stats(procs_running_p95=None)) is True  # 실행큐(CPU saturation) 미관측
    assert is_partial_evaluation(_stats(disk_await_p95_ms=None)) is True  # await(Disk IO) 미관측
    assert is_partial_evaluation(_stats()) is False  # saturation 전부 관측(Linux 정상)


def test_assess_triggers_as_evidence():
    """assess: under 시 hit 신호를 triggers 근거로 반환 (설명 가능 — "어떤 데이터로 under")."""
    a = assess(_stats(mem_p95_pct=MEM_UPSIZE_P95_PCT))
    assert a.recommendation == "under_provisioned"
    assert "mem_util" in a.triggers


def test_assess_windows_swap_excluded_load_unmeasured():
    """Windows: swap trigger 제외(pagefile), load 미관측은 unmeasured — 분류는 완결(데이터 부족 아님)."""
    a = assess(_stats(swap_used=True, cpu_load_15m_max=None, os_family="windows"))
    assert "mem_saturation" not in a.triggers
    assert "cpu_saturation" in a.unmeasured
    assert a.is_partial is True
    assert a.recommendation != "insufficient_data"


def test_assess_under_on_partial_metric_no_miss():
    """한쪽 utilization(mem)만 있어도 위험 신호면 under — 누락 0 (네 원칙: under 반드시 평가)."""
    a = assess(_stats(cpu_p95_pct=None, mem_p95_pct=MEM_UPSIZE_P95_PCT))
    assert a.recommendation == "under_provisioned"


# ─── os-aware saturation helper (S1/S2 — Windows run queue·paging 실측) ───


def _win(**overrides) -> ResourceStats:
    """Windows 기본 stats — load/iowait/swap 는 Linux 축이라 무의미, run_queue/paging/disk_queue 로 판정."""
    base = {
        "os_family": "windows",
        "cpu_load_15m_max": None,  # loadavg OS 부재
        "iowait_p95_pct": None,  # iowait OS 부재
        "disk_await_p95_ms": None,  # Windows await 미발행 -> disk_queue 로 판정
        "swap_used": True,  # pagefile baseline (saturation 신호 아님)
    }
    base.update(overrides)
    return _stats(**base)


def test_cpu_saturated_os_aware():
    # Linux: procs_running/cores >= 1.0
    assert cpu_saturated(_stats(procs_running_p95=4.0, cpu_cores=4)) is True
    assert cpu_saturated(_stats(procs_running_p95=1.0, cpu_cores=4)) is False
    assert cpu_saturated(_stats(procs_running_p95=None)) is None  # 미관측
    # Windows: run queue/cores >= 2, load 는 무시
    assert cpu_saturated(_win(cpu_run_queue_p95=8.0, cpu_cores=4)) is True  # 2.0 >= 2
    assert cpu_saturated(_win(cpu_run_queue_p95=4.0, cpu_cores=4)) is False  # 1.0 < 2
    assert cpu_saturated(_win(cpu_run_queue_p95=None)) is None  # perflib 미발행
    # cores 0/None -> 미관측 (div-by-zero 회피)
    assert cpu_saturated(_stats(cpu_cores=None, procs_running_p95=100.0)) is None


def test_mem_saturated_os_aware():
    # Linux: active page-out(mem_swap_paging, 항상 관측 — None 없음). 정적 swap 점유(swap_used) 는 신호 아님.
    assert mem_saturated(_stats(mem_swap_paging=True)) is True
    assert mem_saturated(_stats(mem_swap_paging=False, swap_used=True)) is False
    # Windows: pagefile 사용량(swap_used) 무시, Pages Input/sec(하드폴트) rate 로 판정
    assert mem_saturated(_win(mem_pages_input_rate_p95=WIN_PAGES_INPUT_SATURATION)) is True
    assert mem_saturated(_win(mem_pages_input_rate_p95=10.0)) is False  # < 20
    assert mem_saturated(_win(mem_pages_input_rate_p95=None)) is None  # perflib 미발행 -> 미관측


def test_disk_io_saturated_os_aware():
    assert disk_io_saturated(_stats(disk_await_p95_ms=RS_DISKIO_AWAIT_MS + 1)) is True
    assert disk_io_saturated(_stats(disk_await_p95_ms=None)) is None
    assert disk_io_saturated(_win(disk_queue_p95=DISK_QUEUE_PER_DISK_SATURATION)) is True
    assert disk_io_saturated(_win(disk_queue_p95=1.0)) is False
    assert disk_io_saturated(_win(disk_queue_p95=None)) is None


def test_assess_windows_cpu_saturation_via_run_queue():
    """Windows run queue/cores >= 2 -> cpu_saturation trigger -> under_provisioned (loadavg 부재여도 실측)."""
    a = assess(_win(cpu_run_queue_p95=4.0 * CPU_RUN_QUEUE_PER_CORE_SATURATION, cpu_cores=4))
    assert a.recommendation == "under_provisioned"
    assert "cpu_saturation" in a.triggers
    assert "cpu_saturation" not in a.unmeasured  # 측정됨


def test_assess_windows_mem_saturation_via_paging():
    """Windows Pages/sec rate >= 임계 -> mem_saturation trigger -> under_provisioned (pagefile swap 무관)."""
    a = assess(_win(mem_pages_input_rate_p95=WIN_PAGES_INPUT_SATURATION))
    assert a.recommendation == "under_provisioned"
    assert "mem_saturation" in a.triggers


def test_assess_windows_perflib_absent_unmeasured_not_triggered():
    """Windows perflib 미발행(세 축 None) -> 미관측 기록·발화 안 함, 분류는 utilization 으로 완결."""
    a = assess(_win(cpu_p95_pct=40.0, mem_p95_pct=60.0))  # run_queue/paging/disk_queue 전부 None
    assert "cpu_saturation" in a.unmeasured
    assert "mem_saturation" in a.unmeasured
    assert "disk_io" in a.unmeasured
    assert not a.triggers  # 위험 신호 0
    assert a.recommendation == "optimal"  # utilization 로 완결 (표본 부족 아님)


def test_assess_linux_mem_saturation_never_unmeasured():
    """Linux 는 swap 이 항상 관측 — mem_saturation 은 unmeasured 에 절대 안 들어간다(swap 유무 무관)."""
    assert "mem_saturation" not in assess(_stats(swap_used=False)).unmeasured
    assert "mem_saturation" not in assess(_stats(swap_used=True)).unmeasured
