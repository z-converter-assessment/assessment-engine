"""recommendation 도메인 커널 property-based 감사 (hypothesis).

수천 가짓수의 합성 ResourceStats 를 생성해 rollup_host/assess_* 출력의 불변식·합리성 oracle 을 검증한다.
목적은 회귀 고정이 아니라 입력 공간 전수 탐색으로 버그(모순·비합리·오탐)를 자동 발견하는 것.
실 fleet(짧은 이력·idle 다수)이 못 덮는 조합을 생성이 덮는다.
"""

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from assessment_engine import recommendation as R
from assessment_engine.recommendation import ResourceStats
from tests.hypothesis_scale import examples

_HOST_STATUS = {"under", "idle", "over", "optimal", "insufficient"}
_RES_KINDS = {"cpu", "memory", "disk_capacity", "disk_io", "network"}


def _opt(strat):
    """None 또는 값 (미측정 축 포함)."""
    return st.one_of(st.none(), strat)


_pct = st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False)
_cores = st.sampled_from([1, 2, 4, 8, 16, 32, 64])
_mb = st.sampled_from([512, 1024, 2048, 4096, 8192, 16384, 32768, 65536])


@st.composite
def stats_strategy(draw) -> ResourceStats:
    return ResourceStats(
        cpu_p95_pct=draw(_opt(_pct)),
        cpu_peak_pct=draw(_opt(_pct)),
        cpu_load_15m_max=draw(_opt(st.floats(0, 64, allow_nan=False))),
        cpu_cores=draw(_opt(_cores)),
        mem_p95_pct=draw(_opt(_pct)),
        swap_used=draw(st.booleans()),
        disk_used_pct=draw(_opt(_pct)),
        iowait_p95_pct=draw(_opt(_pct)),
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
    """구조 불변식 — 어떤 유효 입력에도 성립해야 함 (위반 = 확정 버그)."""
    host = R.rollup_host(stats)

    # 1. 5자원 축 전부 존재
    assert set(host.resources) == _RES_KINDS
    # 2. host_status 유효 enum
    assert host.host_status in _HOST_STATUS
    # 3. sizing_target/floor 는 있으면 양의 정수
    for a in host.resources.values():
        if a.sizing_target is not None:
            assert isinstance(a.sizing_target, int) and a.sizing_target > 0, (a.kind, a.sizing_target)
        if a.sizing_floor is not None:
            assert isinstance(a.sizing_floor, int) and a.sizing_floor > 0, (a.kind, a.sizing_floor)
    # 4. classify 정합 — classify_host == host_status_to_recommendation(rollup) (문서화된 단일 진실)
    assert R.classify_host(stats) == R.host_status_to_recommendation(host.host_status)


@settings(max_examples=examples(2000))
@given(stats_strategy())
def test_determinism(stats: ResourceStats):
    """동일 입력 -> 동일 출력 (순수 함수)."""
    h1, h2 = R.rollup_host(stats), R.rollup_host(stats)
    assert h1.host_status == h2.host_status
    assert {k: v.status for k, v in h1.resources.items()} == {k: v.status for k, v in h2.resources.items()}
    assert {k: v.sizing_target for k, v in h1.resources.items()} == {
        k: v.sizing_target for k, v in h2.resources.items()
    }


@settings(max_examples=examples(3000))
@given(stats_strategy())
def test_cpu_never_under_provision(stats: ResourceStats):
    """CPU under -> sizing_target 은 현재 코어 이상 (부족을 더 부족하게 만들지 않음). over -> 이하."""
    cpu = R.rollup_host(stats).resources["cpu"]
    if stats.cpu_cores is None or cpu.sizing_target is None:
        return
    if cpu.status == "under":
        assert cpu.sizing_target >= stats.cpu_cores, (stats.cpu_cores, cpu.sizing_target, cpu.triggers)
    if cpu.status == "over":
        assert cpu.sizing_target <= stats.cpu_cores, (stats.cpu_cores, cpu.sizing_target)


@settings(max_examples=examples(3000))
@given(stats_strategy())
def test_memory_sizing_bounded(stats: ResourceStats):
    """메모리 사이징 목표는 현재의 1.3배(near-peak<=100%/80% + floor 1.3x 상한) 를 넘지 않는다.

    넘으면 near-peak 통계 오염 또는 산식 결함(비현실적 과대 사이징).
    """
    mem = R.rollup_host(stats).resources["memory"]
    if stats.mem_total_mb is None or mem.sizing_target is None:
        return
    upper = math.ceil(stats.mem_total_mb * 1.3) + 2  # 반올림 여유
    assert mem.sizing_target <= upper, (stats.mem_total_mb, mem.sizing_target, mem.status, mem.triggers)
    if mem.status == "under":
        assert mem.sizing_target >= stats.mem_total_mb, (stats.mem_total_mb, mem.sizing_target)


@settings(max_examples=examples(3000))
@given(stats_strategy())
def test_idle_cpu_not_under_by_runqueue_artifact(stats: ResourceStats):
    """CPU 이용률이 거의 0인데 run-queue 만으로 under(코어 증설)로 판정하면 안 된다.

    procs_running 은 수집기 자신을 포함해 저활동 시스템에서 노이즈성으로 >= 1/core 가 된다.
    실제 CPU 병목은 이용률에도 나타나므로, 이용률이 무시할 수준(<= idle 임계)이고 steal 도 낮으면
    run-queue 포화 단독으로 코어를 늘리는 건 물리적 모순이다 (util 상관 dual-gate 필요).
    """
    cpu_util = stats.cpu_p95_pct
    steal = stats.cpu_steal_p95_pct or 0
    # 이용률이 실측됐고 유휴 수준이며 steal 도 낮은데 CPU under 인 경우
    if cpu_util is not None and cpu_util <= R.IDLE_CPU_P95_PCT and steal < 5:
        cpu = R.rollup_host(stats).resources["cpu"]
        assert cpu.status != "under", (
            f"idle CPU(util={cpu_util}%)인데 under — run_queue={stats.procs_running_p95}/"
            f"win_q={stats.cpu_run_queue_p95} cores={stats.cpu_cores} triggers={cpu.triggers}"
        )
