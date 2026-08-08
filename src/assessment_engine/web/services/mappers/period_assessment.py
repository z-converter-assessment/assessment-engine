"""서버 세부 '최근 N일' 평가 카드 — 자원별 이용률·포화 2축 + 신뢰도 보완 신호 (P2).

보고서 표(`report.py`)와 주제가 다르다. 저쪽은 여러 호스트를 한 표에 세우는 행 변환이고, 여기는 호스트
하나를 자원 축으로 펼치는 카드다.

판정은 전부 `right_sizing` 도메인 helper 를 경유한다. 임계를 여기서 다시 해석하면 같은 호스트가
목록·보고서·카드에서 다른 상태로 보인다 (#E3).
"""

from typing import TYPE_CHECKING

from assessment_engine.domain import right_sizing
from assessment_engine.web.services.mappers.assessment_display import SaturationAxisDisplay, saturation_axis_displays
from assessment_engine.web.services.mappers.constants import _DONUT_SEGMENT_DEFS
from assessment_engine.web.view_models.metric import (
    PeriodAssessment,
    PeriodErrorRow,
    PeriodExtraGroup,
    PeriodResource,
    PeriodSignalRow,
)

if TYPE_CHECKING:
    from assessment_engine.web.view_models.metric import ErrorSignal


def _pct_str(v: float | None) -> str:
    return f"{v:.1f}%" if v is not None else "N/A"


_VERDICT_LABEL = {
    "under": "부족",
    "over": "과다",
    "filling": "용량 임박",
    "io_bound": "I/O 병목",
    "congested": "혼잡",
    "idle": "유휴",
    "unmeasured": "미측정",
    "insufficient": "표본 부족",
}
_VERDICT_COLOR = {
    "under": "#dc2626",
    "filling": "#dc2626",
    "io_bound": "#dc2626",
    "congested": "#d97706",
    "over": "var(--color-title)",
}


def _verdict(status: str) -> tuple[str, str]:
    return _VERDICT_LABEL.get(status, "정상"), _VERDICT_COLOR.get(status, "#94a3b8")


def _period_error_rows(errors: list[ErrorSignal]) -> list[PeriodErrorRow]:
    """ErrorSignal -> 카드 표시행 (배지 precompute).

    not_applicable 은 이 OS 가 구조적으로 못 내는 신호(예 Windows EDAC)다 — no_data 와 구분해
    "나중에 나타날 값"으로 오인시키지 않는다.
    """
    rows: list[PeriodErrorRow] = []
    for e in errors:
        if e.state == "occurred":
            text, cls = (f"{e.count}건" if e.count is not None else "발생"), "badge-danger"
            note = e.context or ""
        elif e.state == "clean":
            text, cls, note = "이상 없음", "badge-ok", ""
        elif e.state == "not_applicable":
            text, cls, note = "N/A", "badge-muted", ""
        else:
            text, cls, note = "수집 대기", "badge-muted", ""

        sizing = "메모리 자원 부족" if (e.key == "mem_oom" and e.state == "occurred") else ""
        rows.append(
            PeriodErrorRow(key=e.key, label=e.label, badge_text=text, badge_class=cls, note=note, sizing_signal=sizing)
        )
    return rows


def _extra_row(
    label: str, val: float | None, unit: str, thr: float | None = None, over: bool = False
) -> PeriodSignalRow:
    value = f"{val:.1f}{unit}" if val is not None else "N/A"
    threshold = f"임계 {thr:g}{unit}" if thr is not None else ""
    return PeriodSignalRow(label=label, value=value, threshold=threshold, over=over, measured=val is not None)


def _confidence_rows(stats: right_sizing.ResourceStats) -> list[PeriodSignalRow]:
    """관측 시간·표본 충분성 — 값은 host 공통이라 자원 간 동일하다 (자원마다 반복 노출이 설계 의도)."""
    rec = right_sizing
    hours = stats.history_hours
    suff = stats.sample_sufficiency
    return [
        PeriodSignalRow(
            label="관측 시간",
            value=(f"{hours:.0f}h" if hours is not None else "N/A"),
            threshold=f"최소 {rec.CONFIDENCE_MIN_HOURS:g}h",
            measured=hours is not None,
            over=hours is not None and hours < rec.CONFIDENCE_MIN_HOURS,
        ),
        PeriodSignalRow(
            label="표본 충분성",
            value=(f"{suff * 100:.0f}%" if suff is not None else "N/A"),
            threshold=f"최소 {rec.DOWNSIZE_MIN_SUFFICIENCY * 100:g}%",
            measured=suff is not None,
            over=suff is not None and suff < rec.DOWNSIZE_MIN_SUFFICIENCY,
        ),
    ]


def _cpu_extra_groups(stats: right_sizing.ResourceStats) -> list[PeriodExtraGroup]:
    rec = right_sizing
    percore = stats.cpu_percore_p95_max
    burst = stats.cpu_burst_ratio
    steal = stats.cpu_steal_p95_pct
    load_rows = [
        _extra_row("피크 사용률", stats.cpu_peak_pct, "%"),
        _extra_row(
            "코어별 최대 p95",
            percore,
            "%",
            rec.CPU_PERCORE_HOLD_PCT,
            over=percore is not None and percore >= rec.CPU_PERCORE_HOLD_PCT,
        ),
        _extra_row(
            "D-state 블록 p95",
            stats.procs_blocked_p95,
            "",
            rec.PROCS_BLOCKED_DSTATE_SATURATION,
            over=stats.procs_blocked_p95 is not None and stats.procs_blocked_p95 >= rec.PROCS_BLOCKED_DSTATE_SATURATION,
        ),
    ]
    confidence_rows = [
        _extra_row(
            "버스트 비율(p95/median)",
            burst,
            "x",
            rec.BURST_RATIO_MAX,
            over=burst is not None and burst > rec.BURST_RATIO_MAX,
        ),
        _extra_row(
            "Steal 편향 p95",
            steal,
            "%",
            rec.CPU_STEAL_BIAS_PCT,
            over=steal is not None and steal >= rec.CPU_STEAL_BIAS_PCT,
        ),
        *_confidence_rows(stats),
    ]
    return [
        PeriodExtraGroup("부하 신호", load_rows),
        PeriodExtraGroup("통계 신뢰도", confidence_rows),
    ]


def _mem_extra_groups(stats: right_sizing.ResourceStats) -> list[PeriodExtraGroup]:
    """메모리 상세 탭 "신뢰도" 카드 — CPU 와 동일 구성.

    near-peak 은 버킷별 max 의 p95(비탄력 피크 사이징 기준)다. steal·burst 같은 메모리 편향 원자료는
    ResourceStats 에 없어 통계 신뢰도 그룹이 host-level 입력만 갖는다.
    """
    load_rows = [_extra_row("Near-peak 사용률", stats.mem_near_peak_pct, "%")]
    return [
        PeriodExtraGroup("부하 신호", load_rows),
        PeriodExtraGroup("통계 신뢰도", _confidence_rows(stats)),
    ]


def _storage_extra_groups(stats: right_sizing.ResourceStats) -> list[PeriodExtraGroup]:
    rec = right_sizing

    def _runway_row(label: str, val: float | None) -> PeriodSignalRow:
        stable = stats.history_hours is not None and stats.history_hours >= rec.CONFIDENCE_MIN_HOURS
        if val is not None:
            value = f"{val:.0f}일"
            threshold = f"최소 {rec.DISK_RUNWAY_DAYS:g}일"
        elif stable:
            value, threshold = "안정 (추세 없음)", ""
        else:
            value, threshold = "N/A (관측 부족)", f"최소 {rec.CONFIDENCE_MIN_HOURS:g}h 관측"
        return PeriodSignalRow(
            label=label,
            value=value,
            threshold=threshold,
            over=val is not None and val < rec.DISK_RUNWAY_DAYS,
            measured=val is not None or stable,
        )

    inode_used = stats.disk_inode_used_pct
    load_rows = [
        _runway_row("용량 소진 잔여일수", stats.disk_capacity_runway_days),
        _runway_row("inode 소진 잔여일수", stats.disk_inode_runway_days),
        _extra_row(
            "inode 사용률",
            inode_used,
            "%",
            rec.DISK_STATIC_GUARD_PCT,
            over=inode_used is not None and inode_used >= rec.DISK_STATIC_GUARD_PCT,
        ),
        _extra_row("IOPS 활동량(baseline)", stats.disk_iops_baseline, " IOPS"),
        _extra_row("확장 목표 용량(1년 수명)", stats.disk_capacity_target_gb, "GB"),
    ]
    return [
        PeriodExtraGroup("통계 신뢰도", _confidence_rows(stats)),
        PeriodExtraGroup("부하 신호", load_rows),
    ]


def _network_extra_groups(stats: right_sizing.ResourceStats) -> list[PeriodExtraGroup]:
    load_rows = [_extra_row("트래픽 baseline", stats.net_avg_kbytes_per_s, " kB/s")]
    return [
        PeriodExtraGroup("부하 신호", load_rows),
        PeriodExtraGroup("통계 신뢰도", _confidence_rows(stats)),
    ]


def build_period_assessment(
    stats: right_sizing.ResourceStats,
    errors: list[ErrorSignal] | None = None,
    *,
    disk_worst_mount: str | None = None,
    window_days: int | None = None,
) -> PeriodAssessment:
    rec = right_sizing
    axes = saturation_axis_displays(stats)
    sat_labels = ["실행 큐", "페이징", "응답 지연"]

    def _u(label: str, val: float | None, thr: float) -> PeriodSignalRow:
        return PeriodSignalRow(
            label=label,
            value=_pct_str(val),
            threshold=f"임계 {thr:g}%",
            over=val is not None and val >= thr,
            measured=val is not None,
        )

    def _s(label: str, d: SaturationAxisDisplay) -> PeriodSignalRow:
        raw = d.threshold
        thr = raw if raw.startswith("발생") else "임계 " + raw.removeprefix(">= ").removeprefix("> ")
        return PeriodSignalRow(label=label, value=d.value, threshold=thr, over=d.crossed, measured=d.measured)

    def _net(label: str, val: float | None, thr: float, unit: str) -> PeriodSignalRow:
        return PeriodSignalRow(
            label=label,
            value=(f"{val:.2f}{unit}" if val is not None else "N/A"),
            threshold=f"임계 {thr:g}{unit}",
            over=val is not None and val >= thr,
            measured=val is not None,
        )

    cpu_u = [_u("P95 사용률", stats.cpu_p95_pct, rec.CPU_UNDER_PCT)]
    mem_u = [_u("P95 사용률", stats.mem_p95_pct, rec.MEM_UNDER_PCT)]
    disk_util_val = stats.disk_used_pct
    disk_util_value = _pct_str(disk_util_val)
    if disk_util_val is not None and disk_worst_mount:
        disk_util_value = f"{disk_util_value} ({disk_worst_mount})"
    disk_u = [
        PeriodSignalRow(
            label="사용률 (worst mount)",
            value=disk_util_value,
            threshold=f"임계 {rec.DISK_STATIC_GUARD_PCT:g}%",
            over=disk_util_val is not None and disk_util_val >= rec.DISK_STATIC_GUARD_PCT,
            measured=disk_util_val is not None,
        )
    ]
    cpu_s = [_s(sat_labels[0], axes[0])]
    mem_s = [_s(sat_labels[1], axes[1])]
    disk_s = [_s(sat_labels[2], axes[2])]
    net_s = [
        _net("재전송", stats.net_retrans_pct, rec.NET_RETRANS_PCT, "%"),
        _net("드롭", stats.net_drop_pct, rec.NET_DROP_PCT, "%"),
        PeriodSignalRow(
            label="conntrack",
            value=(f"{stats.conntrack_ratio:.2f}" if stats.conntrack_ratio is not None else "N/A"),
            threshold=f"임계 {rec.CONNTRACK_SATURATION_RATIO:g}",
            over=stats.conntrack_ratio is not None and stats.conntrack_ratio >= rec.CONNTRACK_SATURATION_RATIO,
            measured=stats.conntrack_ratio is not None,
        ),
    ]

    def _over(rows: list[PeriodSignalRow]) -> int:
        return sum(1 for r in rows if r.over)

    host = rec.rollup_host(stats)
    seg_key = rec.host_status_to_recommendation(host.host_status)
    cls_label = rec.RECOMMENDATION_LABEL_KO[seg_key]
    cls_color = next(c for k, c, _ in _DONUT_SEGMENT_DEFS if k == seg_key)

    def _rstat(kind: right_sizing.ResourceKind) -> right_sizing.ResourceStatus:
        return host.resources[kind].status if kind in host.resources else "unmeasured"

    # verdict_label(용량) / verdict_label2(성능) 2개로 분리 노출한다.
    dc, di = _rstat("disk_capacity"), _rstat("disk_io")

    error_rows = _period_error_rows(errors or [])

    mem_error_rows = [r for r in error_rows if r.key.startswith("mem_")]

    return PeriodAssessment(
        window_days=window_days if window_days is not None else rec.WINDOW_DAYS,
        error_rows=error_rows,
        classification_label=cls_label,
        classification_color=cls_color,
        resources=[
            PeriodResource(
                "CPU",
                cpu_u,
                _over(cpu_u),
                cpu_s,
                _over(cpu_s),
                True,
                "cpu",
                *_verdict(_rstat("cpu")),
                extra_groups=_cpu_extra_groups(stats),
            ),
            PeriodResource(
                "메모리",
                mem_u,
                _over(mem_u),
                mem_s,
                _over(mem_s),
                True,
                "memory",
                *_verdict(_rstat("memory")),
                extra_groups=_mem_extra_groups(stats),
                error_rows=mem_error_rows,
            ),
            PeriodResource(
                "스토리지",
                disk_u,
                _over(disk_u),
                disk_s,
                _over(disk_s),
                True,
                "storage",
                *_verdict(dc),
                extra_groups=_storage_extra_groups(stats),
                verdict_label2=_verdict(di)[0],
                verdict_color2=_verdict(di)[1],
            ),
            PeriodResource(
                "네트워크",
                [],
                0,
                net_s,
                _over(net_s),
                False,
                "network",
                *_verdict(_rstat("network")),
                extra_groups=_network_extra_groups(stats),
            ),
        ],
    )
