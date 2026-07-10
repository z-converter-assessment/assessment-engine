"""os-aware 포화 helper 검증 — cpu_saturated / mem_saturated / disk_io_saturated (원칙 P2).

OS별 raw 신호를 통일 축으로 정규화하는 3 helper 의 판정·미관측(None) 처리. 호스트 종합 분류(rollup_host)·
판정 순서는 test_right_sizing_model.py 단일 진실 — 본 파일은 helper 단위(신호 -> bool|None)만.
"""

from assessment_engine.recommendation import (
    DISK_QUEUE_PER_DISK_SATURATION,
    RS_DISKIO_AWAIT_MS,
    WIN_PAGES_INPUT_SATURATION,
    ResourceStats,
    cpu_saturated,
    disk_io_saturated,
    mem_saturated,
)


def _stats(**overrides) -> ResourceStats:
    """기본 Linux 미포화 stats — 각 test 가 override 로 신호 활성화."""
    base: dict = {
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


def _win(**overrides) -> ResourceStats:
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
    # Gate0 dual-gate(v2): mem_saturated 는 이용률 p95 >= RS_MEM_UNDER_PCT(90) AND 페이징일 때만 포화.
    # 이용률 gate 를 통과시켜(mem_p95_pct=95) 페이징 신호 자체를 검증 — swap_used 는 그래도 무시됨.
    assert mem_saturated(_stats(mem_p95_pct=95.0, os_family="linux", swap_used=True, mem_swap_paging=False)) is False
    assert mem_saturated(_stats(mem_p95_pct=95.0, os_family="linux", swap_used=False, mem_swap_paging=True)) is True


def test_mem_saturated_windows_excludes_pagefile_uses_hardfault_rate():
    # Windows pagefile 상시 사용은 신호 아님 -> 하드폴트율(pages_input rate) 로만 판정.
    # Gate0 dual-gate(v2): 이용률 gate(mem_p95_pct=95 >= 90) 통과시켜 하드폴트율 임계 자체를 검증.
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
