"""Attention 신호(gap·os_eol·agent_unstable)·환경 개요 mapper (P2).

under_provisioned 는 운영신호가 아니라 USE Method right-sizing 소속이라 EnvironmentOverview 로 간다.
"""

import math
from collections import Counter
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from assessment_engine.domain import right_sizing
from assessment_engine.domain.service_classifier import SIGNATURE_CATEGORIES
from assessment_engine.web.services.device_filters import disk_total_bytes
from assessment_engine.web.services.mappers.assessment_display import build_host_confidence_notes
from assessment_engine.web.services.mappers.constants import (
    _CAUSE_LABEL_BY_TRIGGER,
    _DONUT_SEGMENT_DEFS,
    BADGE_CLASS,
    UTIL_GAUGE_COLOR,
)
from assessment_engine.web.services.mappers.host_display import spec_display_line
from assessment_engine.web.services.mappers.os_eol import (
    lookup_os_eol,
    resolve_os_eol,
)
from assessment_engine.web.services.mappers.resource_stats import build_resource_stats
from assessment_engine.web.services.mappers.server import workload_category_counter
from assessment_engine.web.services.unit_converter import bytes_to_gb, bytes_to_gib
from assessment_engine.web.view_models.attention import (
    ActionTargets,
    AttentionRow,
    CapacityWarningItem,
    EnvironmentOverview,
    EnvironmentRealtime,
    FleetErrorItem,
    RealtimeLoadCell,
    RealtimeLoadRow,
    RiskDonutSegment,
    SaturationDonut,
    UtilizationBar,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from assessment_engine.db.dtos.outbound import (
        EnvironmentUtilizationRaw,
        FleetErrorRaw,
        MetricGapWarningRaw,
        ReportRowRaw,
        ServerDetail,
    )
    from assessment_engine.json_types import JsonObject

# 카테고리간 강도를 비교할 근거가 없어 단일 색 — 임계 초과 발화 자체가 시그널이다.
_ATTN_ACTIVE_BADGE = "attn-active"

# 활용률 정도는 게이지 길이(dash_length)로만 표현하고 색에는 임계 의미를 담지 않는다 —
# 위험도 색은 right-sizing 분류 도넛이 따로 담당.
_UTIL_COLOR_GAUGE = UTIL_GAUGE_COLOR
_UTIL_COLOR_NONE = "#cbd5e1"  # 표본 부재 (회색)

# base.html .badge-cat-* 뱃지 색의 시각적 쌍둥이 — SVG stroke 는 hex 를 요구해 CSS 클래스와
# 별도 소스가 되므로 값 동기화 의무.
_WORKLOAD_COLORS: dict[str, str] = {
    "web": "#2563eb",
    "db": "#e11d48",
    "cache": "#059669",
    "mq": "#9333ea",
    "container": "#0891b2",
    "monitor": "#ea580c",
}

# 템플릿 SVG r="42" 와 정합해야 dash-array 가 맞는다.
_DONUT_RADIUS = 42
_UTIL_DONUT_CIRC = 2 * math.pi * _DONUT_RADIUS

# 임계 초과 강조 색 — 네트워크 혼잡·디스크 I/O 병목·페이징 초과가 같은 hex 공유(동일 의미 = 동일 색).
_NET_CONGESTED_COLOR = "#dc2626"

# 네트워크는 사이징과 별개 품질 축 — host_status 를 구동하지 않아 전용 필드로 분리(판정은 assess_network).
_NET_STATUS_LABEL: dict[str, str] = {"quality_ok": "정상", "congested": "혼잡", "unmeasured": "미측정"}


def to_gap_warning_item(raw: MetricGapWarningRaw, now: datetime) -> AttentionRow:
    """MetricGapWarningRaw -> AttentionRow (gap 분·badge 파생)."""
    gap_min = int((now - raw.last_metric_at).total_seconds() // 60)
    return AttentionRow(
        badge_class=_ATTN_ACTIVE_BADGE,
        badge_text=f"{gap_min}분",
        link_href=f"/servers/{raw.public_id}",
        link_text=raw.hostname,
        meta_text="마지막 수집 ",
        meta_at=raw.last_metric_at,
    )


def _bar_color(pct: float | None) -> str:
    if pct is None:
        return _UTIL_COLOR_NONE
    return _UTIL_COLOR_GAUGE


def _dash_length(pct: float | None) -> float:
    if pct is None:
        return 0.0
    return max(0.0, min(pct, 100.0)) / 100.0 * _UTIL_DONUT_CIRC


def _donut_dash(count: float, total: float) -> float:
    return (count / total) * _UTIL_DONUT_CIRC if (total > 0 and count > 0) else 0.0


def _donut_pct(count: float, total: float) -> float:
    return round(count / total * 100, 1) if total > 0 else 0.0


def _util_bar(label: str, pct: float | None) -> UtilizationBar:
    return UtilizationBar(label=label, pct=pct, bar_color=_bar_color(pct), dash_length=_dash_length(pct))


def build_risk_donut_segments(risk_counts: dict[str, int]) -> tuple[list[RiskDonutSegment], int, int]:
    """카테고리별 카운트 -> (세그먼트 list, total, under_provisioned 수). 누락 키는 0."""
    total = sum(risk_counts.values())
    segments: list[RiskDonutSegment] = []
    cum_offset = 0.0
    for key, _color, description in _DONUT_SEGMENT_DEFS:
        count = risk_counts.get(key, 0)
        dash_length = _donut_dash(count, total)
        pct = _donut_pct(count, total)
        segments.append(
            RiskDonutSegment(
                key=key,
                label=right_sizing.RECOMMENDATION_LABEL_KO[key],
                # _DONUT_SEGMENT_DEFS 의 다색은 쓰지 않는다 — 분류 막대는 라벨이 의미를 전달해 색이 무관.
                color=UTIL_GAUGE_COLOR,
                count=count,
                pct=pct,
                dash_length=dash_length,
                dash_offset=-cum_offset,  # 음수 offset = 시계방향 이동
                description=description,
            )
        )
        cum_offset += dash_length
    under_count = risk_counts.get("under_provisioned", 0)
    return segments, total, under_count


# 카드에는 심각도 상위 N 만 싣고 전체 수는 count 로 노출.
_UNDER_PROVISIONED_DISPLAY_MAX = 3


def _build_saturation_donut(label: str, count: int, total: int) -> SaturationDonut:
    dash = _donut_dash(count, total)
    return SaturationDonut(label=label, count=count, total=total, dash_length=dash, color=_UTIL_COLOR_GAUGE)


def _build_error_fleet(err: FleetErrorRaw | None) -> list[FleetErrorItem]:
    if err is None:
        return []
    t = err.total
    return [
        FleetErrorItem(
            "cpu_mce", "머신체크(MCE)", err.mce_hosts, t, "CPU/메모리 하드웨어 정정불가 오류(machine check)"
        ),
        FleetErrorItem("mem_oom", "OOM Kill", err.oom_hosts, t, "메모리 부족으로 커널이 프로세스 강제 종료"),
        FleetErrorItem("mem_corrupted", "메모리 손상(EDAC)", err.corrupted_hosts, t, "ECC 정정된 하드웨어 메모리 손상"),
        FleetErrorItem("disk_errors", "디스크 에러", err.disk_error_hosts, t, "RAID degraded·파일시스템 손상·IO 오류"),
        FleetErrorItem("net_errors", "NIC 에러", err.net_error_hosts, t, "네트워크 인터페이스 rx/tx 오류 프레임"),
    ]


def _os_eol_summary(details: list[ServerDetail], today: date) -> tuple[int, int, int, int]:
    """OS 지원 단계 종합 -> (무상 패치 종료, 보안 패치만, 미상, 지원 중).

    paid_only·ended 를 한 칸으로 합친다 — 유상 계약 여부를 수집할 수 없어 운영자 행동이 같다.
    os_id 없는 서버(인벤토리 미수집)는 판정 불가인 "미상"과 달라 종합에서 뺀다.
    """
    passed = security_only = unknown = supported = 0
    for d in details:
        if not d.os_id:
            continue
        info = lookup_os_eol(d.os_id, d.os_version, d.kernel_version, today)
        if info is None:
            unknown += 1
        elif info.status in ("paid_only", "ended"):
            passed += 1
        elif info.status == "security_only":
            security_only += 1
        else:
            supported += 1
    return passed, security_only, unknown, supported


def _workload_donut_segments(role_sorted: dict[str, int]) -> tuple[list[RiskDonutSegment], int]:
    total = sum(role_sorted.values())
    segments: list[RiskDonutSegment] = []
    cum = 0.0
    for cat, cnt in role_sorted.items():
        dash = _donut_dash(cnt, total)
        segments.append(
            RiskDonutSegment(
                key=cat,
                label=cat,
                color=_WORKLOAD_COLORS.get(cat, "#94a3b8"),
                count=cnt,
                pct=_donut_pct(cnt, total),
                dash_length=dash,
                dash_offset=-cum,
                description="",
            )
        )
        cum += dash
    return segments, total


def build_environment_overview(
    details: list[ServerDetail],
    online_count: int,
    utilization: EnvironmentUtilizationRaw | None = None,
    risk_counts: dict[str, int] | None = None,
    under_provisioned_hosts: list[CapacityWarningItem] | None = None,
    under_limit: int | None = _UNDER_PROVISIONED_DISPLAY_MAX,
    saturation_counts: dict[str, int] | None = None,
    error_summary: FleetErrorRaw | None = None,
):
    """ServerDetail list + 집계 raw -> EnvironmentOverview (환경 요약 카드 단일 소스).

    None 으로 온 축(utilization·risk_counts·under_provisioned_hosts·saturation_counts)은 빈 list 로 나간다.
    """
    total = len(details)
    total_vcpus = sum(d.cpu_cores or 0 for d in details)
    total_mem_bytes = sum(d.mem_total_bytes or 0 for d in details)
    total_disk_bytes = sum(disk_total_bytes(d.block_devices or []) for d in details)
    os_counter: Counter[str] = Counter()
    for d in details:
        os_counter[d.os_family or "unknown"] += 1

    # 인스턴스 개수 합산 — 호스트 dedup 아님(한 서버에 mq 2개면 +2).
    role_counter: Counter[str] = Counter()
    role_unknown = 0  # 이쪽만 호스트 수 — 특징 워크로드가 하나도 안 잡힌 호스트
    for d in details:
        counter = workload_category_counter(d.services, d.listen_ports)
        if counter:
            role_counter.update(counter)  # Counter.update 는 dict 와 달리 값을 덮지 않고 더한다
        else:
            role_unknown += 1

    util_bars: list[UtilizationBar] = []
    util_bars_p95: list[UtilizationBar] = []
    util_sample = 0
    if utilization is not None:
        util_sample = utilization.sample_size
        util_bars = [
            _util_bar("CPU", utilization.cpu_avg_pct),
            _util_bar("메모리", utilization.mem_avg_pct),
            # 라벨이 "디스크 용량" — fs used% 는 capacity 축이라 이용률과 다르다.
            _util_bar("디스크 용량", utilization.disk_avg_pct),
        ]
        # p95 는 CPU·메모리만 — 디스크는 Windows 디바이스 인식이 불완전해 capacity 합을 믿을 수 없다.
        util_bars_p95 = [
            _util_bar("CPU", utilization.cpu_p95_pct),
            _util_bar("메모리", utilization.mem_p95_pct),
        ]

    risk_segments: list[RiskDonutSegment] = []
    risk_total = 0
    risk_under = 0
    if risk_counts is not None:
        risk_segments, risk_total, risk_under = build_risk_donut_segments(risk_counts)

    _under_all = sorted(under_provisioned_hosts or [], key=lambda c: (-c.severity_score, c.hostname.lower()))
    _under_shown = _under_all if under_limit is None else _under_all[:under_limit]
    # 0 카테고리도 남긴다 — 발화 없는 범례도 노출(E9). 정렬은 개수 desc, 동수는 카탈로그 순서.
    role_sorted = dict(
        sorted(
            ((cat, role_counter.get(cat, 0)) for cat in SIGNATURE_CATEGORIES),
            key=lambda kv: (-kv[1], SIGNATURE_CATEGORIES.index(kv[0])),
        )
    )
    workload_segments, _wl_total = _workload_donut_segments(role_sorted)

    # 카운트는 호출자가 자원 적정성 창 기준으로 산출해 넘긴다 — 실시간 순간 스냅샷과 다른 기준.
    sat_donuts: list[SaturationDonut] = []
    if saturation_counts is not None:
        _sat_total = saturation_counts.get("total", 0)
        sat_donuts = [
            _build_saturation_donut("CPU 포화", saturation_counts.get("cpu", 0), _sat_total),
            _build_saturation_donut("메모리 압박", saturation_counts.get("mem", 0), _sat_total),
            _build_saturation_donut("디스크 I/O 포화", saturation_counts.get("disk_io", 0), _sat_total),
            _build_saturation_donut("네트워크 혼잡", saturation_counts.get("net", 0), _sat_total),
        ]

    _eol_passed, _eol_security_only, _eol_unknown, _eol_supported = _os_eol_summary(details, datetime.now(UTC).date())
    return EnvironmentOverview(
        total=total,
        online=online_count,
        offline=total - online_count,
        total_vcpus=total_vcpus,
        total_memory_gb=bytes_to_gib(total_mem_bytes) or 0.0,
        total_disk_gb=int(bytes_to_gb(total_disk_bytes) or 0),
        # most_common 을 안 쓴다 — 동순위 순서가 삽입순(= DB row 순서)이라 비결정적.
        os_distribution=dict(sorted(os_counter.items(), key=lambda kv: (-kv[1], kv[0]))),
        role_distribution=role_sorted,
        workload_donut=workload_segments,
        workload_total=_wl_total,
        role_unknown_count=role_unknown,
        utilization=util_bars,
        utilization_p95=util_bars_p95,
        util_sample_size=util_sample,
        risk_donut=risk_segments,
        risk_donut_total=risk_total,
        risk_high_count=risk_under,
        under_provisioned_hosts=_under_shown,
        under_provisioned_hosts_count=len(_under_all),
        under_provisioned_hosts_shown=len(_under_shown),
        saturation_donuts=sat_donuts,
        error_fleet=_build_error_fleet(error_summary),
        os_eol_passed=_eol_passed,
        os_eol_security_only=_eol_security_only,
        os_eol_unknown=_eol_unknown,
        os_eol_supported=_eol_supported,
    )


def build_environment_realtime(
    total: int,
    online: int,
    snapshots: list[JsonObject],
    last_collected_at: datetime | None,
) -> EnvironmentRealtime:
    """온라인 서버 최신 스냅샷 -> EnvironmentRealtime.

    호출자가 온라인 서버만 넘긴다 — 오프라인 stale 메트릭이 sample_size 를 부풀리지 않게.
    load_rows 의 디스크 이용률 0%(유휴 실측)와 응답지연 "—"(I/O 0건이라 await 계산 불가)는 한 호스트에
    동시에 나올 수 있다 — 이용률(Utilization)과 응답지연(Saturation)은 USE 상 별개 축이라 모순 아니다.
    """

    # capacity-weighted — 단순 산술평균이 아니다. get_environment_utilization SQL 과 같은 정의를 유지해야 한다.
    # CPU 는 시점 usage 라 jiffies delta 를 못 써 코어 가중 근사로 대신한다.
    def _cap_weighted(value_key: str, weight_key: str) -> float | None:
        num = sum(s[value_key] * s[weight_key] for s in snapshots if s.get(value_key) is not None and s.get(weight_key))
        den = sum(s[weight_key] for s in snapshots if s.get(value_key) is not None and s.get(weight_key))
        return round(num / den, 1) if den else None

    def _ratio(used_key: str, total_key: str) -> float | None:
        used = sum(s[used_key] for s in snapshots if s.get(used_key) is not None and s.get(total_key))
        total = sum(s[total_key] for s in snapshots if s.get(used_key) is not None and s.get(total_key))
        return round(used / total * 100, 1) if total else None

    avg_cpu = _cap_weighted("cpu_pct", "cpu_cores")
    avg_mem = _ratio("mem_used_bytes", "mem_total_bytes")
    # 디스크는 도넛에서 뺀다 — 용량(fill%)은 느린 누적 축이고, I/O 이용률은 장치 종류별 신뢰도 편차라
    # (SSD/NVMe 는 여유가 있어도 100%) 환경 평균으로 묶으면 오탐. 호스트별 raw 값만 load_rows 칼럼으로.
    # 편차 근거는 right-sizing-thresholds.md "Disk IO" 절.
    util_bars = [
        _util_bar("CPU", avg_cpu),
        _util_bar("메모리", avg_mem),
    ]

    def _net_status_cell(congested: bool) -> RealtimeLoadCell:
        """처리량 대신 혼잡 판정만 — 처리량은 판정 원자료(재전송·드롭·conntrack)와 달라 임계를 못 적는다."""
        if congested:
            return RealtimeLoadCell(value=1.0, display="혼잡", color=_NET_CONGESTED_COLOR)
        return RealtimeLoadCell(value=0.0, display="정상")

    def _cell(value: float | None, fmt: Callable[[float], str], exceeded: bool = False) -> RealtimeLoadCell:
        """exceeded=True 면 신호 도넛과 같은 hex 로 강조. 임계 없는 축은 기본값 False 로 무강조."""
        if value is None:
            return RealtimeLoadCell(value=None, display="—")
        return RealtimeLoadCell(value=value, display=fmt(value), color=_NET_CONGESTED_COLOR if exceeded else "")

    def _os_tag(os_family: str | None) -> str:
        return "W" if os_family == "windows" else "L"

    def _os_cell(
        value: float | None, os_family: str | None, fmt: Callable[[float], str], exceeded: bool = False
    ) -> RealtimeLoadCell:
        """페이징 전용 — 값 앞에 L/W 접두를 붙인다.

        무정규화 raw rate 라 OS 무관 해석이 안 된다 — Linux refault(>0 압박) vs Windows Pages Input/sec
        (>=20 압박) 은 같은 숫자가 다른 의미다. 실행 큐는 지수 정규화라 접두가 필요 없다.
        fmt 는 소수점 2자리 고정 — Linux 임계가 "> 0" 이라 정수 반올림하면 0.03/s 실측이 "0" 으로 묻힌다.
        exceeded 는 호출자가 mem_pressure_active 결과로 넘긴다 — 여기서 재계산하면 신호 도넛과 갈린다.
        """
        if value is None:
            return RealtimeLoadCell(value=None, display="—")
        color = _NET_CONGESTED_COLOR if exceeded else ""
        return RealtimeLoadCell(value=value, display=f"{_os_tag(os_family)} {fmt(value)}", color=color)

    # top-N 절단 없이 호스트당 1행 — 유휴(0)도 남기고 칼럼 정렬로 축별 랭킹을 본다.
    load_rows = sorted(
        (
            RealtimeLoadRow(
                hostname=s["hostname"],
                public_id=s["public_id"],
                cpu=_cell(s.get("cpu_pct"), lambda v: f"{v:.1f}%"),
                mem=_cell(s.get("mem_pct"), lambda v: f"{v:.1f}%"),
                run_queue=_cell(
                    s.get("cpu_sat_index"), lambda v: f"{v:.2f}x", exceeded=(s.get("cpu_sat_index") or 0) >= 1.0
                ),
                paging=_os_cell(
                    s.get("paging_rate"),
                    s.get("os_family"),
                    lambda v: f"{v:.2f}/s",
                    exceeded=bool(s.get("mem_pressure")),
                ),
                disk_util=_cell(s.get("disk_util_pct"), lambda v: f"{v:.0f}%"),
                disk_io=_cell(
                    s.get("disk_sat_index"), lambda v: f"{v:.2f}x", exceeded=(s.get("disk_sat_index") or 0) >= 1.0
                ),
                network=_net_status_cell(bool(s.get("net_congested"))),
            )
            for s in snapshots
        ),
        key=lambda r: r.hostname,
    )

    # 라벨을 신호명으로 둔다 — 여기는 순간 단일신호라 "포화" 판정어를 안 쓴다(dual-gate 자원 평가 전용).
    sample = len(snapshots)
    cpu_sat_count = sum(1 for s in snapshots if (s.get("cpu_sat_index") or 0) >= 1.0)
    disk_sat_count = sum(1 for s in snapshots if (s.get("disk_sat_index") or 0) >= 1.0)
    mem_pressure_count = sum(1 for s in snapshots if s.get("mem_pressure"))
    net_congested_count = sum(1 for s in snapshots if s.get("net_congested"))
    saturation_donuts = [
        _build_saturation_donut("실행 큐 임계", cpu_sat_count, sample),
        _build_saturation_donut("페이징", mem_pressure_count, sample),
        _build_saturation_donut("디스크 응답지연 임계", disk_sat_count, sample),
        _build_saturation_donut("네트워크 혼잡", net_congested_count, sample),
    ]
    return EnvironmentRealtime(
        total=total,
        online=online,
        offline=total - online,
        sample_size=len(snapshots),
        utilization=util_bars,
        last_collected_at=last_collected_at,
        load_rows=load_rows,
        saturation_donuts=saturation_donuts,
    )


def to_capacity_warning_item(raw: ReportRowRaw):
    """ReportRowRaw -> CapacityWarningItem. 이름과 달리 전 분류(under/over/idle/optimal/insufficient)에 대해 호출된다.

    active_causes 는 rollup_host 가 잡은 trigger 키를 라벨로 매핑만 한다 — 임계 재계산 0(drift 방지).
    """
    # 운영 신호 경로는 get_report_aggregate 원본만 본다 — disk baseline 주입 없음(유휴 활동 축 미관측).
    stats = build_resource_stats(raw, disk_baseline=None)
    host = right_sizing.rollup_host(stats)
    classification = right_sizing.host_status_to_recommendation(host.host_status)
    hit = {t for r in host.resources.values() for t in r.triggers}
    swap_active = "mem_saturation" in hit
    # 표시 순서 = _CAUSE_LABEL_BY_TRIGGER 삽입순.
    active_causes = [lbl for key, lbl in _CAUSE_LABEL_BY_TRIGGER.items() if key in hit]

    net_res = host.resources["network"]
    net_congested = net_res.status == "congested"
    net_status_value = _NET_STATUS_LABEL.get(net_res.status, net_res.status)
    net_status_color = _NET_CONGESTED_COLOR if net_congested else ""
    # 디스크 I/O 는 classification 무관 항상 노출 — root_cause_label 은 under 인과 기여 시에만 채워져
    # CPU·메모리가 정상인 io_bound 호스트가 안 드러나는 사각지대가 생긴다.
    # 라벨은 STATUS_LABEL_KO(축 status 전용 — 분류 enum 라벨 RECOMMENDATION_LABEL_KO 와 다른 딕셔너리)가
    # 세 상태를 이미 다 갖고 있어 _NET_STATUS_LABEL 같은 전용 딕셔너리를 따로 두지 않는다.
    disk_io_res = host.resources["disk_io"]
    disk_io_status_value = right_sizing.STATUS_LABEL_KO.get(disk_io_res.status, disk_io_res.status)
    disk_io_status_color = _NET_CONGESTED_COLOR if disk_io_res.status == "io_bound" else ""
    spec_display = spec_display_line(raw.cpu_cores, raw.mem_total_bytes, raw.block_devices)
    util_vals = [v for v in (raw.cpu_p95_pct, raw.mem_p95_pct, raw.worst_mount_used_pct) if v is not None]
    peak_util = max(util_vals) if util_vals else 0.0
    # 심각도 가중치는 자릿수로 갈라 우선순위 충돌을 없앤다 — swap 1e4 > 원인수*100(max 500) > util(max 100).
    if classification == "under_provisioned":
        action = right_sizing.under_prescription(host)
        severity_score = (10000.0 if swap_active else 0.0) + len(active_causes) * 100.0 + peak_util
    else:
        action = right_sizing.recommend_action(classification, stats)
        severity_score = 100.0 - peak_util  # 낮은 활용률 = 낭비 큼 -> 정렬 우선
    return CapacityWarningItem(
        public_id=raw.public_id,
        hostname=raw.hostname,
        classification=classification,
        classification_label=right_sizing.RECOMMENDATION_LABEL_KO[classification],
        badge_class=BADGE_CLASS[classification],
        classification_rank=right_sizing.CLASSIFICATION_ORDER[classification],
        active_causes=active_causes,
        services=dict(workload_category_counter(raw.services, raw.listen_ports)),
        confidence_notes=build_host_confidence_notes(host),
        recommendation_action=action,
        root_cause_label=right_sizing.root_cause_display(host),
        severity_score=severity_score,
        net_status_label=net_status_value,
        net_status_color=net_status_color,
        disk_io_status_label=disk_io_status_value,
        disk_io_status_color=disk_io_status_color,
        spec_display=spec_display,
    )


def build_action_targets(raws: list[ReportRowRaw]) -> ActionTargets:
    """전 서버·전 분류 자원 적정성 표 — 자원 평가 페이지·환경 보고서 공유. 정렬은 분류 순서 > 심각도."""
    items: list[CapacityWarningItem] = []
    eff_raws: list[ReportRowRaw] = []
    for raw in raws:
        # item.classification 재사용 — 재계산하면 호스트당 rollup_host 가 두 번 돈다.
        item = to_capacity_warning_item(raw)
        items.append(item)
        if item.classification in ("over_provisioned", "idle"):
            eff_raws.append(raw)
    items.sort(key=lambda it: (right_sizing.CLASSIFICATION_ORDER[it.classification], -it.severity_score, it.hostname))
    return ActionTargets(
        hosts=items,
        total=len(items),
        under_count=sum(1 for it in items if it.classification == "under_provisioned"),
        efficiency_count=len(eff_raws),
        efficiency_vcpus=sum(r.cpu_cores or 0 for r in eff_raws),
        efficiency_memory_gb=round(sum((r.mem_total_bytes or 0) / 1024**3 for r in eff_raws), 1),
        efficiency_disk_gb=int(bytes_to_gb(sum(disk_total_bytes(r.block_devices or []) for r in eff_raws)) or 0),
    )


def to_os_eol_warning_item(raw: ReportRowRaw, now: datetime) -> AttentionRow | None:
    """ReportRowRaw -> AttentionRow, EOL 미경과면 None. 판정은 os_eol.resolve_os_eol 단일 진실."""
    result = resolve_os_eol(raw.os_id, raw.os_version, raw.kernel_version, now.date())
    if result is None:
        return None
    eol_iso, label = result
    # EOL 을 지난 일수라 양수. 파싱 실패는 None 으로 두고 칼럼에서 "—" 로 떨어뜨린다.
    days_over: int | None = None
    try:
        days_over = (now.date() - date.fromisoformat(eol_iso)).days
    except ValueError, TypeError:
        days_over = None
    return AttentionRow(
        badge_class=_ATTN_ACTIVE_BADGE,
        badge_text=label,
        link_href=f"/servers/{raw.public_id}",
        link_text=raw.hostname,
        meta_text=f"{label} · EOL {eol_iso}",
        eol_days_over=days_over,
    )


def to_agent_unstable_item(public_id: str, hostname: str, restart_count: int) -> AttentionRow:
    """raw -> AttentionRow. caller가 임계 필터링 후 호출."""
    return AttentionRow(
        badge_class=_ATTN_ACTIVE_BADGE,
        badge_text=f"{restart_count}회",
        link_href=f"/servers/{public_id}",
        link_text=hostname,
        meta_text="신뢰도 낮음",
    )
