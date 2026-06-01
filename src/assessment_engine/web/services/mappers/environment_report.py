"""환경 보고서 mapper — ReportSummary + 기존 helper 결과를 EnvironmentReportSummary 로 합성 (P2).

server scope 보고서와 분리된 high-level 양식 — 분류 분포·OS 분포·top risk·view 별 요약.
색·라벨 단일 진실: `assessment_engine.recommendation`.
"""

from collections import Counter
from datetime import datetime

from assessment_engine.db.dtos.outbound import ServerDetail
from assessment_engine.db.repositories.base_diagnostic_repository import (
    DIAGNOSTIC_RANGE_LABEL_KR,
)
from assessment_engine.web.services.mappers.shared import (
    _CAPACITY_IMMINENT_DAYS,
)
from assessment_engine.web.services.mappers.shared import (
    _DONUT_SEGMENT_DEFS as _PROVISIONING_SEGMENT_DEFS,
)
from assessment_engine.web.view_models.attention import (
    AttentionSignals,
    CapacityWarningItem,
    EnvironmentOverview,
)
from assessment_engine.web.view_models.environment_report import (
    AttentionHostItem,
    CapacityImminentItem,
    ClassificationCount,
    DistributionBar,
    EnvironmentReportSummary,
    InsufficientHostItem,
    OsCount,
)
from assessment_engine.web.view_models.report import ReportRowItem, ReportSummary

# `_PROVISIONING_SEGMENT_DEFS` / `_CAPACITY_IMMINENT_DAYS` 단일 진실 = mappers/shared.py (#E8).
# 본 모듈은 import alias 만 — 환경 보고서·대시보드 도넛·보고서 row 색 통일 (T13).

# 위험도 정렬 우선순위 (Top N 선정).
_RISK_PRIORITY: dict[str, int] = {
    "high": 0,
    "attention": 1,
    "low_usage": 2,
    "normal": 3,
}

# view 별 Top N — customer 는 즉시 액션 항목 (high) 전체, engineer 는 전체 호스트 (제한 없음).
# 엔지니어 보고서는 운영 검토 list 라 N 잘림 없이 전수 노출.
_TOP_RISK_N_BY_VIEW: dict[str, int | None] = {
    "customer": None,
    "engineer": None,
}


def _count_classifications(rows: list[ReportRowItem]) -> list[ClassificationCount]:
    """rows.recommendation 카운트 → USE Method 6 분류 ClassificationCount list (정석 1:1).

    label·color 는 _PROVISIONING_SEGMENT_DEFS — 대시보드 도넛과 시각·의미 정합 (T13).
    """
    counts = Counter(r.recommendation for r in rows)
    return [
        ClassificationCount(
            key=key,
            label=label,
            count=counts.get(key, 0),
            color=color,
            description=description,
        )
        for key, label, color, description in _PROVISIONING_SEGMENT_DEFS
    ]


# OS family key -> 표시명. 구성 막대 라벨 단일 진실 (unknown 은 "미상").
_OS_FAMILY_LABEL: dict[str, str] = {"linux": "Linux", "windows": "Windows", "unknown": "미상"}


def _to_distribution_bars(
    counts: dict[str, int],
    label_map: dict[str, str] | None = None,
) -> list[DistributionBar]:
    """카테고리 카운트 dict -> 구성 분포 막대 list (count DESC, label ASC tie-break).

    pct = 분포 내 최대 count 대비 막대 너비 % (P3 회피 mapper precompute). 단순 분포라 위험도 색 없음.
    """
    if not counts:
        return []
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    mx = max(c for _, c in items) or 1
    return [
        DistributionBar(
            label=(label_map or {}).get(key, key),
            count=count,
            pct=round(count / mx * 100, 1),
        )
        for key, count in items
    ]


def _count_os(details: list[ServerDetail]) -> list[OsCount]:
    """ServerDetail.os_id + os_version 그룹 카운트, count DESC."""
    counts: Counter[str] = Counter()
    for d in details:
        parts = [p for p in [d.os_id, d.os_version] if p]
        label = " ".join(parts) if parts else "unknown"
        counts[label] += 1
    return [OsCount(os_display=label, count=n) for label, n in counts.most_common()]


def _select_top_risks(rows: list[ReportRowItem], view: str) -> list[ReportRowItem]:
    """view 별 위험 list 선정.

    customer: 위험(risk_level='high')만 필터 (정상·주의 제외). 즉시 액션 필요한 호스트 자체.
    engineer: 전체를 위험도 우선순위 정렬 → 상위 10 (운영 액션 list).
    동일 우선순위는 cpu_p95 DESC tie-break.
    """

    def _key(r: ReportRowItem) -> tuple[int, float]:
        return (
            _RISK_PRIORITY.get(r.risk_level, 99),
            -(r.cpu_p95_pct or 0.0),
        )

    if view == "customer":
        return sorted(
            [r for r in rows if r.risk_level == "high"],
            key=lambda r: -(r.cpu_p95_pct or 0.0),
        )
    limit = _TOP_RISK_N_BY_VIEW.get(view, 10)
    sorted_rows = sorted(rows, key=_key)
    return sorted_rows if limit is None else sorted_rows[:limit]


def _extract_attention_hosts(
    attention: AttentionSignals,
    rows: list[ReportRowItem],
) -> list[AttentionHostItem]:
    """운영 신호 3 카테고리 (gap / os_eol / agent_unstable) 를 호스트 기준 unified 합성.

    동일 호스트가 여러 신호 발화 가능 — hostname 기준 merge.
    rows 는 os_display 보충용 (AttentionRow 에 os_display 없음 — 별도 매칭).
    """
    # hostname → os_display 매핑 (rows 에서 1회 build, AttentionRow 에 OS 없어 보충)
    os_by_host: dict[str, str] = {r.hostname: r.os_display for r in rows}

    by_host: dict[str, dict] = {}

    def _slot(public_id: str, hostname: str) -> dict:
        return by_host.setdefault(
            hostname,
            {
                "public_id": public_id,
                "hostname": hostname,
                "gap_label": None,
                "os_eol_label": None,
                "restart_label": None,
            },
        )

    def _pid_from_href(href: str) -> str:
        # AttentionRow.link_href = "/servers/{public_id}" — 마지막 segment 가 public_id.
        return href.rsplit("/", 1)[-1]

    for row in attention.gap_warnings:
        slot = _slot(_pid_from_href(row.link_href), row.link_text)
        slot["gap_label"] = row.badge_text
    for row in attention.os_eol_warnings:
        slot = _slot(_pid_from_href(row.link_href), row.link_text)
        slot["os_eol_label"] = row.meta_text
    for row in attention.agent_unstable:
        slot = _slot(_pid_from_href(row.link_href), row.link_text)
        slot["restart_label"] = row.badge_text

    out: list[AttentionHostItem] = []
    for hostname, slot in by_host.items():
        active = sum(1 for k in ("gap_label", "os_eol_label", "restart_label") if slot[k] is not None)
        if active == 0:
            continue
        out.append(
            AttentionHostItem(
                public_id=slot["public_id"],
                hostname=hostname,
                os_display=os_by_host.get(hostname, ""),
                gap_label=slot["gap_label"],
                os_eol_label=slot["os_eol_label"],
                restart_label=slot["restart_label"],
                active_count=active,
            )
        )
    # 활성 신호 많은 순 → hostname
    out.sort(key=lambda h: (-h.active_count, h.hostname))
    return out


def _extract_capacity_imminent(rows: list[ReportRowItem]) -> list[CapacityImminentItem]:
    """디스크 capacity 임박 호스트 추출 — worst_mount_days_until_full <= 30 (engineer 보고서)."""
    out: list[CapacityImminentItem] = []
    for r in rows:
        if r.worst_mount_days_until_full is None:
            continue
        if r.worst_mount_days_until_full > _CAPACITY_IMMINENT_DAYS:
            continue
        if not r.worst_mount:
            continue
        out.append(
            CapacityImminentItem(
                public_id=r.public_id,
                hostname=r.hostname,
                worst_mount=r.worst_mount,
                days_until_full=r.worst_mount_days_until_full,
                used_pct=r.worst_mount_used_pct,
            )
        )
    # 임박 (작은 days) 우선
    out.sort(key=lambda h: (h.days_until_full, h.hostname))
    return out


def _extract_insufficient(rows: list[ReportRowItem]) -> list[InsufficientHostItem]:
    """insufficient_data 분류 호스트 추출 + 원인 순차 진단.

    1순위: 오프라인 — 에이전트 미가동 (메트릭 자연스러운 부재, root cause).
    2순위: 온라인이나 메트릭 수집 누락 — 누락 메트릭 명시 (CPU·메모리·Load·iowait·디스크).
    3순위: 모든 메트릭 있지만 표본 부족 (윈도우 미만).
    """
    out: list[InsufficientHostItem] = []
    for r in rows:
        if r.recommendation != "insufficient_data":
            continue
        if not r.is_online:
            reason = "오프라인 — 에이전트 미가동"
        else:
            # 온라인 — 세부 메트릭 점검 후 누락 list 명시.
            missing: list[str] = []
            if r.cpu_p95_pct is None:
                missing.append("CPU")
            if r.mem_p95_pct is None:
                missing.append("메모리")
            if r.load_15m_max is None:
                missing.append("Load")
            if r.iowait_p95_pct is None:
                missing.append("iowait")
            if r.worst_mount_used_pct is None:
                missing.append("디스크")
            reason = f"메트릭 수집 누락: {' · '.join(missing)}" if missing else "윈도우 내 표본 부족"
        out.append(
            InsufficientHostItem(
                public_id=r.public_id,
                hostname=r.hostname,
                os_display=r.os_display,
                reason=reason,
            )
        )
    out.sort(key=lambda x: x.hostname)
    return out


def _env_summary_bullets(
    view: str,
    overview: EnvironmentOverview,
    attention: AttentionSignals,
    classification_dist: list[ClassificationCount],
) -> list[str]:
    """환경 단위 정성 요약 — view (customer/engineer) 별 다른 톤·우선순위.

    customer: 비즈니스 시그널 (전체 N대 / 위험 N대 / 권고 사항).
    engineer: 기술 시그널 (분류 분포 / capacity trigger / OS EOL).
    """
    classified = {c.key: c.count for c in classification_dist}
    under = classified.get("under_provisioned", 0)
    over = classified.get("over_provisioned", 0)
    idle = classified.get("idle", 0)
    optimal = classified.get("optimal", 0)
    insufficient = classified.get("insufficient_data", 0)
    online_ratio = (overview.online / overview.total * 100) if overview.total else 0.0

    if view == "customer":
        return [
            f"등록 서버 총 {overview.total}대 — 온라인 {overview.online}대 ({online_ratio:.0f}%).",
            (
                f"[자원 평가] 위험(리소스 부족) {under}대 / 검토 권장(과다·유휴) {over + idle}대"
                f" / 정상 {optimal}대 / 데이터 부족 {insufficient}대."
            ),
        ]

    resource = (
        f"vCPU {overview.total_vcpus} / 메모리 {overview.total_memory_gb:.1f} GB / 디스크 {overview.total_disk_gb} GB"
    )
    distribution = f"under {under} / over {over} / idle {idle} / optimal {optimal} / insufficient {insufficient}"
    bullets = [
        f"등록 {overview.total}대 ({resource}).",
        f"[자원 평가] 분류 분포: {distribution}.",
    ]
    if attention.os_eol_warnings:
        bullets.append(f"[운영 신호] OS EOL {len(attention.os_eol_warnings)}건 — 업그레이드 계획.")
    if attention.agent_unstable:
        bullets.append(f"[운영 신호] 에이전트 재시작 {len(attention.agent_unstable)}건 — 호스트 안정성 점검.")
    return bullets


def to_environment_report(
    *,
    view: str,
    time_range: str,
    anchor_at: datetime,
    overview: EnvironmentOverview,
    attention: AttentionSignals,
    base: ReportSummary,
    details: list[ServerDetail],
    generated_at: datetime,
    under_provisioned_hosts: list[CapacityWarningItem] | None = None,
) -> EnvironmentReportSummary:
    """ReportSummary + EnvironmentOverview + AttentionSignals → EnvironmentReportSummary 합성.

    under_provisioned_hosts: 자원 부족 호스트의 trigger 뱃지 5종 list (caller 가 raw 에서 추출).
    None 이면 EnvironmentOverview.under_provisioned_hosts 재사용.
    """
    classification_dist = _count_classifications(base.rows)
    # P3 회피 precompute — segment 별 pct (mapper 단일 산식).
    classified_total = sum(c.count for c in classification_dist)
    for c in classification_dist:
        c.pct = round((c.count / classified_total * 100), 1) if classified_total else 0.0
    os_dist = _count_os(details)
    # 구성 계층 (P-A) — overview 가 이미 집계한 OS family·워크로드 분포를 막대 ViewModel 로 precompute.
    os_family_dist = _to_distribution_bars(overview.os_distribution, _OS_FAMILY_LABEL)
    workload_dist = _to_distribution_bars(overview.role_distribution)
    top_risks = _select_top_risks(base.rows, view)
    summary = _env_summary_bullets(view, overview, attention, classification_dist)
    under_hosts = (
        under_provisioned_hosts if under_provisioned_hosts is not None else list(overview.under_provisioned_hosts)
    )
    # engineer 보고서 전용 — 운영 신호 호스트 통합 / capacity 임박 / 표본 부족.
    # customer 도 동일 헬퍼 호출 (로직 단일, 미사용 필드는 template 분기로 노출 안 함).
    attention_hosts = _extract_attention_hosts(attention, base.rows)
    capacity_imminent = _extract_capacity_imminent(base.rows)
    insufficient = _extract_insufficient(base.rows)
    return EnvironmentReportSummary(
        view=view,
        time_range=time_range,
        time_range_label=DIAGNOSTIC_RANGE_LABEL_KR.get(time_range, time_range),
        anchor_at=anchor_at,
        generated_at=generated_at,
        overview=overview,
        attention=attention,
        base=base,
        classification_dist=classification_dist,
        os_distribution=os_dist,
        os_family_dist=os_family_dist,
        workload_dist=workload_dist,
        workload_unknown_count=overview.role_unknown_count,
        top_risks=top_risks,
        summary_bullets_env=summary,
        under_provisioned_hosts=under_hosts,
        attention_hosts=attention_hosts,
        capacity_imminent=capacity_imminent,
        insufficient_hosts=insufficient,
        # 템플릿 P3 회피 — 카운트 mapper precompute (#E1 P3).
        top_risks_count=len(top_risks),
        attention_hosts_count=len(attention_hosts),
        capacity_imminent_count=len(capacity_imminent),
        insufficient_hosts_count=len(insufficient),
        under_provisioned_hosts_count=len(under_hosts),
    )
