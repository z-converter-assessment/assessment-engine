"""Attention 신호·환경 개요 mapper (P2).

운영신호 카드(AttentionSignals) = gap·os_eol·agent_unstable 3개 (`query_service._assemble_attention` 조립) +
환경 활용률 도넛·bar·USE Method 분포·under_provisioned 호스트(EnvironmentOverview) 합성. 책임은 raw 신호 → ViewModel.
capacity(under_provisioned)·disk·days_until_full 은 운영신호가 아니라 USE Method right-sizing 소속 —
to_capacity_warning_item 은 EnvironmentOverview.under_provisioned_hosts 로 간다.
임계 분류·표시 색은 shared.py 또는 본 모듈 상단 상수 단일 진실.
"""

from collections import Counter
from datetime import datetime

from assessment_engine import recommendation
from assessment_engine.db.dtos.outbound import MetricGapWarningRaw
from assessment_engine.web.services.mappers.report import build_resource_stats
from assessment_engine.web.services.mappers.server import workload_category_counter
from assessment_engine.web.services.mappers.shared import (
    _DONUT_SEGMENT_DEFS,
    UTIL_GAUGE_COLOR,
    resolve_os_eol,
)
from assessment_engine.web.view_models.attention import (
    AttentionRow,
    CapacityMetric,
    CapacityTriggerBadge,
    CapacityWarningItem,
    EnvironmentOverview,
    EnvironmentRealtime,
    RealtimePeak,
    RealtimePeakGroup,
    RiskDonutSegment,
    UtilizationBar,
)

# ─── 표시 임계·색 단일 진실 ────────────────────────────────────────────────
# 운영 신호 단일 active 색 클래스. base.html 단일 진실. 카테고리간 강도 비교 근거 부족 →
# 임계 초과 발화 자체가 시그널이라 단일 색으로 통일.
_ATTN_ACTIVE_BADGE = "attn-active"

# UtilizationBar 게이지 색 — 환경 평균 활용률 도넛/바 단색 (그라데이션·임계 분기 제거).
# 활용률 정도는 게이지 길이(dash_length)로, 색은 값 무관 단일 푸른색. 색으로 임계 의미를 주지
# 않는다 — 위험도 색은 Right-sizing 분류 도넛이 별도 담당.
_UTIL_COLOR_GAUGE = UTIL_GAUGE_COLOR  # 푸른 단색 (blue-500) — shared.UTIL_GAUGE_COLOR 단일 진실
_UTIL_COLOR_NONE = "#cbd5e1"  # 표본 부재 (회색)

# 도넛 SVG 원주 — r=42, 2*pi*r ≈ 263.89. pct 0~100을 0~_UTIL_DONUT_CIRC로 매핑.
_UTIL_DONUT_CIRC = 263.89

# capacity trigger 5종 색 — hue 명확 분리. 본문 badge와 범례 단일 진실.
# USE Method classify 입력과 1:1 정합 — swap/CPU util/mem util/load(cpu sat)/disk capacity/iowait(disk IO sat).
_CAPACITY_TRIGGER_COLORS: dict[str, str] = {
    "스왑": "#dc2626",  # 빨강 — 메모리 saturation (paging 발생)
    "CPU": "#2563eb",  # 파랑 — CPU utilization 임계 초과
    "메모리": "#8b5cf6",  # 보라 — 메모리 utilization 임계 초과
    "Load": "#ea580c",  # 주황 — CPU saturation (load_15m / cores >= 1.0)
    "디스크": "#0891b2",  # 청록 — disk capacity 또는 IO saturation (iowait)
}

# inactive trigger badge 톤 — active 색은 위 dict, inactive는 본 상수.
_CAPACITY_TRIGGER_INACTIVE_BG = "#f8fafc"
_CAPACITY_TRIGGER_INACTIVE_FG = "#cbd5e1"

# 언더 프로비저닝 카드 지표 값 색 — active(위반)는 under_provisioned 도넛과 동일 hex (E8 동일 의미 단일 진실).
# 위반만 강조, 그 외(정상·N/A)는 동일 진한 색 (미관측 흐림 분기 없음 — N/A 도 같은 폰트).
_METRIC_VIOLATION_COLOR = next(c for k, _, c, _ in _DONUT_SEGMENT_DEFS if k == "under_provisioned")
_METRIC_NORMAL_COLOR = "#1e293b"


def _pct(v: float | None) -> str:
    """float 백분율 -> '94.0%' 표시 (소수 1자리). None(미관측)은 'N/A'."""
    return f"{v:.1f}%" if v is not None else "N/A"


def _metric(label: str, value: str, active: bool, measured: bool) -> CapacityMetric:
    """CapacityMetric 1개 — active(위반)만 강조색, 그 외(정상·N/A)는 동일 진한 색 precompute (P3)."""
    color = _METRIC_VIOLATION_COLOR if active else _METRIC_NORMAL_COLOR
    return CapacityMetric(label=label, value=value, active=active, measured=measured, color=color)


# ─── gap (운영신호) ────────────────────────────────────────────────────────


def to_gap_warning_item(raw: MetricGapWarningRaw, now: datetime) -> AttentionRow:
    """MetricGapWarningRaw → AttentionRow (P2: gap_minutes·badge·표시 string 단일 변환).

    last_metric_at은 KST 표시 위해 meta_at으로 그대로 전달 (KST 변환은 template kst 필터 #F2).
    mount path 없는 카테고리 — mount_path=None.
    """
    gap_min = int((now - raw.last_metric_at).total_seconds() // 60)
    return AttentionRow(
        badge_class=_ATTN_ACTIVE_BADGE,
        badge_text=f"{gap_min}분",
        link_href=f"/servers/{raw.public_id}",
        link_text=raw.hostname,
        meta_text="마지막 수집 ",
        meta_at=raw.last_metric_at,
    )


# ─── 환경 활용률 도넛·bar (UtilizationBar + RiskDonutSegment) ────────────


def _bar_color(pct: float | None) -> str:
    """환경 평균 사용률 게이지 색 — 단색 푸른색 (표본 부재 시 회색).

    활용률 정도는 게이지 길이(dash_length)로 표현하고 색은 값 무관 단일 — 색으로 임계 의미를
    주지 않는다 (Right-sizing 분류 도넛이 위험도 색을 별도 담당).
    """
    if pct is None:
        return _UTIL_COLOR_NONE
    return _UTIL_COLOR_GAUGE


def _dash_length(pct: float | None) -> float:
    if pct is None:
        return 0.0
    return max(0.0, min(pct, 100.0)) / 100.0 * _UTIL_DONUT_CIRC


def build_risk_donut_segments(risk_counts: dict[str, int]) -> tuple[list, int, int]:
    """카테고리별 카운트 -> (RiskDonutSegment list, total, under_count).

    risk_counts 예: {"under_provisioned": 1, "over_provisioned": 2, "optimal": 7} (키 = _DONUT_SEGMENT_DEFS 6종).
    누락 키는 0으로 취급. dash_length·dash_offset은 누적 비례 계산.
    under_count는 도넛 중앙 강조용 (가장 시급한 카테고리).
    """
    total = sum(risk_counts.values())
    segments: list = []
    cum_offset = 0.0
    for key, label, _color, description in _DONUT_SEGMENT_DEFS:
        count = risk_counts.get(key, 0)
        dash_length = (count / total) * _UTIL_DONUT_CIRC if (total > 0 and count > 0) else 0.0
        pct = round(count / total * 100, 1) if total > 0 else 0.0
        segments.append(
            RiskDonutSegment(
                key=key,
                # 표시 라벨 = right-sizing 한국어 분류명(LABEL_KO 단일 진실).
                # 보고서·대시보드 통일, 영어 enum 노출 금지.
                label=recommendation.LABEL_KO.get(key, label),
                # 색 = 게이지 테마 단색 통일 (분류 막대 — 라벨이 의미 전달, 색 무관). _DONUT_SEGMENT_DEFS 다색 미사용.
                color=UTIL_GAUGE_COLOR,
                count=count,
                pct=pct,
                dash_length=dash_length,
                dash_offset=-cum_offset,  # 음수 offset = 시계방향 이동 (잔존 — 도넛 폐기 후 미사용)
                description=description,
            )
        )
        cum_offset += dash_length
    under_count = risk_counts.get("under_provisioned", 0)
    return segments, total, under_count


# 자원 부족(언더 프로비저닝) 상세 표시 상한 — 심각도 상위 N(탑3)만 카드 표시, 전체 수는 count 로 노출.
_UNDER_PROVISIONED_DISPLAY_MAX = 3


def build_environment_overview(
    details: list,
    online_count: int,
    utilization=None,
    risk_counts=None,
    under_provisioned_hosts: list | None = None,
):
    """ServerDetail list + online_count + EnvironmentUtilizationRaw + risk_counts -> EnvironmentOverview.

    list 화면 상단 환경 요약 — 총 N대·자원 합계·역할 분포·온라인/오프라인·평균 활용률·위험도 분포·자원 부족 상세.
    utilization=None이면 활용률 빈 list. risk_counts=None이면 위험도 도넛 빈 list.
    under_provisioned_hosts=None이면 빈 list (도넛 아래 상세 sub-block 미표시).
    """
    total = len(details)
    total_vcpus = sum(d.cpu_cores or 0 for d in details)
    total_mem_kb = sum(d.mem_total_kb or 0 for d in details)
    total_disk_bytes = 0
    for d in details:
        for disk in d.disks or []:
            total_disk_bytes += disk.get("size_bytes") or 0
    # OS 구성 — os_family(windows/linux) 별 서버 수.
    os_counter: Counter[str] = Counter()
    for d in details:
        os_counter[d.os_family or "unknown"] += 1

    # 역할 분포 — 각 서버의 워크로드 카테고리 (ADR 0032 union: services 이름 ∪ listen 소켓 탐지).
    # 이름 분류는 인스턴스 카운트(단 container 런타임 스택은 호스트당 1), listen-only 보충은 +1. unknown 제외.
    role_counter: Counter[str] = Counter()
    role_unknown = 0  # known 역할 0인 호스트 수 (서비스 없음 또는 전부 unknown) — 호스트 단위
    for d in details:
        counter = workload_category_counter(d.services, d.listen_ports)
        if counter:
            role_counter.update(counter)
        else:
            role_unknown += 1

    util_bars: list = []
    util_bars_p95: list = []
    util_sample = 0
    if utilization is not None:
        util_sample = utilization.sample_size
        util_bars = [
            UtilizationBar(
                label="CPU",
                pct=utilization.cpu_avg_pct,
                bar_color=_bar_color(utilization.cpu_avg_pct),
                dash_length=_dash_length(utilization.cpu_avg_pct),
            ),
            UtilizationBar(
                label="메모리",
                pct=utilization.mem_avg_pct,
                bar_color=_bar_color(utilization.mem_avg_pct),
                dash_length=_dash_length(utilization.mem_avg_pct),
            ),
            UtilizationBar(
                label="디스크",
                pct=utilization.disk_avg_pct,
                bar_color=_bar_color(utilization.disk_avg_pct),
                dash_length=_dash_length(utilization.disk_avg_pct),
            ),
        ]
        # p95 활용률 — 평균과 동일 capacity-weighted 환경 분포 기반(per_ts 95퍼센타일). CPU·메모리만 —
        # 디스크는 물리디스크/디바이스 인식이 Windows 에서 불완전해 capacity 합이 신뢰 불가라 제외.
        util_bars_p95 = [
            UtilizationBar(
                label="CPU",
                pct=utilization.cpu_p95_pct,
                bar_color=_bar_color(utilization.cpu_p95_pct),
                dash_length=_dash_length(utilization.cpu_p95_pct),
            ),
            UtilizationBar(
                label="메모리",
                pct=utilization.mem_p95_pct,
                bar_color=_bar_color(utilization.mem_p95_pct),
                dash_length=_dash_length(utilization.mem_p95_pct),
            ),
        ]

    risk_segments: list = []
    risk_total = 0
    risk_under = 0
    if risk_counts is not None:
        risk_segments, risk_total, risk_under = build_risk_donut_segments(risk_counts)

    # 자원 부족 상세 — 심각도(severity_score) DESC 정렬 후 상위 N(탑3)만 표시(P2). 전체 수 count·표시 수 shown 분리.
    # 마이그레이션 우선순위: swap(paging) > 위반 자원 수 > 최고 활용률. 동률은 hostname ASC tie-break.
    _under_all = sorted(under_provisioned_hosts or [], key=lambda c: (-c.severity_score, c.hostname.lower()))
    _under_shown = _under_all[:_UNDER_PROVISIONED_DISPLAY_MAX]

    return EnvironmentOverview(
        total=total,
        online=online_count,
        offline=total - online_count,
        total_vcpus=total_vcpus,
        total_memory_gb=round(total_mem_kb / 1024 / 1024, 1),
        total_disk_gb=int(total_disk_bytes / 10**9),
        # count 내림차순 + 동count는 이름 오름차순 tie-break (most_common 동순위는 삽입순=DB row 순서라 비결정적).
        os_distribution=dict(sorted(os_counter.items(), key=lambda kv: (-kv[1], kv[0]))),
        role_distribution=dict(sorted(role_counter.items(), key=lambda kv: (-kv[1], kv[0]))),
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
    )


def build_environment_realtime(
    total: int,
    online: int,
    snapshots: list[dict],
    last_collected_at,
    top_n: int = 3,
) -> EnvironmentRealtime:
    """온라인 서버 최신 스냅샷 snapshots -> EnvironmentRealtime.

    호출자가 온라인 서버만 snapshots 로 전달 (오프라인 stale 메트릭 제외 — sample_size = len(snapshots)).
    snapshots 키: hostname/public_id + 부하상위용 서버별 값(cpu_pct/mem_pct/disk_pct/load_pct/swap_pct)
                + capacity-weighted 가중치(cpu_cores·mem_used_kb·mem_total_kb·fs_used_gb·fs_total_gb).
    utilization: CPU/메모리/디스크 평균 도넛 3개 — capacity-weighted(environment_utilization 동일 정의).
    peak_groups: 자원별(CPU/메모리/디스크/로드/스왑) top_n 5열 — 서버별 값(load=코어대비%, disk=worst mount).
    """

    # capacity-weighted 평균 — 환경 전체 자원 풀 활용률(단순 산술평균 X). environment_utilization SQL 과 동일 정의:
    #   CPU = Σ(usage%·cores)/Σcores (시점 usage 라 jiffies delta 대신 코어 가중 근사),
    #   mem = Σused_kb/Σtotal_kb, disk = Σused_gb/Σtotal_gb (전 mount 통합, worst mount 아님).
    def _cap_weighted(value_key: str, weight_key: str) -> float | None:
        num = sum(s[value_key] * s[weight_key] for s in snapshots if s.get(value_key) is not None and s.get(weight_key))
        den = sum(s[weight_key] for s in snapshots if s.get(value_key) is not None and s.get(weight_key))
        return round(num / den, 1) if den else None

    def _ratio(used_key: str, total_key: str) -> float | None:
        used = sum(s[used_key] for s in snapshots if s.get(used_key) is not None and s.get(total_key))
        total = sum(s[total_key] for s in snapshots if s.get(used_key) is not None and s.get(total_key))
        return round(used / total * 100, 1) if total else None

    avg_cpu = _cap_weighted("cpu_pct", "cpu_cores")
    avg_mem = _ratio("mem_used_kb", "mem_total_kb")
    avg_disk = _ratio("fs_used_gb", "fs_total_gb")
    util_bars = [
        UtilizationBar(label="CPU", pct=avg_cpu, bar_color=_bar_color(avg_cpu), dash_length=_dash_length(avg_cpu)),
        UtilizationBar(label="메모리", pct=avg_mem, bar_color=_bar_color(avg_mem), dash_length=_dash_length(avg_mem)),
        UtilizationBar(
            label="디스크", pct=avg_disk, bar_color=_bar_color(avg_disk), dash_length=_dash_length(avg_disk)
        ),
    ]

    def _top(key: str) -> list[RealtimePeak]:
        ranked = sorted((s for s in snapshots if s.get(key) is not None), key=lambda s: s[key], reverse=True)
        return [
            RealtimePeak(hostname=s["hostname"], public_id=s["public_id"], pct=s[key], color=_bar_color(s[key]))
            for s in ranked[:top_n]
        ]

    peak_groups = [
        RealtimePeakGroup(label="CPU", peaks=_top("cpu_pct")),
        RealtimePeakGroup(label="메모리", peaks=_top("mem_pct")),
        RealtimePeakGroup(label="디스크", peaks=_top("disk_pct")),
        RealtimePeakGroup(label="로드(코어 대비)", peaks=_top("load_pct")),
        RealtimePeakGroup(label="스왑", peaks=_top("swap_pct")),
    ]
    return EnvironmentRealtime(
        total=total,
        online=online,
        offline=total - online,
        sample_size=len(snapshots),
        utilization=util_bars,
        last_collected_at=last_collected_at,
        peak_groups=peak_groups,
        has_peaks=any(g.peaks for g in peak_groups),
    )


# ─── capacity(USE Method) · os_eol/agent_unstable(운영신호) · disk_days(dead) ──


def to_capacity_warning_item(raw):
    """ReportRowRaw -> CapacityWarningItem. caller가 under_provisioned 필터링 후 호출.

    triggers list — USE Method 5종(스왑/CPU/메모리/Load/디스크) 항상 포함, active 분기.
    active 판정은 recommendation.assess(triggers) 단일 진실 — 임계 재계산 없이 hit 한 trigger 키를
    매핑(drift 방지). 디스크 = capacity 또는 IO 포화. 비활성 trigger 도 list 포함(시각 일관 — 5종 자원 추적).
    swap 은 Windows pagefile 제외(assess 내부 swap_saturation, P2).
    """
    hit = set(recommendation.assess(build_resource_stats(raw)).triggers)
    swap_active = "mem_saturation" in hit
    cpu_active = "cpu_util" in hit
    mem_active = "mem_util" in hit
    load_active = "cpu_saturation" in hit
    disk_active = "disk_capacity" in hit or "disk_io" in hit

    def _badge(label: str, active: bool) -> CapacityTriggerBadge:
        color = _CAPACITY_TRIGGER_COLORS[label]
        if active:
            bg, fg = color, "#fff"
        else:
            bg, fg = _CAPACITY_TRIGGER_INACTIVE_BG, _CAPACITY_TRIGGER_INACTIVE_FG
        return CapacityTriggerBadge(label=label, color=color, active=active, bg_color=bg, fg_color=fg)

    triggers = [
        _badge("스왑", swap_active),
        _badge("CPU", cpu_active),
        _badge("메모리", mem_active),
        _badge("Load", load_active),
        _badge("디스크", disk_active),
    ]

    # 대시보드 카드 지표 6축 — 위반 여부 무관 평가에 쓰인 측정값 전부 노출(active 만 강조). hit 재사용(임계 재계산 0).
    load_ratio = raw.load_15m_max / raw.cpu_cores if raw.load_15m_max is not None and raw.cpu_cores else None
    load_value = f"{load_ratio:.2f}" if load_ratio is not None else "N/A"
    metrics = [
        _metric("CPU p95", _pct(raw.cpu_p95_pct), cpu_active, raw.cpu_p95_pct is not None),
        _metric("메모리 p95", _pct(raw.mem_p95_pct), mem_active, raw.mem_p95_pct is not None),
        _metric("스왑", "발생" if raw.swap_used else "없음", swap_active, True),
        _metric("Load/core", load_value, load_active, load_ratio is not None),
        _metric("디스크", _pct(raw.worst_mount_used_pct), "disk_capacity" in hit, raw.worst_mount_used_pct is not None),
        _metric("iowait", _pct(raw.iowait_p95_pct), "disk_io" in hit, raw.iowait_p95_pct is not None),
    ]
    # 상위 N 절단용 심각도 — swap(paging) 최우선 > 위반 자원 수 > 최고 활용률(CPU/메모리/디스크 max).
    # 가중치 자릿수 분리(swap 1e4 > active*100(max 500) > util(max 100))로 우선순위 충돌 없음.
    util_vals = [v for v in (raw.cpu_p95_pct, raw.mem_p95_pct, raw.worst_mount_used_pct) if v is not None]
    peak_util = max(util_vals) if util_vals else 0.0
    active_count = sum([swap_active, cpu_active, mem_active, load_active, disk_active])
    severity_score = (10000.0 if swap_active else 0.0) + active_count * 100.0 + peak_util
    return CapacityWarningItem(
        public_id=raw.public_id,
        hostname=raw.hostname,
        triggers=triggers,
        # 워크로드 카테고리 카운트 — role_distribution 과 동일 단일 진실 (services 이름 ∪ listen 소켓).
        services=dict(workload_category_counter(raw.services, raw.listen_ports)),
        metrics=metrics,
        severity_score=severity_score,
    )


def to_os_eol_warning_item(raw, now: datetime) -> AttentionRow | None:
    """ReportRowRaw -> AttentionRow if EOL 경과(resolve_os_eol 공용 판정), else None.

    판정(Windows build / Linux os_version + EOL 경과 비교)은 shared.resolve_os_eol 단일 진실 —
    보고서 정성 요약과 동일 로직. 본 함수는 표시(AttentionRow) 변환만.
    """
    result = resolve_os_eol(raw.os_id, raw.os_version, raw.kernel_version, now.date())
    if result is None:
        return None
    eol_iso, label = result
    return AttentionRow(
        badge_class=_ATTN_ACTIVE_BADGE,
        badge_text="EOL",
        link_href=f"/servers/{raw.public_id}",
        link_text=raw.hostname,
        meta_text=f"{label} · EOL {eol_iso}",
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
