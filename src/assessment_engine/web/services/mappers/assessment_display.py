"""right_sizing 판정 -> 표시·계약 원자 (P2).

포화 축과 신뢰도 노트 둘 다 판정 자체는 도메인 helper(`cpu_saturated`·`ConfidenceNote` 등)가 내고,
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
    """os-aware 포화 축 표시 원자 — single_report 포화 축 카드·attention capacity 지표 공용 (P2 표현 단일 소스).

    axis: os-neutral 축 이름(CPU 포화/메모리 포화/디스크 I/O). signal: 해당 OS 측정 신호 이름. value: 형식화
    값('N/A'=미측정). threshold: 임계 표기. measured: 실측 여부. 포화 여부(bool)는 right_sizing helper 별도 —
    본 원자는 표시값만(포맷·라벨·임계 문자열을 한 곳에서 결정해 카드/표 간 표기 drift 차단).
    """

    axis: str
    signal: str
    value: str
    threshold: str
    measured: bool
    # 단일 게이트 crossing — 이 raw 신호값이 자기 임계를 넘었는가(값·임계와 같은 자리 산출, 단일 진실). 종합
    # 판정(dual-gate: 이용률 AND 포화)은 cpu_saturated 등 별도 — 축 표시는 신호 자체가 임계 넘었는지(비정상)를 보임.
    crossed: bool = False


def saturation_axis_displays(stats: right_sizing.ResourceStats) -> list[SaturationAxisDisplay]:
    """포화 3축(CPU·메모리·디스크 I/O) os-aware 표시값 — [cpu, mem, disk] 순 (report·attention 단일 진실, P2).

    OS별 측정 신호·형식화·임계 표기를 한 곳에서 결정 — 판정(saturated bool)은 cpu_saturated/mem_saturated/
    disk_io_saturated helper 몫(임계 재계산 없음). 값 스케일은 stats(=build_resource_stats) raw 그대로.
    """
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
                f"W {rq:.2f}"
                if rq is not None
                else "N/A",  # W 태그 — Linux(실행큐)와 의미·임계 달라 값만으론 구분 불가
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
            f"L {rq:.2f}" if rq is not None else "N/A",  # L 태그 — Windows(Processor Queue)와 의미·임계 달라 구분
            f">= {rec.PROCS_RUNNING_PER_CORE_SATURATION:g}",
            rq is not None,
            crossed=rq is not None and rq >= rec.PROCS_RUNNING_PER_CORE_SATURATION,
        ),
        SaturationAxisDisplay(
            "메모리 포화",
            "swap page-out",
            "L 발생" if stats.mem_swap_paging else "L 없음",
            "발생 시",
            True,
            crossed=stats.mem_swap_paging,
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
    """호스트 confidence 단서 라벨 (rollup_host 기반) — 자원별 ConfidenceNote 를 호스트 단위 OR 종합.

    coverage_gap(포화 축 미관측)·low_precision(이력<30h·버스티)를 호스트 단위로 노출한다. biased(virtio 구조
    편향)는 disk_io 가 상시 True 라 표시 노이즈여서 노트에서 빼고 다운사이즈 게이트 안에서만 쓴다.
    report·attention 공용.

    '창 대비 관측 부족'은 30h 절대 바닥(low_precision)과 별개 축 — 선택 창을 다 못 덮으면(예: 14일 창에
    2일 데이터) sample_sufficiency 가 낮아 발화. 짧은 창으로 판정 시 저커버리지를 정직하게 노출.
    """
    notes: list[str] = []
    if right_sizing.host_saturation_unmeasured(host):  # 포화 축(cpu·mem·disk_io) 한정 — 용량·네트워크 제외
        notes.append("포화 수치 미관측")
    if any(r.confidence.low_precision for r in host.resources.values()):
        notes.append("표본 부족")
    if host.sample_sufficiency is not None and host.sample_sufficiency < right_sizing.DOWNSIZE_MIN_SUFFICIENCY:
        notes.append("창 대비 관측 부족")
    return notes


def resource_confidence_notes(c: right_sizing.ConfidenceNote) -> list[str]:
    """자원별 신뢰도 하향 사유 — biased(virtio 구조 편향)는 상시라 노이즈로 제외. right-sizing/assessment API 공용."""
    notes: list[str] = []
    if c.low_precision:
        notes.append("표본 부족")
    if c.coverage_gap:
        notes.append("포화 수치 미관측")
    if c.nonstationary:
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
    """자원별 포화 신호 — os-aware raw 수치(계약용 numeric). right-sizing/assessment API 공용(P2 단일 진실)."""
    win = stats.os_family == "windows"
    if kind == "cpu":
        rq = stats.cpu_run_queue_p95 if win else stats.procs_running_p95
        val = right_sizing.cpu_saturation_index(rq, stats.cpu_cores, stats.os_family)
        thr = right_sizing.CPU_RUN_QUEUE_PER_CORE_SATURATION if win else right_sizing.PROCS_RUNNING_PER_CORE_SATURATION
        sig = "Processor Queue Length/core" if win else "run queue (procs_running)/core"
        return _saturation_dict(sig, val, thr, "per_core", right_sizing.cpu_saturated(stats))
    if kind == "memory":
        if win:
            return _saturation_dict(
                "Pages Input/sec",
                stats.mem_pages_input_rate_p95,
                right_sizing.WIN_PAGES_INPUT_SATURATION,
                "per_sec",
                right_sizing.mem_saturated(stats),
            )
        # Linux swap page-out 은 발생 이벤트(수치 없음) — 판정은 saturated 로.
        sat = right_sizing.mem_saturated(stats)
        return {
            "signal": "swap page-out",
            "value": None,
            "threshold": None,
            "unit": "event",
            "measured": sat is not None,
            "saturated": sat,
        }
    # disk_io — await 우선(양 OS), 구세대 viostor 만 큐 폴백.
    if stats.disk_await_p95_ms is not None:
        return _saturation_dict(
            "await",
            stats.disk_await_p95_ms,
            right_sizing.DISKIO_AWAIT_MS,
            "ms",
            right_sizing.disk_io_saturated(stats),
        )
    if stats.disk_queue_p95 is not None:
        return _saturation_dict(
            "Avg Disk Queue Length",
            stats.disk_queue_p95,
            right_sizing.DISK_QUEUE_PER_DISK_SATURATION,
            "queue",
            right_sizing.disk_io_saturated(stats),
        )
    return {
        "signal": "await",
        "value": None,
        "threshold": right_sizing.DISKIO_AWAIT_MS,
        "unit": "ms",
        "measured": False,
        "saturated": right_sizing.disk_io_saturated(stats),
    }
