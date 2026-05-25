"""Attention 신호·환경 개요 mapper (P2).

대시보드 상단 attention 카드 (disk·gap·capacity·disk_days·os_eol·agent_unstable) +
환경 활용률 도넛·bar (EnvironmentOverview) 합성. 책임은 raw 신호 → ViewModel.
임계 분류·표시 색은 shared.py 또는 본 모듈 상단 상수 단일 진실.
"""

from collections import Counter
from datetime import datetime

from assessment_engine import recommendation
from assessment_engine.db.dtos.outbound import (
    DiskUsageWarningRaw,
    MetricGapWarningRaw,
)
from assessment_engine.web.services.mappers.server import infer_role
from assessment_engine.web.services.mappers.shared import (
    _DONUT_SEGMENT_DEFS,
    _OS_EOL,
    _USAGE_DANGER_PCT,
)
from assessment_engine.web.view_models.attention import (
    AttentionRow,
    CapacityTriggerBadge,
    CapacityWarningItem,
    EnvironmentOverview,
    RiskDonutSegment,
    UtilizationBar,
)

# ─── 표시 임계·색 단일 진실 ────────────────────────────────────────────────
# 운영 신호 단일 active 색 클래스. base.html 단일 진실. 카테고리간 강도 비교 근거 부족 →
# 임계 초과 발화 자체가 시그널이라 단일 색으로 통일.
_ATTN_ACTIVE_BADGE = "attn-active"

# disk_warnings stale 임계 — last_metric_at이 24h 이상 안 갱신된 mount는 meta에 "마지막 수집" 추가 표시.
# 7d cutoff(SQL) 안에서도 1d 이상 stale은 운영자가 인지해야 함.
_DISK_STALE_HOURS = 24

# UtilizationBar 임계 — 환경 평균 활용률 색 결정 (P3 임계 분기 금지 → mapper 단일).
# 본 임계는 환경 평균 도메인이라 server detail badge(_USAGE_DANGER_PCT/_USAGE_WARN_PCT)와 별개.
_UTIL_LOW_PCT = 60  # 미만 → 녹색 (여유)
_UTIL_HIGH_PCT = 80  # 이상 → 빨강 (압박)
_UTIL_COLOR_LOW = "#22c55e"
_UTIL_COLOR_MID = "#f59e0b"
_UTIL_COLOR_HIGH = "#ef4444"
_UTIL_COLOR_NONE = "#cbd5e1"  # 표본 부재

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


# ─── 단순 attention 카드 (disk · gap) ─────────────────────────────────────


def to_disk_warning_item(raw: DiskUsageWarningRaw, now: datetime) -> AttentionRow:
    """DiskUsageWarningRaw → AttentionRow (P2: 단위 변환·badge 분류·표시 string·stale 분기 단일 변환).

    threshold(85%)는 repo가 이미 거름 — mapper는 단위 + badge + 표시 string만. SQL이 total_bytes>0를 거름.
    last_metric_at이 _DISK_STALE_HOURS 이상 안 갱신된 mount는 meta_at 채워서 "마지막 수집" 표시 활성.
    """
    used_pct = (1 - raw.avail_bytes / raw.total_bytes) * 100
    free_gb = raw.avail_bytes / 1024**3
    total_gb = raw.total_bytes / 1024**3
    # 디스크 사용률 위험도 (repo 가 85%+ 만 거름): 90%+ = 위험(빨강), 85~90% = 주의(amber).
    badge = "rec-under_provisioned" if used_pct >= _USAGE_DANGER_PCT else "badge-warn"
    is_stale = (now - raw.last_metric_at).total_seconds() / 3600 >= _DISK_STALE_HOURS
    meta_text = f"잔여 {free_gb:.1f} / {total_gb:.1f} GB"
    if is_stale:
        meta_text += " · 마지막 수집 "
    return AttentionRow(
        badge_class=badge,
        badge_text=f"{used_pct:.0f}%",
        link_href=f"/servers/{raw.public_id}/storage",
        link_text=raw.hostname,
        mount_path=raw.mount,
        meta_text=meta_text,
        meta_at=raw.last_metric_at if is_stale else None,
    )


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
    """환경 평균 사용률 bar — 0% 초록 → 60% 노랑 → 100% 빨강 HSL hue 그라데이션.

    사용률 변화에 따라 자연스러운 색 이행 (3단계 jump 보다 시각 정합).
    """
    if pct is None:
        return _UTIL_COLOR_NONE
    pct_capped = max(0.0, min(100.0, pct))
    hue = 120 - 1.2 * pct_capped  # 0% -> 120 (green), 100% -> 0 (red)
    return f"hsl({hue:.0f}, 65%, 45%)"


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
    for key, label, color, description in _DONUT_SEGMENT_DEFS:
        count = risk_counts.get(key, 0)
        dash_length = (count / total) * _UTIL_DONUT_CIRC if (total > 0 and count > 0) else 0.0
        segments.append(
            RiskDonutSegment(
                key=key,
                label=label,
                color=color,
                count=count,
                dash_length=dash_length,
                dash_offset=-cum_offset,  # 음수 offset = 시계방향 이동
                description=description,
            )
        )
        cum_offset += dash_length
    under_count = risk_counts.get("under_provisioned", 0)
    return segments, total, under_count


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
    role_counter: Counter[str] = Counter()
    for d in details:
        role_counter[infer_role(d.services)] += 1

    util_bars: list = []
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

    risk_segments: list = []
    risk_total = 0
    risk_under = 0
    if risk_counts is not None:
        risk_segments, risk_total, risk_under = build_risk_donut_segments(risk_counts)

    return EnvironmentOverview(
        total=total,
        online=online_count,
        offline=total - online_count,
        total_vcpus=total_vcpus,
        total_memory_gb=round(total_mem_kb / 1024 / 1024, 1),
        total_disk_gb=int(total_disk_bytes / 10**9),
        role_distribution=dict(role_counter.most_common()),
        utilization=util_bars,
        util_sample_size=util_sample,
        risk_donut=risk_segments,
        risk_donut_total=risk_total,
        risk_high_count=risk_under,
        under_provisioned_hosts=under_provisioned_hosts or [],
        under_provisioned_hosts_count=len(under_provisioned_hosts or []),
    )


# ─── capacity / disk_days / os_eol / agent_unstable ───────────────────────


def to_capacity_warning_item(raw):
    """ReportRowRaw -> CapacityWarningItem. caller가 under_provisioned 필터링 후 호출.

    triggers list — USE Method classify 입력과 1:1 정합 5종(스왑/CPU/메모리/Load/디스크) 항상 포함, active 분기:
    - swap_used=True → "스왑" (Memory saturation)
    - cpu_p95 >= CPU_UPSIZE_P95_PCT → "CPU" (CPU utilization)
    - mem_p95 >= MEM_UPSIZE_P95_PCT → "메모리" (Memory utilization)
    - load_15m_max / cpu_cores >= CPU_SATURATION_LOAD_RATIO → "Load" (CPU saturation)
    - worst_mount_used_pct >= DISK_CAPACITY_UPSIZE_PCT 또는 iowait_p95 >= IOWAIT_UPSIZE_PCT → "디스크"
    비활성 trigger도 list에 포함 (시각 일관 — "이 카드는 5종 자원 추적" 명시).
    """
    swap_active = bool(raw.swap_used)
    cpu_active = (raw.cpu_p95_pct or 0) >= recommendation.CPU_UPSIZE_P95_PCT
    mem_active = (raw.mem_p95_pct or 0) >= recommendation.MEM_UPSIZE_P95_PCT
    load_active = (
        raw.load_15m_max is not None
        and raw.cpu_cores is not None
        and raw.cpu_cores > 0
        and (raw.load_15m_max / raw.cpu_cores) >= recommendation.CPU_SATURATION_LOAD_RATIO
    )
    disk_capacity_active = (
        raw.worst_mount_used_pct is not None and raw.worst_mount_used_pct >= recommendation.DISK_CAPACITY_UPSIZE_PCT
    )
    disk_io_active = raw.iowait_p95_pct is not None and raw.iowait_p95_pct >= recommendation.IOWAIT_UPSIZE_PCT
    disk_active = disk_capacity_active or disk_io_active

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
    return CapacityWarningItem(
        public_id=raw.public_id,
        hostname=raw.hostname,
        triggers=triggers,
    )


def to_disk_days_warning_item(
    public_id: str,
    hostname: str,
    mount: str,
    days_until_full: int,
    used_pct: float | None,
) -> AttentionRow:
    """raw tuple -> AttentionRow. caller가 days <= 30 필터링 후 호출.

    badge=N일 (rec-under_provisioned), mount_path 별도 attribute, meta="{used_pct}%" (없으면 빈 string).
    """
    meta = ""
    if used_pct is not None:
        meta = f"{used_pct:.0f}%"
    return AttentionRow(
        badge_class="rec-under_provisioned",
        badge_text=f"{days_until_full}일",
        link_href=f"/servers/{public_id}/storage",
        link_text=hostname,
        mount_path=mount,
        meta_text=meta,
    )


def to_os_eol_warning_item(raw) -> AttentionRow | None:
    """ReportRowRaw -> AttentionRow if matches _OS_EOL, else None.

    badge=EOL 라벨 + meta="{os_id os_version} · EOL {eol_date}".
    """
    for (eol_os, eol_ver), date in _OS_EOL.items():
        if raw.os_id == eol_os and (raw.os_version or "").startswith(eol_ver):
            os_display = " ".join(p for p in [raw.os_id, raw.os_version] if p) or "-"
            return AttentionRow(
                badge_class=_ATTN_ACTIVE_BADGE,
                badge_text="EOL",
                link_href=f"/servers/{raw.public_id}",
                link_text=raw.hostname,
                meta_text=f"{os_display} · EOL {date}",
            )
    return None


def to_agent_unstable_item(public_id: str, hostname: str, restart_count: int) -> AttentionRow:
    """raw -> AttentionRow. caller가 임계 필터링 후 호출."""
    return AttentionRow(
        badge_class=_ATTN_ACTIVE_BADGE,
        badge_text=f"{restart_count}회",
        link_href=f"/servers/{public_id}",
        link_text=hostname,
        meta_text="신뢰도 낮음",
    )
