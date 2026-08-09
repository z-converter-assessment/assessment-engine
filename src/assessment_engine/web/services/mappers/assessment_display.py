"""right_sizing 판정 -> 표시·계약 원자 (P2).

포화 축과 신뢰도 노트 둘 다 판정 자체는 도메인 helper(`is_cpu_saturated`·`ConfidenceNote` 등)가 내고,
여기서는 그 결과를 화면·API 가 소비할 형태로만 바꾼다 — 임계 재계산 없음 (#E3).

두 표기 경로가 있다. 화면 카드는 형식화 문자열(`SaturationAxisDisplay`), 계약 API 는 raw numeric
(`saturation_block`) 이다. 같은 신호를 쓰되 소비자가 요구하는 형태가 달라 나뉜다.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from assessment_engine.domain import right_sizing

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject


@dataclass
class SaturationAxisDisplay:
    """os-aware 포화 축 표시 원자 — 포맷·라벨·임계 문자열을 한 곳에서 결정해 카드/표 간 표기 drift 를 막는다.

    표시값만 담는다 — 포화 여부(bool) 판정은 right_sizing helper 몫이다. value 는 미측정이면 'N/A'.
    """

    axis: str
    signal: str
    value: str
    threshold: str
    measured: bool

    crossed: bool = False


def saturation_axis_displays(stats: right_sizing.ResourceStats) -> list[SaturationAxisDisplay]:
    rec = right_sizing
    cores = stats.cpu_cores
    await_ms = stats.disk_await_p95_ms
    disk_over = await_ms is not None and await_ms > rec.DISKIO_AWAIT_MS
    if stats.os_family == "windows":
        rq = stats.cpu_run_queue_p95 / cores if stats.cpu_run_queue_p95 is not None and cores else None
        pages = stats.mem_pages_input_rate_p95
        return [
            SaturationAxisDisplay(
                "CPU 포화",
                "Processor Queue Length / core",
                f"W {rq:.2f}" if rq is not None else "N/A",
                f">= {rec.CPU_RUN_QUEUE_PER_CORE_SATURATION:g}",
                rq is not None,
                crossed=rq is not None and rq >= rec.CPU_RUN_QUEUE_PER_CORE_SATURATION,
            ),
            SaturationAxisDisplay(
                "메모리 포화",
                "Memory Pages Input/sec p95",
                f"W {pages:.0f}/s" if pages is not None else "N/A",
                f">= {rec.WIN_PAGES_INPUT_SATURATION:g}/s",
                pages is not None,
                crossed=pages is not None and pages >= rec.WIN_PAGES_INPUT_SATURATION,
            ),
            SaturationAxisDisplay(
                "디스크 I/O 포화",
                "await p95 (IOCTL ReadTime/WriteTime)",
                f"{await_ms:.0f}ms" if await_ms is not None else "N/A",
                f"> {rec.DISKIO_AWAIT_MS:g}ms",
                await_ms is not None,
                crossed=disk_over,
            ),
        ]
    rq = stats.procs_running_p95 / cores if stats.procs_running_p95 is not None and cores else None
    return [
        SaturationAxisDisplay(
            "CPU 포화",
            "run queue (procs_running) / core",
            f"L {rq:.2f}" if rq is not None else "N/A",
            f">= {rec.PROCS_RUNNING_PER_CORE_SATURATION:g}",
            rq is not None,
            crossed=rq is not None and rq >= rec.PROCS_RUNNING_PER_CORE_SATURATION,
        ),
        SaturationAxisDisplay(
            "메모리 포화",
            "swap page-out",
            "L 발생" if stats.mem_swap_paging else ("L 없음" if stats.mem_swap_paging is False else "N/A"),
            "발생 시",
            stats.mem_swap_paging is not None,
            crossed=stats.mem_swap_paging is True,
        ),
        SaturationAxisDisplay(
            "디스크 I/O 포화",
            "await p95",
            f"{await_ms:.0f}ms" if await_ms is not None else "N/A",
            f"> {rec.DISKIO_AWAIT_MS:g}ms",
            await_ms is not None,
            crossed=disk_over,
        ),
    ]


def build_host_confidence_notes(host: right_sizing.HostAssessment) -> list[str]:
    notes: list[str] = []
    if right_sizing.has_unmeasured_saturation(host):
        notes.append("포화 수치 미관측")
    if any(r.confidence.insufficient_history for r in host.resources.values()):
        notes.append("관측 이력 부족")
    if any(r.confidence.high_utilization_variability for r in host.resources.values()):
        notes.append("이용률 변동성 큼")
    if host.sample_sufficiency is not None and host.sample_sufficiency < right_sizing.DOWNSIZE_MIN_SAMPLE_COVERAGE:
        notes.append("창 대비 관측 부족")
    return notes


def resource_confidence_notes(c: right_sizing.ConfidenceNote) -> list[str]:
    notes: list[str] = []
    if c.insufficient_history:
        notes.append("관측 이력 부족")
    if c.high_utilization_variability:
        notes.append("이용률 변동성 큼")
    if c.coverage_gap:
        notes.append("포화 수치 미관측")
    if c.rising_utilization_trend:
        notes.append("상승 추세")
    return notes


def _saturation_dict(
    signal: str, value: float | None, threshold: float | None, unit: str, saturated: bool | None
) -> JsonObject:
    """포화 신호 1건 — raw numeric(파싱 계약). network.signals 와 동형, value 미측정 시 null."""
    return {
        "signal": signal,
        "value": round(value, 2) if value is not None else None,
        "threshold": threshold,
        "unit": unit,
        "measured": value is not None,
        "saturated": saturated,
    }


def saturation_block(kind: str, stats: right_sizing.ResourceStats) -> JsonObject:
    """자원별 포화 신호 — os-aware raw 수치 (계약용 numeric)."""
    win = stats.os_family == "windows"
    if kind == "cpu":
        rq = stats.cpu_run_queue_p95 if win else stats.procs_running_p95
        val = rq / stats.cpu_cores if rq is not None and stats.cpu_cores else None
        thr = right_sizing.CPU_RUN_QUEUE_PER_CORE_SATURATION if win else right_sizing.PROCS_RUNNING_PER_CORE_SATURATION
        sig = "Processor Queue Length/core" if win else "run queue (procs_running)/core"
        return _saturation_dict(sig, val, thr, "per_core", right_sizing.is_cpu_saturated(stats))
    if kind == "memory":
        if win:
            return _saturation_dict(
                "Pages Input/sec",
                stats.mem_pages_input_rate_p95,
                right_sizing.WIN_PAGES_INPUT_SATURATION,
                "per_sec",
                right_sizing.is_memory_saturated(stats),
            )
        # Linux swap page-out 은 발생 이벤트(수치 없음) — 판정은 saturated 로.
        sat = right_sizing.is_memory_saturated(stats)
        return {
            "signal": "swap page-out",
            "value": None,
            "threshold": None,
            "unit": "event",
            "measured": sat is not None,
            "saturated": sat,
        }

    if stats.disk_await_p95_ms is not None:
        return _saturation_dict(
            "await",
            stats.disk_await_p95_ms,
            right_sizing.DISKIO_AWAIT_MS,
            "ms",
            right_sizing.is_disk_io_saturated(stats),
        )
    return {
        "signal": "await",
        "value": None,
        "threshold": right_sizing.DISKIO_AWAIT_MS,
        "unit": "ms",
        "measured": False,
        "saturated": right_sizing.is_disk_io_saturated(stats),
    }
