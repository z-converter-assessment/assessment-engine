"""서버 세부 '최근 N일' 평가 카드 — 자원별 이용률·포화 2축 + 신뢰도 보완 신호 (P2).

보고서 표(`report.py`)와 주제가 다르다. 저쪽은 여러 호스트를 한 표에 세우는 행 변환이고, 여기는 호스트
하나를 자원 축으로 펼치는 카드다. 소비자도 갈린다 — 서버 세부 탭과 단일 서버 보고서가 이 카드를 쓴다.

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


# 자원별 status -> (소제목 옆 verdict 라벨, 색). 문제 자원(부족·용량임박·I/O병목=빨강 / 혼잡=주황 / 과다=파랑)만
# 색으로 부각, 정상·유휴·미측정은 muted 회색. 라벨은 카드용 간결형(STATUS_LABEL_KO 동계열).
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
    """ErrorSignal -> 카드 표시행(배지 precompute, P3). occurred=빨강 카운트 / clean=초록 이상없음 / no_data=회색

    "수집 대기"(일시적 미수집) / not_applicable=회색 "N/A"(이 OS 구조적 미지원, 예 Windows EDAC — no_data 와
    구분해 "나중에 나타날 것"으로 오인 표시 안 함).

    OOM(mem_oom)만 분류(자원 부족) 발화 신호라 명시 — 나머지 에러는 표시 전용(사이징 무관, 하드웨어 고장 등).
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
        # OOM 은 1건이라도 발생(occurred)하면 "메모리 자원 부족"을 임계 이상급 빨간색으로 명시(assess_memory 즉시 under).
        # 상시 태그 아님 — 실제 발화 때만. 나머지 에러는 사이징 무관(표시 전용).
        sizing = "메모리 자원 부족" if (e.key == "mem_oom" and e.state == "occurred") else ""
        rows.append(
            PeriodErrorRow(key=e.key, label=e.label, badge_text=text, badge_class=cls, note=note, sizing_signal=sizing)
        )
    return rows


def _extra_row(
    label: str, val: float | None, unit: str, thr: float | None = None, over: bool = False
) -> PeriodSignalRow:
    """신뢰도 카드 공용 로우 빌더 — CPU/메모리 등 자원별 extra_groups 가 공유(P2 표현 단일 소스)."""
    value = f"{val:.1f}{unit}" if val is not None else "N/A"
    threshold = f"임계 {thr:g}{unit}" if thr is not None else ""  # 임계 없는 정보성 값 — 괄호 자체 생략(_prows)
    return PeriodSignalRow(label=label, value=value, threshold=threshold, over=over, measured=val is not None)


def _confidence_rows(stats: right_sizing.ResourceStats) -> list[PeriodSignalRow]:
    """관측 시간·표본 충분성 — host-level 신뢰도 입력(_base_confidence 공용). 자원마다 별 신뢰도

    카드에 반복 노출하는 게 설계 의도(per-resource ConfidenceNote) — 값 자체는 host 공통이라 자원 간 동일.
    """
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
    """CPU 상세 탭 "신뢰도" 카드 — U/S 2축 헤드라인 수치를 얼마나 믿을지 보완하는 원신호, 성격별 2그룹(#E9 완전

    노출). 대등한 두 독립 축이 아니라 전부 "신뢰도" 우산 아래 성격 구분 — 부하 신호도 사이징/근본원인 판정
    게이트(코어별 최대=단일스레드 보호, D-state=IO발 로드 오귀속 방지)라 결국 U/S 수치 해석 맥락이다.

    "부하 신호" = 피크·코어별 최대·D-state 블록 (피크는 임계 없는 정보성, over 항상 False). "통계 신뢰도" =
    버스트·steal 편향·관측 시간·표본 충분성 — confidence/근본원인 판정에 쓰이는 실제 임계 재사용(재계산 0).
    """
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
    """메모리 상세 탭 "신뢰도" 카드 — CPU와 동일 개념(U/S 헤드라인 보완 원신호, 성격별 2그룹, #E9 완전 노출).

    "부하 신호" = near-peak 사용률(버킷별 max 의 p95, 비탄력 피크 사이징 기준 — assess_memory 사이징에 이미
    쓰이나 지금까지 화면에 미노출이던 값, 임계 없는 정보성). "통계 신뢰도" = CPU와 동일 host-level 입력
    (_confidence_rows 공용) — steal/burst 같은 메모리 전용 편향 원자료는 ResourceStats 에 없어 그대로 생략.
    """
    load_rows = [_extra_row("Near-peak 사용률", stats.mem_near_peak_pct, "%")]
    return [
        PeriodExtraGroup("부하 신호", load_rows),
        PeriodExtraGroup("통계 신뢰도", _confidence_rows(stats)),
    ]


def _storage_extra_groups(stats: right_sizing.ResourceStats) -> list[PeriodExtraGroup]:
    """스토리지 상세 탭 "신뢰도" 카드 — CPU/메모리와 동일 개념. 스토리지는 용량(disk_capacity)+I/O(disk_io)

    두 축 통합이라 "부하 신호"에 양쪽 원신호를 함께 담는다(#E9 완전 노출).

    "부하 신호" = 용량 소진 잔여일수(bytes·inode, DISK_RUNWAY_DAYS 미만이면 임박)·inode 사용률(정적 가드
    DISK_STATIC_GUARD_PCT, byte 사용률과 대칭)·IOPS 활동량(baseline, 임계 없는 정보성 — 유휴 device 구분용)·
    확장 목표 용량(1년 수명, 사이징 참고). "통계 신뢰도" = CPU/메모리와 동일 host-level 입력(_confidence_rows).
    disk_io 의 virtio 편향(biased=True)은 상시 True 라 표시 노이즈 -> host 신뢰도 노트와 동일하게 생략.
    """
    rec = right_sizing

    def _runway_row(label: str, val: float | None) -> PeriodSignalRow:
        """runway=None 에 원인 둘이 섞여 있어 "N/A" 단독으로는 어느 쪽인지 못 읽는다 — 구분 표기.

        (1) 관측 span 부족(mount_calc 의 rate_min_span 미달) -> 아직 추세를 못 낸 것(진짜 미상).
        (2) span 은 충분한데 free 가 늘거나 그대로(줄지 않음) -> 추세상 안 채워짐(무한대, 안정).
        두 span 을 별도 SQL로 안 쪼개고 host-level history_hours(같은 agent, 거의 동일 수집 시작점이라
        근사 유효)로 구분 — 정확한 마운트별 span 이 필요해지면 get_report_aggregate 에 별도 컬럼 추가 검토.

        threshold("최소 30일") 는 DISK_RUNWAY_DAYS(값이 이 밑이면 위험) 표기라 val 이 실수일 때만 의미
        있음 — N/A/안정 상태 문자열엔 그 대신 실제 관측 문턱(CONFIDENCE_MIN_HOURS, "관측 시간" 신뢰도
        행과 동일 단일 진실)을 시간 단위로 명시 — 숫자가 우연히 같은 DISK_RUNWAY_DAYS(일)와 혼동 방지.
        """
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
    """네트워크 상세 탭 "신뢰도" 카드 — CPU/메모리/스토리지와 동일 개념(#E9 완전 노출).

    "부하 신호" = 트래픽 baseline(net_avg_kbytes_per_s, 임계 없는 정보성) — assess_network 의 저트래픽 게이트
    (NET_MIN_TRAFFIC_KBPS 미만이면 재전송·드롭 비율을 혼잡 판정에서 억제)가 왜 발동했는지 근거로 유용.
    "통계 신뢰도" = CPU/메모리/스토리지와 동일 host-level 입력(_confidence_rows).
    """
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
    """서버 세부 '최근 N일' 카드 — 자원별 이용률(p95) + 포화(os-aware) 2축 + 에러축(E) (P2 precompute).

    이용률 = p95 사용률 vs 사이징 목표 임계(right_sizing, 14일 p95 도메인 — 실시간 순간과 분리). 포화 = saturation_axis_
    displays(CPU/메모리/디스크 3축, 단일 진실) + 네트워크는 stats retrans/drop/conntrack. 판정(over)은 os-aware
    helper 경유(임계 재계산 0). 에러축(E)은 전 자원 통합(USE 완결). 실시간 카드(순간)와 별 창 — 분류·판정 근거.

    disk_worst_mount — 스토리지 "사용률" 행이 worst-mount 산식(마운트 중 최댓값)임을 값에 병기하는 마운트 이름.
    ResourceStats 에는 안 둠(도메인 모델에 표시 전용 str 필드 추가 회피) — 호출부(get_period_assessment)가
    ReportRowRaw.disk_capacity_worst_mount 를 직접 전달. 실시간 카드 도넛(disk_usage_pct, 전체 마운트 가중평균)
    과 다른 산식이라는 걸 화면에서 바로 알 수 있게 함(#E9 "의도된 차이" 명시 요청 반영).
    """
    rec = right_sizing
    axes = saturation_axis_displays(stats)  # [cpu, mem, disk]
    sat_labels = ["실행 큐", "페이징", "응답 지연"]  # 실시간 카드 라벨과 통일

    def _u(label: str, val: float | None, thr: float) -> PeriodSignalRow:
        return PeriodSignalRow(
            label=label,
            value=_pct_str(val),
            threshold=f"임계 {thr:g}%",
            over=val is not None and val >= thr,
            measured=val is not None,
        )

    # 포화 축 over = 단일 게이트(d.crossed: 신호가 자기 임계 넘음) — 값·임계와 self-consistent. 종합 dual-gate
    # 판정(이용률 AND 포화)은 분류 배지(classify_host)가 전담 — 축은 신호 자체 비정상 여부만.
    # d.threshold 는 saturation_axis_displays 원문(">= 1"·"> 20ms"·"발생 시") — 다른 화면(단일 보고서 포화 축
    # 표·참고자료 임계값)과 공유하는 표시 단일 진실이라 그 원본은 불변. 이 카드만 이용률 컬럼과 같은 "임계 X" 어투
    # 통일 — 부등호(>=·>)는 "임계"라는 말 자체가 그 값을 포함하는 의미라 중복이라 제거하고 숫자만 접두.
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
    # 스토리지 "사용률"만 다른 자원(P95 host 집계)과 달리 worst-mount 산식(가장 채워진 마운트 1개) — 실시간
    # 도넛(전체 마운트 가중평균)과 값이 다를 수 있어 라벨·값에 worst-mount 임을 명시(#E9 의도된 차이 표기).
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

    # 종합·자원별 판정 — rollup_host 1회(목록 자원 적정성과 동일 단일 진실). 배지=host_status 종합, 소제목 옆
    # verdict=자원별 status(어느 자원발인지). 문제 자원만 색, 정상·유휴·미측정은 muted.
    host = rec.rollup_host(stats)
    seg_key = rec.host_status_to_recommendation(host.host_status)
    cls_label = rec.RECOMMENDATION_LABEL_KO[seg_key]
    cls_color = next(c for k, c, _ in _DONUT_SEGMENT_DEFS if k == seg_key)

    def _rstat(kind: right_sizing.ResourceKind) -> right_sizing.ResourceStatus:
        return host.resources[kind].status if kind in host.resources else "unmeasured"

    # 스토리지 = 용량(disk_capacity) + 성능/IO(disk_io) 독립 2축 — 배지 1개로 합치면(우선순위 승자만 노출)
    # 승자 아닌 축 상태가 안 보인다 — "I/O 병목" 만 뜨면 용량이 괜찮은지 판단할 수 없다. 카드 제목
    # 옆 배지를 용량/성능 2개로 분리 노출(verdict_label=용량, verdict_label2=성능) — PeriodResource 참고.
    dc, di = _rstat("disk_capacity"), _rstat("disk_io")

    error_rows = _period_error_rows(errors or [])
    # 메모리 상세 탭 전용 에러 열 — mem_ 접두 키(OOM Kill·메모리 손상 EDAC)만, 사이징 무관 나머지(MCE·NIC·디스크)
    # 는 제외(자원별 카드에 자기 자원 무관 에러 노출 금지).
    mem_error_rows = [r for r in error_rows if r.key.startswith("mem_")]

    return PeriodAssessment(
        # 실제 집계창 주입(호출자) — 서버 상세=WINDOW_DAYS(14), 단일 보고서=선택 range(period_days). 미주입 시
        # WINDOW_DAYS 폴백. 하드코딩 시 보고서 스냅샷 window_days 가 실제 창과 decouple(P2 지속경로 정합).
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


# --- ReportRowRaw -> ReportRowItem (P2 단일 변환) ---
