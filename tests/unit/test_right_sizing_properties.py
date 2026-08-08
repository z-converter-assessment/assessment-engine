import math
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.strategies import DrawFn

from assessment_engine.domain import right_sizing
from assessment_engine.domain.right_sizing import ResourceStats
from tests.hypothesis_scale import examples

_HOST_STATUS = {"under", "idle", "over", "optimal", "insufficient"}
_RES_KINDS = {"cpu", "memory", "disk_capacity", "disk_io", "network"}


def _opt(strat: st.SearchStrategy[Any]) -> st.SearchStrategy[Any]:
    return st.one_of(st.none(), strat)


_pct = st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False)
_cores = st.sampled_from([1, 2, 4, 8, 16, 32, 64])
_mb = st.sampled_from([512, 1024, 2048, 4096, 8192, 16384, 32768, 65536])


@st.composite
def stats_strategy(draw: DrawFn) -> ResourceStats:
    return ResourceStats(
        cpu_p95_pct=draw(_opt(_pct)),
        cpu_peak_pct=draw(_opt(_pct)),
        cpu_cores=draw(_opt(_cores)),
        mem_p95_pct=draw(_opt(_pct)),
        disk_used_pct=draw(_opt(_pct)),
        net_avg_kbytes_per_s=draw(_opt(st.floats(0, 1_000_000, allow_nan=False))),
        os_family=draw(st.sampled_from(["linux", "windows", None])),
        sample_sufficiency=draw(_opt(st.floats(0, 1.2, allow_nan=False))),
        disk_queue_p95=draw(_opt(st.floats(0, 50, allow_nan=False))),
        cpu_run_queue_p95=draw(_opt(st.floats(0, 30, allow_nan=False))),
        mem_pages_input_rate_p95=draw(_opt(st.floats(0, 2000, allow_nan=False))),
        cpu_percore_p95_max=draw(_opt(_pct)),
        procs_blocked_p95=draw(_opt(st.floats(0, 30, allow_nan=False))),
        procs_running_p95=draw(_opt(st.floats(0, 30, allow_nan=False))),
        mem_swap_paging=draw(st.booleans()),
        oom_occurred=draw(st.booleans()),
        mem_total_mb=draw(_opt(_mb)),
        mem_near_peak_pct=draw(_opt(_pct)),
        disk_await_p95_ms=draw(_opt(st.floats(0, 2000, allow_nan=False))),
        disk_iops_baseline=draw(_opt(st.floats(0, 10000, allow_nan=False))),
        disk_capacity_runway_days=draw(_opt(st.floats(0, 1000, allow_nan=False))),
        disk_inode_runway_days=draw(_opt(st.floats(0, 1000, allow_nan=False))),
        disk_inode_used_pct=draw(_opt(_pct)),
        disk_capacity_target_gb=draw(_opt(st.floats(0, 5000, allow_nan=False))),
        net_retrans_pct=draw(_opt(st.floats(0, 20, allow_nan=False))),
        net_drop_pct=draw(_opt(st.floats(0, 20, allow_nan=False))),
        conntrack_ratio=draw(_opt(st.floats(0, 1.5, allow_nan=False))),
        history_hours=draw(_opt(st.floats(0, 1000, allow_nan=False))),
        cpu_burst_ratio=draw(_opt(st.floats(0, 10, allow_nan=False))),
        util_trend_rising=draw(_opt(st.booleans())),
        cpu_steal_p95_pct=draw(_opt(_pct)),
    )


@settings(max_examples=examples(3000))
@given(stats_strategy())
def test_structural_invariants(stats: ResourceStats):
    host = right_sizing.rollup_host(stats)

    assert set(host.resources) == _RES_KINDS
    assert host.host_status in _HOST_STATUS
    for a in host.resources.values():
        if a.sizing_target is not None:
            assert isinstance(a.sizing_target, int), (a.kind, a.sizing_target)
            assert a.sizing_target > 0, (a.kind, a.sizing_target)
        if a.sizing_floor is not None:
            assert isinstance(a.sizing_floor, int), (a.kind, a.sizing_floor)
            assert a.sizing_floor > 0, (a.kind, a.sizing_floor)
    assert right_sizing.classify_host(stats) == right_sizing.host_status_to_recommendation(host.host_status)


@settings(max_examples=examples(2000))
@given(stats_strategy())
def test_determinism(stats: ResourceStats):
    h1, h2 = right_sizing.rollup_host(stats), right_sizing.rollup_host(stats)
    assert h1.host_status == h2.host_status
    assert {k: v.status for k, v in h1.resources.items()} == {k: v.status for k, v in h2.resources.items()}
    assert {k: v.sizing_target for k, v in h1.resources.items()} == {
        k: v.sizing_target for k, v in h2.resources.items()
    }


@settings(max_examples=examples(3000))
@given(stats_strategy())
def test_cpu_never_under_provision(stats: ResourceStats):
    cpu = right_sizing.rollup_host(stats).resources["cpu"]
    if stats.cpu_cores is None or cpu.sizing_target is None:
        return
    if cpu.status == "under":
        assert cpu.sizing_target >= stats.cpu_cores, (stats.cpu_cores, cpu.sizing_target, cpu.triggers)
    if cpu.status == "over":
        assert cpu.sizing_target <= stats.cpu_cores, (stats.cpu_cores, cpu.sizing_target)


@settings(max_examples=examples(3000))
@given(stats_strategy())
def test_memory_sizing_bounded(stats: ResourceStats):
    mem = right_sizing.rollup_host(stats).resources["memory"]
    if stats.mem_total_mb is None or mem.sizing_target is None:
        return
    upper = math.ceil(stats.mem_total_mb * 1.3) + 2
    assert mem.sizing_target <= upper, (stats.mem_total_mb, mem.sizing_target, mem.status, mem.triggers)
    if mem.status == "under":
        assert mem.sizing_target >= stats.mem_total_mb, (stats.mem_total_mb, mem.sizing_target)


@settings(max_examples=examples(3000))
@given(stats_strategy())
def test_idle_cpu_not_under_by_runqueue_artifact(stats: ResourceStats):
    cpu_util = stats.cpu_p95_pct
    steal = stats.cpu_steal_p95_pct or 0
    if cpu_util is not None and cpu_util <= right_sizing.IDLE_CPU_P95_PCT and steal < 5:
        cpu = right_sizing.rollup_host(stats).resources["cpu"]
        assert cpu.status != "under", (
            f"idle CPU(util={cpu_util}%)인데 under — run_queue={stats.procs_running_p95}/"
            f"win_q={stats.cpu_run_queue_p95} cores={stats.cpu_cores} triggers={cpu.triggers}"
        )
