import dataclasses
from typing import Any

from assessment_engine.domain import right_sizing as r


def _stats(**kw: Any) -> r.ResourceStats:
    base = r.ResourceStats(
        cpu_p95_pct=None,
        cpu_peak_pct=None,
        procs_running_p95=None,
        cpu_cores=None,
        mem_p95_pct=None,
        disk_used_pct=None,
        net_avg_kbytes_per_s=None,
    )
    return dataclasses.replace(base, **kw)


def test_cpu_under_by_util():
    a = r.assess_cpu(_stats(cpu_p95_pct=90.0, cpu_cores=8, procs_running_p95=1.0))
    assert a.status == "under"
    assert "cpu_util" in a.triggers
    assert a.sizing_target == 11


def test_cpu_saturation_dual_gate():
    a = r.assess_cpu(_stats(cpu_p95_pct=40.0, cpu_cores=4, procs_running_p95=8.0))
    assert a.status != "under"
    assert "cpu_saturation" not in a.triggers
    b = r.assess_cpu(_stats(cpu_p95_pct=75.0, cpu_cores=4, procs_running_p95=8.0))
    assert b.status == "under"
    assert "cpu_saturation" in b.triggers
    assert "cpu_util" in b.triggers
    assert b.sizing_target == 12


def test_cpu_over():
    a = r.assess_cpu(_stats(cpu_p95_pct=10.0, cpu_cores=8, procs_running_p95=0.5))
    assert a.status == "over"
    assert a.sizing_target == 2


def test_cpu_optimal_at_target():
    a = r.assess_cpu(_stats(cpu_p95_pct=65.0, cpu_cores=8, procs_running_p95=4.0))
    assert a.status == "optimal"


def test_cpu_percore_hold_suppresses_over():
    a = r.assess_cpu(_stats(cpu_p95_pct=10.0, cpu_cores=8, procs_running_p95=0.5, cpu_percore_p95_max=90.0))
    assert a.status == "optimal"


def test_cpu_unmeasured_no_util_no_sat():
    a = r.assess_cpu(_stats(cpu_p95_pct=None, cpu_cores=8))
    assert a.status == "unmeasured"
    assert a.confidence.coverage_gap is True


def test_cpu_windows_saturation():
    a = r.assess_cpu(_stats(cpu_p95_pct=75.0, cpu_cores=4, cpu_run_queue_p95=10.0, os_family="windows"))
    assert a.status == "under"
    assert "cpu_saturation" in a.triggers
    assert a.sizing_target == 8


def test_mem_under_by_util():
    a = r.assess_memory(_stats(mem_p95_pct=95.0, mem_total_mb=16384))
    assert a.status == "under"
    assert "mem_util" in a.triggers
    assert a.sizing_target == 19456


def test_mem_under_by_swap_paging():
    a = r.assess_memory(_stats(mem_p95_pct=95.0, mem_swap_paging=True))
    assert a.status == "under"
    assert "mem_saturation" in a.triggers


def test_memory_windows_missing_saturation_signal_sets_coverage_gap():
    a = r.assess_memory(_stats(mem_p95_pct=95.0, os_family="windows", mem_pages_input_rate_p95=None))
    assert a.status == "under"
    assert a.confidence.coverage_gap is True


def test_memory_linux_missing_saturation_signal_sets_coverage_gap():
    a = r.assess_memory(_stats(mem_p95_pct=95.0, os_family="linux", mem_swap_paging=None))
    assert a.status == "under"
    assert a.confidence.coverage_gap is True


def test_mem_swapless_high_util_primary():
    a = r.assess_memory(_stats(mem_p95_pct=92.0, mem_swap_paging=False))
    assert a.status == "under"
    assert a.triggers == ["mem_util"]


def test_mem_over():
    a = r.assess_memory(_stats(mem_p95_pct=40.0, mem_total_mb=16384))
    assert a.status == "over"
    assert a.sizing_target == 8192


def test_mem_optimal_at_target():
    a = r.assess_memory(_stats(mem_p95_pct=80.0, mem_total_mb=16384))
    assert a.status == "optimal"


def test_mem_unmeasured():
    a = r.assess_memory(_stats(mem_p95_pct=None, mem_swap_paging=False))
    assert a.status == "unmeasured"


def test_mem_under_no_total_no_sizing():
    a = r.assess_memory(_stats(mem_p95_pct=95.0, mem_total_mb=None))
    assert a.status == "under"
    assert a.sizing_target is None


def test_memory_under_invalid_near_peak_uses_headroom_floor():
    a = r.assess_memory(_stats(mem_p95_pct=95.0, mem_near_peak_pct=0.0, mem_total_mb=1000))
    assert a.sizing_target is None
    assert a.sizing_floor == 1300
    assert a.detail == "증설(최소 1300MB)"
    assert r.resource_prescription("memory", a) == "메모리: 최소 1.3GB"


def test_cpu_under_at_utilization_threshold_uses_increase_floor():
    a = r.assess_cpu(_stats(cpu_p95_pct=70.0, cpu_cores=8, procs_running_p95=0.5))
    assert a.sizing_target is None
    assert a.sizing_floor == 9
    assert a.detail == "증설(최소 9코어)"
    assert r.resource_prescription("cpu", a) == "CPU: 최소 9코어"


def test_disk_filling_by_runway():
    a = r.assess_disk_capacity(_stats(disk_capacity_runway_days=10.0))
    assert a.status == "filling"
    assert "disk_capacity" in a.triggers


def test_disk_ok_by_runway():
    a = r.assess_disk_capacity(_stats(disk_capacity_runway_days=100.0))
    assert a.status == "capacity_ok"


def test_disk_static_guard_fallback():
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
    a = r.assess_disk_capacity(_stats(disk_capacity_runway_days=100.0, disk_inode_runway_days=5.0))
    assert a.status == "filling"
    assert a.triggers == ["disk_inode"]
    assert r.resource_prescription("disk_capacity", a) == "inode 정리/재포맷"


def test_disk_byte_and_inode_capacity_actions_are_both_preserved():
    a = r.assess_disk_capacity(
        _stats(disk_capacity_runway_days=5.0, disk_inode_runway_days=10.0, disk_capacity_target_gb=500.0)
    )
    assert a.triggers == ["disk_capacity", "disk_inode"]
    assert r.resource_prescription("disk_capacity", a) == "스토리지: 500GB | inode 정리/재포맷"


def test_diskio_bound():
    a = r.assess_disk_io(_stats(disk_await_p95_ms=34.0))
    assert a.status == "io_bound"
    assert a.sizing_target is None
    assert a.confidence.measurement_bias is False


def test_diskio_ok():
    a = r.assess_disk_io(_stats(disk_await_p95_ms=5.0))
    assert a.status == "io_ok"


def test_diskio_unmeasured():
    a = r.assess_disk_io(_stats(disk_await_p95_ms=None))
    assert a.status == "unmeasured"
    assert a.confidence.coverage_gap is True


def test_diskio_high_activity_without_await_is_unmeasured():
    a = r.assess_disk_io(_stats(disk_await_p95_ms=None, disk_iops_baseline=r.IDLE_DISK_BASELINE_IOPS + 1))
    assert a.status == "unmeasured"
    assert a.confidence.coverage_gap is True


def test_diskio_low_activity_without_await_is_ok():
    a = r.assess_disk_io(_stats(disk_await_p95_ms=None, disk_iops_baseline=r.IDLE_DISK_BASELINE_IOPS))
    assert a.status == "io_ok"
    assert a.confidence.coverage_gap is False


def test_net_congested_retrans():
    a = r.assess_network(_stats(net_avg_kbytes_per_s=r.NET_MIN_TRAFFIC_KBPS, net_retrans_pct=2.0, net_drop_pct=0.0))
    assert a.status == "congested"
    assert "net_retrans" in a.triggers


def test_net_congested_drop():
    a = r.assess_network(_stats(net_avg_kbytes_per_s=r.NET_MIN_TRAFFIC_KBPS, net_retrans_pct=0.0, net_drop_pct=1.0))
    assert a.status == "congested"
    assert "net_drop" in a.triggers


def test_net_quality_ok():
    a = r.assess_network(_stats(net_avg_kbytes_per_s=r.NET_MIN_TRAFFIC_KBPS, net_retrans_pct=0.5, net_drop_pct=0.1))
    assert a.status == "quality_ok"


def test_net_low_traffic_is_quality_ok_with_rate_trigger_deferred():
    a = r.assess_network(_stats(net_retrans_pct=2.0, net_drop_pct=0.0))
    assert a.status == "quality_ok"
    assert a.confidence.coverage_gap is False
    assert a.detail == "저트래픽: 재전송/드롭 판정 생략"


def test_net_conntrack_congested_while_low_traffic_omits_deferred_rates():
    a = r.assess_network(
        _stats(
            net_avg_kbytes_per_s=r.NET_MIN_TRAFFIC_KBPS - 1,
            net_retrans_pct=2.0,
            net_drop_pct=1.0,
            conntrack_ratio=r.CONNTRACK_SATURATION_RATIO,
        )
    )
    assert a.status == "congested"
    assert a.triggers == ["net_conntrack"]
    assert a.detail == "conntrack 80%"


def test_net_unmeasured():
    a = r.assess_network(_stats())
    assert a.status == "unmeasured"


def test_confidence_insufficient_history():
    a = r.assess_cpu(_stats(cpu_p95_pct=90.0, cpu_cores=8, procs_running_p95=1.0, history_hours=10.0))
    assert a.confidence.insufficient_history is True


def test_confidence_high_utilization_variability():
    a = r.assess_cpu(_stats(cpu_p95_pct=90.0, cpu_cores=8, procs_running_p95=1.0, cpu_burst_ratio=3.0))
    assert a.confidence.high_utilization_variability is True


def test_cpu_utilization_variability_does_not_lower_memory_confidence():
    a = r.assess_memory(_stats(mem_p95_pct=10.0, cpu_burst_ratio=3.0))
    assert a.confidence.high_utilization_variability is False


def test_cpu_confidence_nonstationary_uses_cpu_trend_only():
    a = r.assess_cpu(
        _stats(
            cpu_p95_pct=10.0,
            cpu_cores=8,
            procs_running_p95=0.5,
            cpu_utilization_trend_rising=True,
            memory_utilization_trend_rising=False,
        )
    )
    assert a.confidence.rising_utilization_trend is True


def test_memory_confidence_nonstationary_uses_memory_trend_only():
    a = r.assess_memory(
        _stats(
            mem_p95_pct=10.0,
            memory_utilization_trend_rising=True,
            cpu_utilization_trend_rising=False,
        )
    )
    assert a.confidence.rising_utilization_trend is True


def test_cpu_and_memory_trends_do_not_cross_affect_confidence():
    stats = _stats(
        cpu_p95_pct=10.0,
        cpu_cores=8,
        procs_running_p95=0.5,
        mem_p95_pct=10.0,
        cpu_utilization_trend_rising=True,
        memory_utilization_trend_rising=False,
    )
    assert r.assess_cpu(stats).confidence.rising_utilization_trend is True
    assert r.assess_memory(stats).confidence.rising_utilization_trend is False


def test_confidence_high_property():
    clean = r.ConfidenceNote()
    assert clean.high is True
    assert r.ConfidenceNote(coverage_gap=True).high is False
    assert r.ConfidenceNote(measurement_bias=True).high is False
    assert r.ConfidenceNote(insufficient_history=True).high is False
    assert r.ConfidenceNote(high_utilization_variability=True).high is False
    assert r.ConfidenceNote(rising_utilization_trend=True).high is True


def test_root_cause_memory_swap_coupling():
    s = _stats(
        cpu_p95_pct=90.0,
        cpu_cores=8,
        procs_running_p95=1.0,
        mem_p95_pct=95.0,
        mem_swap_paging=True,
        disk_await_p95_ms=40.0,
    )
    h = r.rollup_host(s)
    assert h.root_cause == "memory"
    assert set(h.symptom_of_root) == {"disk_io", "cpu"}


def test_root_cause_diskio_procs_blocked():
    s = _stats(
        cpu_p95_pct=90.0,
        cpu_cores=8,
        procs_running_p95=8.0,
        mem_p95_pct=40.0,
        mem_swap_paging=False,
        disk_await_p95_ms=40.0,
        procs_blocked_p95=5.0,
    )
    h = r.rollup_host(s)
    assert h.root_cause == "disk_io"
    assert h.symptom_of_root == ["cpu"]


def test_root_cause_diskio_requires_cpu_saturation():
    s = _stats(
        cpu_p95_pct=90.0,
        cpu_cores=8,
        procs_running_p95=0.5,
        mem_p95_pct=40.0,
        mem_swap_paging=False,
        disk_await_p95_ms=40.0,
        procs_blocked_p95=5.0,
    )
    h = r.rollup_host(s)
    assert h.root_cause == "cpu"
    assert h.symptom_of_root == []


def test_root_cause_independent_no_coupling():
    s = _stats(
        cpu_p95_pct=90.0,
        cpu_cores=8,
        procs_running_p95=1.0,
        disk_capacity_runway_days=5.0,
    )
    h = r.rollup_host(s)
    assert h.root_cause == "cpu"
    assert h.symptom_of_root == []


def test_under_prescription_order_excludes_advisory_resources():
    h = r.rollup_host(
        _stats(
            cpu_p95_pct=90.0,
            cpu_cores=8,
            procs_running_p95=1.0,
            mem_p95_pct=95.0,
            disk_capacity_runway_days=5.0,
            disk_await_p95_ms=40.0,
        )
    )
    assert r.prescribed_under_kinds(h) == ["memory", "cpu", "disk_capacity"]


def test_root_cause_mem_cpu_no_swap_independent():
    s = _stats(
        cpu_p95_pct=90.0,
        cpu_cores=8,
        procs_running_p95=1.0,
        mem_p95_pct=95.0,
        mem_swap_paging=False,
    )
    h = r.rollup_host(s)
    assert h.root_cause == "memory"
    assert h.symptom_of_root == []


def _over_cpu_stats(**kw: Any) -> r.ResourceStats:
    return _stats(cpu_p95_pct=10.0, cpu_cores=8, procs_running_p95=0.5, **kw)


def test_downsize_prescribable_when_confident():
    s = _over_cpu_stats(sample_sufficiency=0.9)
    a = r.assess_cpu(s)
    assert a.status == "over"
    assert r.can_prescribe_downsize(a, s) is True
    host = r.rollup_host(s)
    assert r.prescribable_downsize_kinds(host, s) == ["cpu"]
    assert r.host_recommendation_action(host, s) == "축소 검토"


def test_downsize_gated_by_low_confidence():
    s = _over_cpu_stats(sample_sufficiency=0.9, cpu_burst_ratio=3.0)
    a = r.assess_cpu(s)
    assert a.status == "over"
    assert r.can_prescribe_downsize(a, s) is False


def test_downsize_gated_by_rising_trend():
    s = _over_cpu_stats(sample_sufficiency=0.9, cpu_utilization_trend_rising=True)
    a = r.assess_cpu(s)
    assert r.can_prescribe_downsize(a, s) is False


def test_downsize_gated_by_low_sufficiency():
    s = _over_cpu_stats(sample_sufficiency=0.4)
    a = r.assess_cpu(s)
    assert r.can_prescribe_downsize(a, s) is False
    host = r.rollup_host(s)
    assert host.recommendation == "over_provisioned"
    assert r.prescribable_downsize_kinds(host, s) == []
    assert r.host_recommendation_action(host, s) == "관찰 지속"


def test_downsize_gated_by_missing_sufficiency():
    s = _over_cpu_stats()
    a = r.assess_cpu(s)
    assert r.can_prescribe_downsize(a, s) is False


def test_downsize_not_over_status():
    s = _stats(cpu_p95_pct=90.0, cpu_cores=8, procs_running_p95=1.0, sample_sufficiency=0.9)
    a = r.assess_cpu(s)
    assert r.can_prescribe_downsize(a, s) is False


def test_utilization_trend_rising_none_returns_none():
    assert r.is_utilization_trend_rising(None) is None


def test_utilization_trend_rising_true_at_threshold():
    assert r.is_utilization_trend_rising(r.UTILIZATION_TREND_RISE_PCT_POINTS_PER_DAY) is True


def test_utilization_trend_rising_false_below_threshold():
    assert r.is_utilization_trend_rising(0.0) is False


def test_cpu_steal_biases_confidence():
    a = r.assess_cpu(_stats(cpu_p95_pct=90.0, cpu_cores=8, procs_running_p95=1.0, cpu_steal_p95_pct=10.0))
    assert a.confidence.measurement_bias is True


def test_cpu_low_steal_no_bias():
    a = r.assess_cpu(_stats(cpu_p95_pct=90.0, cpu_cores=8, procs_running_p95=1.0, cpu_steal_p95_pct=1.0))
    assert a.confidence.measurement_bias is False


def test_host_recommendation_under_provisioned():
    h = r.rollup_host(_stats(cpu_p95_pct=90.0, cpu_cores=8, procs_running_p95=1.0))
    assert h.recommendation == "under_provisioned"


def test_host_recommendation_idle():
    h = r.rollup_host(
        _stats(
            cpu_p95_pct=1.0,
            cpu_peak_pct=1.0,
            cpu_cores=8,
            procs_running_p95=0.1,
            mem_p95_pct=70,
            mem_total_mb=16384,
            net_avg_kbytes_per_s=0.5,
        )
    )
    assert h.recommendation == "idle"


def test_host_recommendation_idle_by_low_p95():
    h = r.rollup_host(
        _stats(
            cpu_p95_pct=2.0,
            cpu_peak_pct=10.0,
            cpu_cores=8,
            procs_running_p95=0.1,
            mem_p95_pct=70,
            mem_total_mb=16384,
            net_avg_kbytes_per_s=100.0,
        )
    )
    assert h.recommendation == "idle"


def test_host_recommendation_over_provisioned():
    h = r.rollup_host(
        _stats(
            cpu_p95_pct=10.0,
            cpu_peak_pct=40.0,
            cpu_cores=8,
            procs_running_p95=0.5,
            mem_p95_pct=70,
            mem_total_mb=16384,
            net_avg_kbytes_per_s=500.0,
        )
    )
    assert h.recommendation == "over_provisioned"


def test_host_recommendation_optimal():
    h = r.rollup_host(
        _stats(
            cpu_p95_pct=65.0,
            cpu_peak_pct=80.0,
            cpu_cores=8,
            procs_running_p95=4.0,
            mem_p95_pct=80,
            mem_total_mb=16384,
            net_avg_kbytes_per_s=500.0,
        )
    )
    assert h.recommendation == "optimal"


def test_host_recommendation_insufficient_data():
    h = r.rollup_host(_stats())
    assert h.recommendation == "insufficient_data"


def test_host_network_congested_flag():
    h = r.rollup_host(
        _stats(
            cpu_p95_pct=65.0,
            cpu_peak_pct=80.0,
            cpu_cores=8,
            procs_running_p95=4.0,
            mem_p95_pct=70,
            mem_total_mb=16384,
            net_avg_kbytes_per_s=500.0,
            net_retrans_pct=3.0,
        )
    )
    assert h.network_congested is True


def test_labels_cover_all_statuses():
    from typing import get_args

    for status in get_args(r.ResourceStatus.__value__):
        assert status in r.RESOURCE_STATUS_LABEL_KO, f"missing status label: {status}"
    for recommendation in get_args(r.Recommendation.__value__):
        assert recommendation in r.RECOMMENDATION_LABEL_KO, f"missing recommendation label: {recommendation}"


def test_labels_cover_all_triggers():
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
    assert keys <= set(r.TRIGGER_LABEL_KO)


def test_under_prescription_coupled_root_still_lists_symptom_resources():
    s = _stats(
        cpu_p95_pct=90.0,
        cpu_cores=8,
        procs_running_p95=8.0,
        mem_p95_pct=95.0,
        mem_total_mb=16384,
        mem_swap_paging=True,
        disk_await_p95_ms=40.0,
        procs_blocked_p95=2.0,
    )
    h = r.rollup_host(s)
    presc = r.under_prescription(h)
    assert h.root_cause == "memory"
    assert h.symptom_of_root
    assert presc.startswith("메모리: ")
    assert "CPU" in presc


def test_under_prescription_independent_all():
    s = _stats(cpu_p95_pct=90.0, cpu_cores=8, procs_running_p95=1.0, disk_used_pct=90.0)
    h = r.rollup_host(s)
    presc = r.under_prescription(h)
    assert not h.symptom_of_root
    assert "CPU: " in presc
    assert "스토리지 확장" in presc


def test_under_prescription_empty_when_no_under():
    assert r.under_prescription(r.rollup_host(_stats(cpu_p95_pct=50.0, cpu_cores=8, procs_running_p95=0.5))) == ""


def test_disk_capacity_target_1yr():
    a = r.assess_disk_capacity(_stats(disk_capacity_runway_days=20.0, disk_capacity_target_gb=500.0))
    assert a.status == "filling"
    assert a.sizing_target == 500.0
    assert "목표 500GB" in a.detail
    assert r.resource_prescription("disk_capacity", a) == "스토리지: 500GB"


def test_disk_capacity_target_none_when_inode_drives():
    a = r.assess_disk_capacity(
        _stats(disk_capacity_runway_days=100.0, disk_inode_runway_days=10.0, disk_capacity_target_gb=500.0)
    )
    assert a.status == "filling"
    assert a.sizing_target is None
    assert r.resource_prescription("disk_capacity", a) == "inode 정리/재포맷"
