"""os-aware 포화 helper 검증 — cpu_saturated / mem_saturated / disk_io_saturated (원칙 P2).

OS별 raw 신호를 통일 축으로 정규화하는 3 helper 의 판정·미관측(None) 처리. 호스트 종합 분류(rollup_host)·
판정 순서는 test_right_sizing_model.py 단일 진실 — 본 파일은 helper 단위(신호 -> bool|None)만.
"""

from typing import TYPE_CHECKING, Any

from assessment_engine.recommendation import (
    CPU_RUN_QUEUE_PER_CORE_SATURATION,
    DISK_QUEUE_PER_DISK_SATURATION,
    PROCS_RUNNING_PER_CORE_SATURATION,
    RS_CONNTRACK_SATURATION_RATIO,
    RS_DISK_HEADROOM_TARGET_PCT,
    RS_DISK_RUNWAY_DAYS,
    RS_DISK_STATIC_GUARD_PCT,
    RS_DISKIO_AWAIT_MS,
    RS_NET_DROP_PCT,
    RS_NET_MIN_TRAFFIC_KBPS,
    RS_NET_RETRANS_PCT,
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
    """기본 Linux 미포화 stats — 각 test 가 override 로 신호 활성화."""
    base: JsonObject = {
        "cpu_p95_pct": 40.0,
        "cpu_peak_pct": 50.0,
        "cpu_load_15m_max": 0.5,
        "procs_running_p95": 0.5,  # Linux CPU 포화 신호 — 미포화 기본
        "cpu_cores": 4,
        "mem_p95_pct": 60.0,
        "swap_used": False,
        "disk_used_pct": 50.0,
        "iowait_p95_pct": 5.0,
        "disk_await_p95_ms": 5.0,  # await 5ms < 20 -> io_ok(측정됨)
        "net_avg_kbytes_per_s": 100.0,
    }
    base.update(overrides)
    return ResourceStats(**base)


def _win(**overrides: Any) -> ResourceStats:
    """Windows 기본 stats — load/iowait/swap 는 Linux 축이라 무의미, run_queue/paging/disk_queue 로 판정."""
    base = {
        "os_family": "windows",
        "cpu_load_15m_max": None,  # loadavg OS 부재
        "iowait_p95_pct": None,  # iowait OS 부재
        "disk_await_p95_ms": None,  # Windows await 미발행 -> disk_queue 폴백으로 판정
        "swap_used": True,  # pagefile baseline (saturation 신호 아님)
    }
    base.update(overrides)
    return _stats(**base)


# ─── 메모리 포화 — Linux page-out(정적 점유 아님) / Windows 하드폴트율 (원칙 P2) ───


def test_mem_saturated_linux_uses_page_out_not_static_swap():
    # Linux 메모리 포화 = active page-out(mem_swap_paging), 정적 스왑 점유(swap_used) 아님.
    # swappiness 로 여유 RAM 에도 유휴 페이지 스왑아웃하므로 점유는 압박 신호가 아님.
    # Gate0 dual-gate: mem_saturated 는 이용률 p95 >= RS_MEM_UNDER_PCT(90) AND 페이징일 때만 포화.
    # 이용률 gate 를 통과시켜(mem_p95_pct=95) 페이징 신호 자체를 검증 — swap_used 는 그래도 무시됨.
    assert mem_saturated(_stats(mem_p95_pct=95.0, os_family="linux", swap_used=True, mem_swap_paging=False)) is False
    assert mem_saturated(_stats(mem_p95_pct=95.0, os_family="linux", swap_used=False, mem_swap_paging=True)) is True


def test_mem_saturated_windows_excludes_pagefile_uses_hardfault_rate():
    # Windows pagefile 상시 사용은 신호 아님 -> 하드폴트율(pages_input rate) 로만 판정.
    # Gate0 dual-gate: 이용률 gate(mem_p95_pct=95 >= 90) 통과시켜 하드폴트율 임계 자체를 검증.
    assert mem_saturated(_win(mem_p95_pct=95.0, mem_pages_input_rate_p95=WIN_PAGES_INPUT_SATURATION)) is True
    assert mem_saturated(_win(mem_p95_pct=95.0, mem_pages_input_rate_p95=10.0)) is False  # < 20
    assert mem_saturated(_win(mem_p95_pct=95.0, mem_pages_input_rate_p95=None)) is None  # perflib 미발행 -> 미관측


# ─── CPU 포화 — Linux 실행 큐 / Windows Processor Queue Length (원칙 P2) ───


def test_cpu_saturated_os_aware():
    # dual-gate: 실행 큐 포화 AND util 실제 높음(>= under 임계) — 저활동 procs_running 노이즈(수집기 포함) 배제
    # Linux: procs_running/cores >= 1.0 AND util 높음
    assert cpu_saturated(_stats(procs_running_p95=4.0, cpu_cores=4, cpu_p95_pct=85.0)) is True
    assert cpu_saturated(_stats(procs_running_p95=4.0, cpu_cores=4, cpu_p95_pct=10.0)) is False  # 저활동=노이즈
    assert cpu_saturated(_stats(procs_running_p95=1.0, cpu_cores=4, cpu_p95_pct=85.0)) is False  # 실행큐 미달
    assert cpu_saturated(_stats(procs_running_p95=4.0, cpu_cores=4, cpu_p95_pct=None)) is True  # util 미측정=신뢰
    assert cpu_saturated(_stats(procs_running_p95=None)) is None  # 미관측
    # Windows: run queue/cores >= 2 AND util 높음
    assert cpu_saturated(_win(cpu_run_queue_p95=8.0, cpu_cores=4, cpu_p95_pct=85.0)) is True  # 2.0 >= 2
    assert cpu_saturated(_win(cpu_run_queue_p95=4.0, cpu_cores=4, cpu_p95_pct=85.0)) is False  # 1.0 < 2
    assert cpu_saturated(_win(cpu_run_queue_p95=None)) is None  # perflib 미발행
    # cores 0/None -> 미관측 (div-by-zero 회피)
    assert cpu_saturated(_stats(cpu_cores=None, procs_running_p95=100.0)) is None


# ─── 디스크 I/O 포화 — await 통일(양 OS), Windows await 미배선 시 큐 폴백 (원칙 P2) ───


def test_disk_io_saturated_os_aware():
    assert disk_io_saturated(_stats(disk_await_p95_ms=RS_DISKIO_AWAIT_MS + 1)) is True
    assert disk_io_saturated(_stats(disk_await_p95_ms=None)) is None
    # Windows await 있으면 await 우선
    assert disk_io_saturated(_win(disk_await_p95_ms=RS_DISKIO_AWAIT_MS + 1)) is True
    # Windows await 미배선 -> 큐 깊이 폴백
    assert disk_io_saturated(_win(disk_queue_p95=DISK_QUEUE_PER_DISK_SATURATION)) is True
    assert disk_io_saturated(_win(disk_queue_p95=1.0)) is False
    assert disk_io_saturated(_win(disk_queue_p95=None)) is None


# ─── 포화 지수 (실시간 aggregate 통합 축 — 임계 정규화로 OS 무관 한 지수) ───


def test_cpu_saturation_index_os_aware():
    # Linux 임계 1.0 — (run_queue/cores)/1.0. cores 만큼 run_queue 면 지수 1.0(포화선).
    assert cpu_saturation_index(4.0, 4, "linux") == (4.0 / 4) / PROCS_RUNNING_PER_CORE_SATURATION
    assert cpu_saturation_index(4.0, 4, "linux") == 1.0
    assert cpu_saturation_index(2.0, 4, None) == 0.5  # os None -> Linux 의미
    # Windows 임계 2.0 — 같은 run_queue 라도 지수는 절반(모집단 차이 정규화).
    assert cpu_saturation_index(4.0, 4, "windows") == (4.0 / 4) / CPU_RUN_QUEUE_PER_CORE_SATURATION
    assert cpu_saturation_index(4.0, 4, "windows") == 0.5
    assert cpu_saturation_index(8.0, 4, "windows") == 1.0  # 2.0/core /2.0 = 1.0 포화선
    # 측정 불가 -> None (run_queue None · cores 0 · cores None)
    assert cpu_saturation_index(None, 4, "linux") is None
    assert cpu_saturation_index(4.0, 0, "linux") is None
    assert cpu_saturation_index(4.0, None, "linux") is None


def test_disk_io_saturation_index_await_priority_queue_fallback():
    # await 우선(양 OS 통일) — await/RS_DISKIO_AWAIT_MS. 임계값이면 지수 1.0.
    assert disk_io_saturation_index(RS_DISKIO_AWAIT_MS, None, "linux") == 1.0
    assert disk_io_saturation_index(40.0, None, "linux") == 40.0 / RS_DISKIO_AWAIT_MS
    # await 있으면 Windows 라도 await 우선(큐 무시).
    assert disk_io_saturation_index(40.0, 100.0, "windows") == 40.0 / RS_DISKIO_AWAIT_MS
    # await None + Windows -> 큐 깊이 폴백 / DISK_QUEUE_PER_DISK_SATURATION.
    assert disk_io_saturation_index(None, DISK_QUEUE_PER_DISK_SATURATION, "windows") == 1.0
    assert disk_io_saturation_index(None, 4.0, "windows") == 4.0 / DISK_QUEUE_PER_DISK_SATURATION
    # await None + Linux(큐 있어도) -> None (Linux 는 큐 폴백 없음).
    assert disk_io_saturation_index(None, 4.0, "linux") is None
    # 둘 다 None -> None.
    assert disk_io_saturation_index(None, None, "windows") is None


# ─── 실시간 메모리 압박 — 하드폴트(major fault) rate 기반, os-aware 임계 ───


def test_net_signal_active_low_traffic_gate():
    """실시간 네트워크 혼잡 신호(4-1) — assess_network 와 동일 임계·저트래픽 게이트.

    retrans/drop 은 트래픽 < RS_NET_MIN_TRAFFIC_KBPS 면 억제(저트래픽 소수 이벤트 지배 방지),
    conntrack 은 트래픽 무관 절대 신호라 게이트 제외.
    """
    hi = RS_NET_MIN_TRAFFIC_KBPS + 1.0
    lo = RS_NET_MIN_TRAFFIC_KBPS - 1.0
    over_retrans = RS_NET_RETRANS_PCT + 1.0
    over_drop = RS_NET_DROP_PCT + 0.5
    over_ct = RS_CONNTRACK_SATURATION_RATIO + 0.05
    # 트래픽 충분 + retrans/drop 초과 -> 발화
    assert net_signal_active(over_retrans, None, None, hi) is True
    assert net_signal_active(None, over_drop, None, hi) is True
    # 저트래픽이면 retrans/drop 초과여도 억제
    assert net_signal_active(over_retrans, over_drop, None, lo) is False
    # conntrack 은 저트래픽에도 발화(게이트 제외)
    assert net_signal_active(None, None, over_ct, lo) is True
    # net rate 미상(None)이면 저트래픽 게이트 미적용 -> 초과 신호 신뢰
    assert net_signal_active(over_retrans, None, None, None) is True
    # 임계 이하 + 정상 conntrack -> 미발화
    assert net_signal_active(0.1, 0.1, 0.1, hi) is False


def test_mem_pressure_active_os_aware():
    # rate None -> 미측정, 압박 아님(False).
    assert mem_pressure_active(None, "linux") is False
    assert mem_pressure_active(None, "windows") is False
    # Linux — refault rate > 0 이면 압박.
    assert mem_pressure_active(0.1, "linux") is True
    assert mem_pressure_active(0.0, "linux") is False
    assert mem_pressure_active(0.1, None) is True  # os None -> Linux 의미
    # Windows — Pages Input/sec >= WIN_PAGES_INPUT_SATURATION(20).
    assert mem_pressure_active(WIN_PAGES_INPUT_SATURATION, "windows") is True
    assert mem_pressure_active(10.0, "windows") is False  # < 20
    assert mem_pressure_active(0.1, "windows") is False  # Linux 라면 압박이나 Windows 임계 미달


# ─── 포화 축 미관측 판정 — cpu/memory/disk_io 한정 (network·disk_capacity 제외) ───


def _ra(kind: ResourceKind, status: ResourceStatus, *, coverage_gap: bool = False) -> ResourceAssessment:
    return ResourceAssessment(kind, status, confidence=ConfidenceNote(coverage_gap=coverage_gap))


def test_host_saturation_unmeasured_limited_to_saturation_axes():
    # 포화 축(cpu/memory/disk_io) 어느 하나라도 coverage_gap 이면 True.
    for gap_kind in ("cpu", "memory", "disk_io"):
        res = {
            "cpu": _ra("cpu", "optimal"),
            "memory": _ra("memory", "optimal"),
            "disk_capacity": _ra("disk_capacity", "capacity_ok"),
            "disk_io": _ra("disk_io", "io_ok"),
            "network": _ra("network", "quality_ok"),
        }
        res[gap_kind] = _ra(gap_kind, res[gap_kind].status, coverage_gap=True)
        assert host_saturation_unmeasured(HostAssessment(resources=res)) is True

    # 포화 축은 전부 측정됐고 network·disk_capacity 만 미측정 -> False(포화 축 아님, 제외).
    res_non_sat = {
        "cpu": _ra("cpu", "optimal"),
        "memory": _ra("memory", "optimal"),
        "disk_capacity": _ra("disk_capacity", "unmeasured", coverage_gap=True),
        "disk_io": _ra("disk_io", "io_ok"),
        "network": _ra("network", "unmeasured", coverage_gap=True),
    }
    assert host_saturation_unmeasured(HostAssessment(resources=res_non_sat)) is False

    # 전 자원 온전 -> False.
    res_clean = {k: _ra(k, "optimal") for k in ("cpu", "memory", "disk_capacity", "disk_io", "network")}
    assert host_saturation_unmeasured(HostAssessment(resources=res_clean)) is False


# ─── 근본원인 칼럼 표시 — 4 포맷 분기 (under_prescription 정합) ───


def test_root_cause_display_no_under_is_empty():
    res = {k: _ra(k, "optimal") for k in ("cpu", "memory", "disk_capacity", "disk_io", "network")}
    assert root_cause_display(HostAssessment(resources=res)) == ""


def test_root_cause_display_single_under_is_resource_name():
    # 단일 부족 -> 자원명만(원인 자명). cpu under 하나.
    res = {
        "cpu": _ra("cpu", "under"),
        "memory": _ra("memory", "optimal"),
        "disk_capacity": _ra("disk_capacity", "capacity_ok"),
        "disk_io": _ra("disk_io", "io_ok"),
        "network": _ra("network", "quality_ok"),
    }
    assert root_cause_display(HostAssessment(resources=res)) == "CPU"


def test_root_cause_display_causal_combined():
    # 인과 결합 -> "root (증상 유발)". 메모리발 + disk_io·cpu 증상.
    res = {
        "cpu": _ra("cpu", "under"),
        "memory": _ra("memory", "under"),
        "disk_capacity": _ra("disk_capacity", "capacity_ok"),
        "disk_io": _ra("disk_io", "io_ok"),
        "network": _ra("network", "quality_ok"),
    }
    host = HostAssessment(resources=res, root_cause="memory", symptom_of_root=["disk_io", "cpu"])
    assert root_cause_display(host) == "메모리 (디스크 I/O·CPU 유발)"


def test_root_cause_display_multiple_independent():
    # 복수 독립(단일 root·증상 없음) -> "·" 나열, _UNDER_ORDER 순(cpu 먼저 아님 — memory,cpu,...).
    res = {
        "cpu": _ra("cpu", "under"),
        "memory": _ra("memory", "optimal"),
        "disk_capacity": _ra("disk_capacity", "filling"),
        "disk_io": _ra("disk_io", "io_ok"),
        "network": _ra("network", "quality_ok"),
    }
    assert root_cause_display(HostAssessment(resources=res)) == "CPU·디스크 용량"


# ─── 마운트 용량 사이징 — 분기별 outcome (per-mount, 축소 없음) ───


def test_assess_mount_capacity_none_when_total_unknown():
    # total_bytes 미상/0 -> 사이징 불가(None).
    assert assess_mount_capacity(None, None, 10.0, None, None, None) is None
    assert assess_mount_capacity(0, None, 10.0, None, None, None) is None


def test_assess_mount_capacity_byte_filling_exact_target():
    # 소진 임박(runway < 30) + 목표 있음 -> increase, exact, rec=max(current, ceil(target/GiB)).
    ms = assess_mount_capacity(100 * _GIB, 200 * _GIB, RS_DISK_RUNWAY_DAYS - 1, None, None, None)
    assert isinstance(ms, MountSizing)
    assert ms.current_gib == 100
    assert ms.recommended_gib == 200
    assert ms.action == "increase"
    assert ms.estimate_quality == "exact"
    assert ms.note == ""


def test_assess_mount_capacity_byte_filling_floor_when_no_target():
    # 소진 임박인데 목표 산출 불가 -> floor: max(current, ceil(current / (headroom%/100))).
    ms = assess_mount_capacity(100 * _GIB, None, RS_DISK_RUNWAY_DAYS - 1, None, None, None)
    assert ms is not None
    assert ms.action == "increase"
    assert ms.estimate_quality == "floor"
    import math

    assert ms.recommended_gib == max(100, math.ceil(100 / (RS_DISK_HEADROOM_TARGET_PCT / 100)))
    assert ms.recommended_gib == 143


def test_assess_mount_capacity_static_guard_byte_filling():
    # runway None + used_pct >= 정적 가드(85) -> byte_filling. 목표 있으면 increase exact.
    ms = assess_mount_capacity(100 * _GIB, 150 * _GIB, None, RS_DISK_STATIC_GUARD_PCT, None, None)
    assert ms is not None
    assert ms.action == "increase"
    assert ms.estimate_quality == "exact"
    assert ms.recommended_gib == 150


def test_assess_mount_capacity_inode_filling_keeps_with_note():
    # inode 소진(byte 미충족) -> keep + advisory note(용량 확장으로 안 풀림). recommended == current.
    ms = assess_mount_capacity(100 * _GIB, 200 * _GIB, None, 50.0, RS_DISK_RUNWAY_DAYS - 1, None)
    assert ms is not None
    assert ms.action == "keep"
    assert ms.estimate_quality == "exact"
    assert ms.recommended_gib == ms.current_gib == 100
    assert "inode" in ms.note


def test_assess_mount_capacity_healthy_keeps():
    # 임박 없음 -> keep exact, note 없음, recommended == current.
    ms = assess_mount_capacity(100 * _GIB, None, None, 50.0, None, None)
    assert ms is not None
    assert ms.action == "keep"
    assert ms.estimate_quality == "exact"
    assert ms.recommended_gib == ms.current_gib == 100
    assert ms.note == ""
