from typing import TYPE_CHECKING, Any

from assessment_engine.domain.right_sizing import (
    CONNTRACK_SATURATION_RATIO,
    CPU_RUN_QUEUE_PER_CORE_SATURATION,
    DISK_HEADROOM_TARGET_PCT,
    DISK_QUEUE_PER_DISK_SATURATION,
    DISK_RUNWAY_DAYS,
    DISK_STATIC_GUARD_PCT,
    DISKIO_AWAIT_MS,
    NET_DROP_PCT,
    NET_MIN_TRAFFIC_KBPS,
    NET_RETRANS_PCT,
    PROCS_RUNNING_PER_CORE_SATURATION,
    WIN_PAGES_INPUT_SATURATION,
    ConfidenceNote,
    HostAssessment,
    MountSizing,
    ResourceAssessment,
    ResourceKind,
    ResourceStats,
    ResourceStatus,
    assess_mount_capacity,
    cpu_saturated,
    cpu_saturation_index,
    disk_io_saturated,
    disk_io_saturation_index,
    host_saturation_unmeasured,
    mem_pressure_active,
    mem_saturated,
    net_signal_active,
    root_cause_display,
)

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject

_GIB = 1024**3


def _stats(**overrides: Any) -> ResourceStats:
    base: JsonObject = {
        "cpu_p95_pct": 40.0,
        "cpu_peak_pct": 50.0,
        "procs_running_p95": 0.5,
        "cpu_cores": 4,
        "mem_p95_pct": 60.0,
        "disk_used_pct": 50.0,
        "disk_await_p95_ms": 5.0,
        "net_avg_kbytes_per_s": 100.0,
    }
    base.update(overrides)
    return ResourceStats(**base)


def _win(**overrides: Any) -> ResourceStats:
    base = {
        "os_family": "windows",
        "disk_await_p95_ms": None,
    }
    base.update(overrides)
    return _stats(**base)


def test_mem_saturated_linux_uses_page_out_not_static_swap():
    assert mem_saturated(_stats(mem_p95_pct=95.0, os_family="linux", mem_swap_paging=False)) is False
    assert mem_saturated(_stats(mem_p95_pct=95.0, os_family="linux", mem_swap_paging=True)) is True


def test_mem_saturated_windows_excludes_pagefile_uses_hardfault_rate():
    assert mem_saturated(_win(mem_p95_pct=95.0, mem_pages_input_rate_p95=WIN_PAGES_INPUT_SATURATION)) is True
    assert mem_saturated(_win(mem_p95_pct=95.0, mem_pages_input_rate_p95=10.0)) is False
    assert mem_saturated(_win(mem_p95_pct=95.0, mem_pages_input_rate_p95=None)) is None


def test_cpu_saturated_os_aware():
    assert cpu_saturated(_stats(procs_running_p95=4.0, cpu_cores=4, cpu_p95_pct=85.0)) is True
    assert cpu_saturated(_stats(procs_running_p95=4.0, cpu_cores=4, cpu_p95_pct=10.0)) is False
    assert cpu_saturated(_stats(procs_running_p95=1.0, cpu_cores=4, cpu_p95_pct=85.0)) is False
    assert cpu_saturated(_stats(procs_running_p95=4.0, cpu_cores=4, cpu_p95_pct=None)) is True
    assert cpu_saturated(_stats(procs_running_p95=None)) is None
    assert cpu_saturated(_win(cpu_run_queue_p95=8.0, cpu_cores=4, cpu_p95_pct=85.0)) is True
    assert cpu_saturated(_win(cpu_run_queue_p95=4.0, cpu_cores=4, cpu_p95_pct=85.0)) is False
    assert cpu_saturated(_win(cpu_run_queue_p95=None)) is None
    assert cpu_saturated(_stats(cpu_cores=None, procs_running_p95=100.0)) is None


def test_disk_io_saturated_os_aware():
    assert disk_io_saturated(_stats(disk_await_p95_ms=DISKIO_AWAIT_MS + 1)) is True
    assert disk_io_saturated(_stats(disk_await_p95_ms=None)) is None
    assert disk_io_saturated(_win(disk_await_p95_ms=DISKIO_AWAIT_MS + 1)) is True
    assert disk_io_saturated(_win(disk_queue_p95=DISK_QUEUE_PER_DISK_SATURATION)) is True
    assert disk_io_saturated(_win(disk_queue_p95=1.0)) is False
    assert disk_io_saturated(_win(disk_queue_p95=None)) is None


def test_cpu_saturation_index_os_aware():
    assert cpu_saturation_index(4.0, 4, "linux") == (4.0 / 4) / PROCS_RUNNING_PER_CORE_SATURATION
    assert cpu_saturation_index(4.0, 4, "linux") == 1.0
    assert cpu_saturation_index(2.0, 4, None) == 0.5
    assert cpu_saturation_index(4.0, 4, "windows") == (4.0 / 4) / CPU_RUN_QUEUE_PER_CORE_SATURATION
    assert cpu_saturation_index(4.0, 4, "windows") == 0.5
    assert cpu_saturation_index(8.0, 4, "windows") == 1.0
    assert cpu_saturation_index(None, 4, "linux") is None
    assert cpu_saturation_index(4.0, 0, "linux") is None
    assert cpu_saturation_index(4.0, None, "linux") is None


def test_disk_io_saturation_index_await_priority_queue_fallback():
    assert disk_io_saturation_index(DISKIO_AWAIT_MS, None, "linux") == 1.0
    assert disk_io_saturation_index(40.0, None, "linux") == 40.0 / DISKIO_AWAIT_MS
    assert disk_io_saturation_index(40.0, 100.0, "windows") == 40.0 / DISKIO_AWAIT_MS
    assert disk_io_saturation_index(None, DISK_QUEUE_PER_DISK_SATURATION, "windows") == 1.0
    assert disk_io_saturation_index(None, 4.0, "windows") == 4.0 / DISK_QUEUE_PER_DISK_SATURATION
    assert disk_io_saturation_index(None, 4.0, "linux") is None
    assert disk_io_saturation_index(None, None, "windows") is None


def test_net_signal_active_low_traffic_gate():
    hi = NET_MIN_TRAFFIC_KBPS + 1.0
    lo = NET_MIN_TRAFFIC_KBPS - 1.0
    over_retrans = NET_RETRANS_PCT + 1.0
    over_drop = NET_DROP_PCT + 0.5
    over_ct = CONNTRACK_SATURATION_RATIO + 0.05
    assert net_signal_active(over_retrans, None, None, hi) is True
    assert net_signal_active(None, over_drop, None, hi) is True
    assert net_signal_active(over_retrans, over_drop, None, lo) is False
    assert net_signal_active(None, None, over_ct, lo) is True
    assert net_signal_active(over_retrans, None, None, None) is True
    assert net_signal_active(0.1, 0.1, 0.1, hi) is False


def test_mem_pressure_active_os_aware():
    assert mem_pressure_active(None, "linux") is False
    assert mem_pressure_active(None, "windows") is False
    assert mem_pressure_active(0.1, "linux") is True
    assert mem_pressure_active(0.0, "linux") is False
    assert mem_pressure_active(0.1, None) is True
    assert mem_pressure_active(WIN_PAGES_INPUT_SATURATION, "windows") is True
    assert mem_pressure_active(10.0, "windows") is False
    assert mem_pressure_active(0.1, "windows") is False


_ALL_KINDS: tuple[ResourceKind, ...] = ("cpu", "memory", "disk_capacity", "disk_io", "network")
_SATURATION_AXES: tuple[ResourceKind, ...] = ("cpu", "memory", "disk_io")
type _Resources = dict[ResourceKind, ResourceAssessment]


def _ra(kind: ResourceKind, status: ResourceStatus, *, coverage_gap: bool = False) -> ResourceAssessment:
    return ResourceAssessment(kind, status, confidence=ConfidenceNote(coverage_gap=coverage_gap))


def test_host_saturation_unmeasured_limited_to_saturation_axes():
    for gap_kind in _SATURATION_AXES:
        res: _Resources = {
            "cpu": _ra("cpu", "optimal"),
            "memory": _ra("memory", "optimal"),
            "disk_capacity": _ra("disk_capacity", "capacity_ok"),
            "disk_io": _ra("disk_io", "io_ok"),
            "network": _ra("network", "quality_ok"),
        }
        res[gap_kind] = _ra(gap_kind, res[gap_kind].status, coverage_gap=True)
        assert host_saturation_unmeasured(HostAssessment(resources=res)) is True

    res_non_sat: _Resources = {
        "cpu": _ra("cpu", "optimal"),
        "memory": _ra("memory", "optimal"),
        "disk_capacity": _ra("disk_capacity", "unmeasured", coverage_gap=True),
        "disk_io": _ra("disk_io", "io_ok"),
        "network": _ra("network", "unmeasured", coverage_gap=True),
    }
    assert host_saturation_unmeasured(HostAssessment(resources=res_non_sat)) is False

    res_clean: _Resources = {k: _ra(k, "optimal") for k in _ALL_KINDS}
    assert host_saturation_unmeasured(HostAssessment(resources=res_clean)) is False


def test_root_cause_display_no_under_is_empty():
    res: _Resources = {k: _ra(k, "optimal") for k in _ALL_KINDS}
    assert root_cause_display(HostAssessment(resources=res)) == ""


def test_root_cause_display_single_under_is_resource_name():
    res: _Resources = {
        "cpu": _ra("cpu", "under"),
        "memory": _ra("memory", "optimal"),
        "disk_capacity": _ra("disk_capacity", "capacity_ok"),
        "disk_io": _ra("disk_io", "io_ok"),
        "network": _ra("network", "quality_ok"),
    }
    assert root_cause_display(HostAssessment(resources=res)) == "CPU"


def test_root_cause_display_causal_combined():
    res: _Resources = {
        "cpu": _ra("cpu", "under"),
        "memory": _ra("memory", "under"),
        "disk_capacity": _ra("disk_capacity", "capacity_ok"),
        "disk_io": _ra("disk_io", "io_ok"),
        "network": _ra("network", "quality_ok"),
    }
    host = HostAssessment(resources=res, root_cause="memory", symptom_of_root=["disk_io", "cpu"])
    assert root_cause_display(host) == "메모리 (디스크 I/O·CPU 유발)"


def test_root_cause_display_multiple_independent():
    res: _Resources = {
        "cpu": _ra("cpu", "under"),
        "memory": _ra("memory", "optimal"),
        "disk_capacity": _ra("disk_capacity", "filling"),
        "disk_io": _ra("disk_io", "io_ok"),
        "network": _ra("network", "quality_ok"),
    }
    assert root_cause_display(HostAssessment(resources=res)) == "CPU·디스크 용량"


def test_assess_mount_capacity_none_when_total_unknown():
    assert assess_mount_capacity(None, None, 10.0, None, None, None) is None
    assert assess_mount_capacity(0, None, 10.0, None, None, None) is None


def test_assess_mount_capacity_byte_filling_exact_target():
    ms = assess_mount_capacity(100 * _GIB, 200 * _GIB, DISK_RUNWAY_DAYS - 1, None, None, None)
    assert isinstance(ms, MountSizing)
    assert ms.current_gib == 100
    assert ms.recommended_gib == 200
    assert ms.action == "increase"
    assert ms.estimate_quality == "exact"
    assert ms.note == ""


def test_assess_mount_capacity_byte_filling_floor_when_no_target():
    ms = assess_mount_capacity(100 * _GIB, None, DISK_RUNWAY_DAYS - 1, None, None, None)
    assert ms is not None
    assert ms.action == "increase"
    assert ms.estimate_quality == "floor"
    import math

    assert ms.recommended_gib == max(100, math.ceil(100 / (DISK_HEADROOM_TARGET_PCT / 100)))
    assert ms.recommended_gib == 143


def test_assess_mount_capacity_static_guard_byte_filling():
    ms = assess_mount_capacity(100 * _GIB, 150 * _GIB, None, DISK_STATIC_GUARD_PCT, None, None)
    assert ms is not None
    assert ms.action == "increase"
    assert ms.estimate_quality == "exact"
    assert ms.recommended_gib == 150


def test_assess_mount_capacity_inode_filling_keeps_with_note():
    ms = assess_mount_capacity(100 * _GIB, 200 * _GIB, None, 50.0, DISK_RUNWAY_DAYS - 1, None)
    assert ms is not None
    assert ms.action == "keep"
    assert ms.estimate_quality == "exact"
    assert ms.recommended_gib == ms.current_gib == 100
    assert "inode" in ms.note


def test_assess_mount_capacity_healthy_keeps():
    ms = assess_mount_capacity(100 * _GIB, None, None, 50.0, None, None)
    assert ms is not None
    assert ms.action == "keep"
    assert ms.estimate_quality == "exact"
    assert ms.recommended_gib == ms.current_gib == 100
    assert ms.note == ""
