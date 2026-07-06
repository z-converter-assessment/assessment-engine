"""ADR 0052 신 자원 적정성 모델 — per-resource USE + 근본원인 종합 + 신뢰도 4종 회귀 가드.

대상: recommendation.py 신 함수(assess_cpu/memory/disk_capacity/disk_io/network · rollup_host ·
downsize_prescribable · ConfidenceNote). 합성 ResourceStats 입력으로 분기·사이징·근본원인·신뢰도를 검증.
기존 assess/classify(단일 분류)는 test_recommendation.py 가 커버 — 본 파일은 신 모델만.
"""

from assessment_engine import recommendation as r


def _stats(**kw) -> r.ResourceStats:
    """필수 축을 None/기본으로 채우고 kw 로 덮는 헬퍼 (ResourceStats 필수 필드가 많아 편의)."""
    base = dict(
        cpu_p95_pct=None,
        cpu_peak_pct=None,
        cpu_load_15m_max=None,
        cpu_cores=None,
        mem_p95_pct=None,
        swap_used=False,
        disk_used_pct=None,
        iowait_p95_pct=None,
        net_avg_kbps=None,
    )
    base.update(kw)
    return r.ResourceStats(**base)


# ─── CPU ───


def test_cpu_under_by_util():
    a = r.assess_cpu(_stats(cpu_p95_pct=90.0, cpu_cores=8, cpu_load_15m_max=1.0))
    assert a.status == "under"
    assert "cpu_util" in a.triggers
    assert a.sizing_target == 11  # ceil(90*8/70)=11 > sat ceil(1/0.7)=2


def test_cpu_under_by_saturation_only():
    # 이용률은 낮으나(40%) load/cores=2 >= 1 -> 포화로 under
    a = r.assess_cpu(_stats(cpu_p95_pct=40.0, cpu_cores=4, cpu_load_15m_max=8.0))
    assert a.status == "under"
    assert "cpu_saturation" in a.triggers
    assert "cpu_util" not in a.triggers
    assert a.sizing_target == 12  # sat ceil(8/0.7)=12 > util ceil(40*4/70)=3


def test_cpu_over():
    a = r.assess_cpu(_stats(cpu_p95_pct=10.0, cpu_cores=8, cpu_load_15m_max=0.5))
    assert a.status == "over"
    assert a.sizing_target == 2  # ceil(10*8/70)=2 < 8


def test_cpu_optimal_at_target():
    a = r.assess_cpu(_stats(cpu_p95_pct=65.0, cpu_cores=8, cpu_load_15m_max=4.0))
    assert a.status == "optimal"  # ceil(65*8/70)=8 == cores


def test_cpu_percore_hold_suppresses_over():
    # 집계 10%면 over 여야 하나, 한 코어가 90% -> 다운사이즈 보류 -> optimal
    a = r.assess_cpu(_stats(cpu_p95_pct=10.0, cpu_cores=8, cpu_load_15m_max=0.5, cpu_percore_p95_max=90.0))
    assert a.status == "optimal"


def test_cpu_unmeasured_no_util_no_sat():
    a = r.assess_cpu(_stats(cpu_p95_pct=None, cpu_cores=8, cpu_load_15m_max=None))
    assert a.status == "unmeasured"
    assert a.confidence.coverage_gap is True


def test_cpu_windows_saturation():
    a = r.assess_cpu(_stats(cpu_p95_pct=30.0, cpu_cores=4, cpu_run_queue_p95=10.0, os_family="windows"))
    assert a.status == "under"
    assert "cpu_saturation" in a.triggers  # 10/4=2.5 >= 2 (Windows 포화선)
    assert a.sizing_target == 8  # ceil(10/(2.0*0.7))=ceil(7.14)=8


# ─── 메모리 ───


def test_mem_under_by_util():
    a = r.assess_memory(_stats(mem_p95_pct=95.0, mem_total_mb=16384))
    assert a.status == "under"
    assert "mem_util" in a.triggers
    assert a.sizing_target == 22236  # ceil(16384*95/70)


def test_mem_under_by_swap_paging():
    a = r.assess_memory(_stats(mem_p95_pct=50.0, mem_swap_paging=True))
    assert a.status == "under"
    assert "mem_saturation" in a.triggers


def test_mem_swapless_high_util_primary():
    # swapless(mem_swap_paging=False)라도 이용률 92%면 under (이용률 주신호)
    a = r.assess_memory(_stats(mem_p95_pct=92.0, mem_swap_paging=False))
    assert a.status == "under"
    assert a.triggers == ["mem_util"]


def test_mem_over():
    a = r.assess_memory(_stats(mem_p95_pct=40.0, mem_total_mb=16384))
    assert a.status == "over"
    assert a.sizing_target == 9363  # ceil(16384*40/70) < 16384


def test_mem_optimal_at_target():
    a = r.assess_memory(_stats(mem_p95_pct=70.0, mem_total_mb=16384))
    assert a.status == "optimal"  # ceil(16384*70/70)=16384


def test_mem_unmeasured():
    a = r.assess_memory(_stats(mem_p95_pct=None, mem_swap_paging=False))
    assert a.status == "unmeasured"


def test_mem_under_no_total_no_sizing():
    a = r.assess_memory(_stats(mem_p95_pct=95.0, mem_total_mb=None))
    assert a.status == "under"
    assert a.sizing_target is None


# ─── 디스크 용량 ───


def test_disk_filling_by_runway():
    a = r.assess_disk_capacity(_stats(disk_capacity_runway_days=10.0))
    assert a.status == "filling"
    assert "disk_capacity" in a.triggers


def test_disk_ok_by_runway():
    a = r.assess_disk_capacity(_stats(disk_capacity_runway_days=100.0))
    assert a.status == "capacity_ok"


def test_disk_static_guard_fallback():
    # 추세 못 냄(runway None) + used 90% -> 정적 가드로 filling
    a = r.assess_disk_capacity(_stats(disk_used_pct=90.0))
    assert a.status == "filling"
    assert "정적 가드" in a.detail


def test_disk_static_guard_ok():
    a = r.assess_disk_capacity(_stats(disk_used_pct=50.0))
    assert a.status == "capacity_ok"


def test_disk_unmeasured():
    a = r.assess_disk_capacity(_stats())
    assert a.status == "unmeasured"


def test_disk_inode_runway_wins():
    # inode 가 더 빨리 소진 -> min runway 로 filling
    a = r.assess_disk_capacity(_stats(disk_capacity_runway_days=100.0, disk_inode_runway_days=5.0))
    assert a.status == "filling"


# ─── 디스크 I/O ───


def test_diskio_bound():
    a = r.assess_disk_io(_stats(disk_await_p95_ms=34.0))
    assert a.status == "io_bound"
    assert a.sizing_target is None  # 사이징 불가
    assert a.confidence.biased is True  # virtio 편향


def test_diskio_ok():
    a = r.assess_disk_io(_stats(disk_await_p95_ms=5.0))
    assert a.status == "io_ok"


def test_diskio_unmeasured():
    a = r.assess_disk_io(_stats(disk_await_p95_ms=None))
    assert a.status == "unmeasured"
    assert a.confidence.coverage_gap is True


# ─── 네트워크 ───


def test_net_congested_retrans():
    a = r.assess_network(_stats(net_retrans_pct=2.0, net_drop_pct=0.0))
    assert a.status == "congested"
    assert "net_retrans" in a.triggers


def test_net_congested_drop():
    a = r.assess_network(_stats(net_retrans_pct=0.0, net_drop_pct=1.0))
    assert a.status == "congested"
    assert "net_drop" in a.triggers


def test_net_quality_ok():
    a = r.assess_network(_stats(net_retrans_pct=0.5, net_drop_pct=0.1))
    assert a.status == "quality_ok"


def test_net_unmeasured():
    a = r.assess_network(_stats())
    assert a.status == "unmeasured"


# ─── 신뢰도 4종 ───


def test_confidence_low_precision_short_history():
    a = r.assess_cpu(_stats(cpu_p95_pct=90.0, cpu_cores=8, cpu_load_15m_max=1.0, history_hours=10.0))
    assert a.confidence.low_precision is True  # 30h 미만


def test_confidence_low_precision_bursty():
    a = r.assess_cpu(_stats(cpu_p95_pct=90.0, cpu_cores=8, cpu_load_15m_max=1.0, cpu_burst_ratio=3.0))
    assert a.confidence.low_precision is True  # p95/median > 2


def test_confidence_nonstationary_trend():
    a = r.assess_cpu(_stats(cpu_p95_pct=10.0, cpu_cores=8, cpu_load_15m_max=0.5, util_trend_rising=True))
    assert a.confidence.nonstationary is True


def test_confidence_high_property():
    clean = r.ConfidenceNote()
    assert clean.high is True
    assert r.ConfidenceNote(coverage_gap=True).high is False
    assert r.ConfidenceNote(biased=True).high is False
    assert r.ConfidenceNote(low_precision=True).high is False
    # 정상성은 high 에 무관 (별도 게이트)
    assert r.ConfidenceNote(nonstationary=True).high is True


# ─── 근본원인 종합 ───


def test_root_cause_memory_swap_coupling():
    # 메모리 under + swap 발생 + 디스크 I/O·CPU 압박 -> root=메모리, 나머지 증상
    s = _stats(
        cpu_p95_pct=90.0,
        cpu_cores=8,
        cpu_load_15m_max=1.0,
        mem_p95_pct=95.0,
        mem_swap_paging=True,
        disk_await_p95_ms=40.0,
    )
    h = r.rollup_host(s)
    assert h.root_cause == "memory"
    assert set(h.symptom_of_root) == {"disk_io", "cpu"}


def test_root_cause_diskio_procs_blocked():
    # 디스크 I/O 포화 + CPU under + procs_blocked 높음 (메모리 압박 없음) -> root=디스크, CPU 증상
    s = _stats(
        cpu_p95_pct=90.0,
        cpu_cores=8,
        cpu_load_15m_max=1.0,
        mem_p95_pct=40.0,
        mem_swap_paging=False,
        disk_await_p95_ms=40.0,
        procs_blocked_p95=5.0,
    )
    h = r.rollup_host(s)
    assert h.root_cause == "disk_io"
    assert h.symptom_of_root == ["cpu"]


def test_root_cause_independent_no_coupling():
    # CPU under + 디스크 용량 filling, 결합 신호 없음 -> 각자 처방(root=상류 cpu, 증상 억제 없음)
    s = _stats(
        cpu_p95_pct=90.0,
        cpu_cores=8,
        cpu_load_15m_max=1.0,
        disk_capacity_runway_days=5.0,
    )
    h = r.rollup_host(s)
    assert h.root_cause == "cpu"
    assert h.symptom_of_root == []


def test_root_cause_mem_cpu_no_swap_independent():
    # 메모리 under + CPU under 지만 swap 발생 없음 -> 결합 아님, 각자(증상 억제 없음)
    s = _stats(
        cpu_p95_pct=90.0,
        cpu_cores=8,
        cpu_load_15m_max=1.0,
        mem_p95_pct=95.0,
        mem_swap_paging=False,
    )
    h = r.rollup_host(s)
    assert h.root_cause == "memory"  # 최상류
    assert h.symptom_of_root == []


# ─── 다운사이즈 처방 게이트 ───


def _over_cpu_stats(**kw) -> r.ResourceStats:
    return _stats(cpu_p95_pct=10.0, cpu_cores=8, cpu_load_15m_max=0.5, **kw)


def test_downsize_prescribable_when_confident():
    s = _over_cpu_stats(sample_sufficiency=0.9)  # 창 충분 관측(>=0.7)
    a = r.assess_cpu(s)
    assert a.status == "over"
    assert r.downsize_prescribable(a, s) is True


def test_downsize_gated_by_low_confidence():
    s = _over_cpu_stats(sample_sufficiency=0.9, cpu_burst_ratio=3.0)  # 버스티 -> low_precision
    a = r.assess_cpu(s)
    assert a.status == "over"
    assert r.downsize_prescribable(a, s) is False


def test_downsize_gated_by_rising_trend():
    s = _over_cpu_stats(sample_sufficiency=0.9, util_trend_rising=True)
    a = r.assess_cpu(s)
    assert r.downsize_prescribable(a, s) is False


def test_downsize_gated_by_low_sufficiency():
    s = _over_cpu_stats(sample_sufficiency=0.4)  # 창 대비 관측 부족(<0.7)
    a = r.assess_cpu(s)
    assert r.downsize_prescribable(a, s) is False


def test_downsize_gated_by_missing_sufficiency():
    s = _over_cpu_stats()  # sample_sufficiency None(측정 축 부재) -> 처방 불가
    a = r.assess_cpu(s)
    assert r.downsize_prescribable(a, s) is False


def test_downsize_not_over_status():
    s = _stats(cpu_p95_pct=90.0, cpu_cores=8, cpu_load_15m_max=1.0, sample_sufficiency=0.9)
    a = r.assess_cpu(s)  # under
    assert r.downsize_prescribable(a, s) is False


# ─── util_trend_rising_from_slopes — 상승 추세 이진화(도메인 임계 단일) ───


def test_util_trend_rising_both_none_returns_none():
    """cpu·mem slope 둘 다 None(표본<2 등) -> None (nonstationary 미설정)."""
    assert r.util_trend_rising_from_slopes(None, None) is None


def test_util_trend_rising_true_when_any_slope_over_threshold():
    """하나라도 임계(%/day) 이상이면 상승 — 보수적(어느 코어 자원이든 성장하면 다운사이즈 보류)."""
    assert r.util_trend_rising_from_slopes(-0.5, r.RS_UTIL_TREND_RISING_PCT_PER_DAY) is True
    assert r.util_trend_rising_from_slopes(r.RS_UTIL_TREND_RISING_PCT_PER_DAY + 1.0, None) is True


def test_util_trend_rising_false_when_all_below_threshold():
    """전부 임계 미만(보합·하락) -> 비상승."""
    assert r.util_trend_rising_from_slopes(0.0, -2.0) is False


# ─── steal 충실도 편향 ───


def test_cpu_steal_biases_confidence():
    a = r.assess_cpu(_stats(cpu_p95_pct=90.0, cpu_cores=8, cpu_load_15m_max=1.0, cpu_steal_p95_pct=10.0))
    assert a.confidence.biased is True  # steal >= 5% -> 충실도 편향


def test_cpu_low_steal_no_bias():
    a = r.assess_cpu(_stats(cpu_p95_pct=90.0, cpu_cores=8, cpu_load_15m_max=1.0, cpu_steal_p95_pct=1.0))
    assert a.confidence.biased is False


# ─── 호스트 요약 상태 (rollup) ───


def test_host_status_under():
    h = r.rollup_host(_stats(cpu_p95_pct=90.0, cpu_cores=8, cpu_load_15m_max=1.0))
    assert h.host_status == "under"


def test_host_status_idle():
    # 전 자원 ok + CPU 피크 거의 0 + 네트워크 거의 0
    h = r.rollup_host(
        _stats(
            cpu_p95_pct=1.0,
            cpu_peak_pct=1.0,
            cpu_cores=8,
            cpu_load_15m_max=0.1,
            mem_p95_pct=70,
            mem_total_mb=16384,
            net_avg_kbps=0.5,
        )
    )
    assert h.host_status == "idle"


def test_host_status_shutdown():
    # CPU p95 <= 3% + 네트워크 낮음(mbps <= 2) 이나 peak 는 idle 문턱 초과 -> shutdown
    h = r.rollup_host(
        _stats(
            cpu_p95_pct=2.0,
            cpu_peak_pct=10.0,
            cpu_cores=8,
            cpu_load_15m_max=0.1,
            mem_p95_pct=70,
            mem_total_mb=16384,
            net_avg_kbps=100.0,
        )
    )
    assert h.host_status == "shutdown"


def test_host_status_over():
    # CPU over(저사용) + 압박 없음 + idle 아님 -> over
    h = r.rollup_host(
        _stats(
            cpu_p95_pct=10.0,
            cpu_peak_pct=40.0,
            cpu_cores=8,
            cpu_load_15m_max=0.5,
            mem_p95_pct=70,
            mem_total_mb=16384,
            net_avg_kbps=500.0,
        )
    )
    assert h.host_status == "over"


def test_host_status_optimal():
    h = r.rollup_host(
        _stats(
            cpu_p95_pct=65.0,
            cpu_peak_pct=80.0,
            cpu_cores=8,
            cpu_load_15m_max=4.0,
            mem_p95_pct=70,
            mem_total_mb=16384,
            net_avg_kbps=500.0,
        )
    )
    assert h.host_status == "optimal"


def test_host_status_insufficient():
    h = r.rollup_host(_stats())  # 전 자원 unmeasured
    assert h.host_status == "insufficient"


def test_host_network_congested_flag():
    h = r.rollup_host(
        _stats(
            cpu_p95_pct=65.0,
            cpu_peak_pct=80.0,
            cpu_cores=8,
            cpu_load_15m_max=4.0,
            mem_p95_pct=70,
            mem_total_mb=16384,
            net_avg_kbps=500.0,
            net_retrans_pct=3.0,
        )
    )
    assert h.network_congested is True


# ─── 라벨 완전성 (신 상태·트리거 전부 라벨 있음) ───


def test_labels_cover_all_statuses():
    from typing import get_args

    for status in get_args(r.ResourceStatus):
        assert status in r.RS_STATUS_LABEL_KO, f"missing status label: {status}"
    for hs in get_args(r.HostStatus):
        assert hs in r.RS_HOST_STATUS_LABEL_KO, f"missing host status label: {hs}"


def test_labels_cover_all_triggers():
    # 자원별 assess 가 낼 수 있는 트리거 키 전부 라벨 존재
    keys = {
        "cpu_util",
        "cpu_saturation",
        "mem_util",
        "mem_saturation",
        "disk_capacity",
        "disk_io",
        "net_retrans",
        "net_drop",
    }
    assert keys <= set(r.RS_TRIGGER_LABEL_KO)
