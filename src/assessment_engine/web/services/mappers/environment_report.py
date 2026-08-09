"""환경 보고서 mapper — ReportSummary + helper 결과를 EnvironmentReportSummary 로 합성.

색·라벨 단일 진실: `assessment_engine.domain.right_sizing`.
"""

from collections import Counter
from typing import TYPE_CHECKING

from assessment_engine.domain import right_sizing
from assessment_engine.domain.service_classifier import SIGNATURE_CATEGORIES, SINGLE_INSTANCE_CATEGORIES
from assessment_engine.web.services.mappers.constants import (
    _CAUSE_LABEL_BY_TRIGGER,
    DIAGNOSTIC_RANGE_LABEL_KR,
    OS_FAMILY_LABEL_KO,
    RISK_LEVEL_ORDER,
    UTIL_GAUGE_COLOR,
    ReportView,
)
from assessment_engine.web.services.mappers.constants import _DONUT_SEGMENT_DEFS as _PROVISIONING_SEGMENT_DEFS
from assessment_engine.web.services.mappers.topology import build_network_topology
from assessment_engine.web.view_models.environment_report import (
    AttentionHostItem,
    CapacityImminentItem,
    ClassificationCount,
    DistributionBar,
    EnvironmentReportSummary,
    OsCount,
    ServiceCatalogGroup,
    ServiceHost,
    ServiceNameCount,
)

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    from assessment_engine.db.dtos.outbound import MetricSeries, ServerDetail
    from assessment_engine.json_types import JsonObject
    from assessment_engine.web.view_models.attention import (
        ActionTargets,
        AttentionSignals,
        CapacityWarningItem,
        EnvironmentOverview,
    )
    from assessment_engine.web.view_models.report import ReportRowItem, ReportSummary


# 둘 다 None — 운영 검토 list 라 상위 N 잘림 없이 전수 노출.
_TOP_RISK_N_BY_VIEW: dict[str, int | None] = {
    "customer": None,
    "engineer": None,
}


def _count_classifications(rows: list[ReportRowItem]) -> list[ClassificationCount]:
    counts = Counter(r.recommendation for r in rows)
    return [
        ClassificationCount(
            key=key,
            label=right_sizing.RECOMMENDATION_LABEL_KO[key],
            count=counts.get(key, 0),
            color=UTIL_GAUGE_COLOR,
            description=right_sizing.RECOMMENDATION_ACTION_KO[key],
        )
        for key, _color, _description in _PROVISIONING_SEGMENT_DEFS
    ]


def _to_distribution_bars(
    counts: dict[str, int],
    label_map: dict[str, str] | None = None,
) -> list[DistributionBar]:
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


def build_metric_trend(
    cpu_series: list[MetricSeries], mem_series: list[MetricSeries], disk_series: list[MetricSeries]
) -> list[JsonObject]:

    # SQL avg 가 Decimal 을 돌려준다 — tojson 이 직렬화하지 못한다.
    def _f(v: float | Decimal | None) -> float | None:
        return round(float(v), 1) if v is not None else None

    cpu_by = {s.collected_at: _f(s.value) for s in cpu_series}
    mem_by = {s.collected_at: _f(s.value) for s in mem_series}
    disk_by = {s.collected_at: _f(s.value) for s in disk_series}
    timestamps = sorted(set(cpu_by) | set(mem_by) | set(disk_by))
    return [
        {"at": t.isoformat(), "cpu": cpu_by.get(t), "mem": mem_by.get(t), "disk": disk_by.get(t)} for t in timestamps
    ]


def build_saturation_trend(
    cpu_series: list[MetricSeries], mem_series: list[MetricSeries], disk_series: list[MetricSeries]
) -> list[JsonObject]:
    """포화 이진(0/1) 시계열 세 개를 버킷 시각 기준 merge -> 차트 JS plain dict.

    포화 판정은 SQL(cpu.saturation·mem.paging_pressure·disk.saturation)이 이미 0.0/1.0 로 내린다 — 여기선 병합만.
    at 은 isoformat str. 표본 없는 축은 None (판정 불가와 미포화 구분).
    """
    cpu_by = {s.collected_at: (float(s.value) if s.value is not None else None) for s in cpu_series}
    mem_by = {s.collected_at: (float(s.value) if s.value is not None else None) for s in mem_series}
    disk_by = {s.collected_at: (float(s.value) if s.value is not None else None) for s in disk_series}
    timestamps = sorted(set(cpu_by) | set(mem_by) | set(disk_by))
    return [
        {"at": t.isoformat(), "cpu_sat": cpu_by.get(t), "mem_sat": mem_by.get(t), "disk_sat": disk_by.get(t)}
        for t in timestamps
    ]


def _aggregate_service_catalog(rows: list[ReportRowItem]) -> list[ServiceCatalogGroup]:
    multi: dict[str, dict[str, list[ServiceHost]]] = {}
    single_names: dict[str, set[str]] = {}
    single_hosts: dict[str, list[ServiceHost]] = {}
    cat_hosts: dict[str, set[str]] = {}
    named_hosts: dict[str, set[str]] = {}
    for r in rows:
        host = ServiceHost(hostname=r.hostname, public_id=r.public_id)
        for cat in r.workload_categories:
            if cat not in SIGNATURE_CATEGORIES:
                continue
            cat_hosts.setdefault(cat, set()).add(r.public_id)

        for cat, names in r.workload_services.items():
            if cat not in SIGNATURE_CATEGORIES or not names:
                continue
            named_hosts.setdefault(cat, set()).add(r.public_id)
            if cat in SINGLE_INSTANCE_CATEGORIES:
                single_names.setdefault(cat, set()).update(names)
                single_hosts.setdefault(cat, []).append(host)
            else:
                cat_map = multi.setdefault(cat, {})
                for n in names:
                    cat_map.setdefault(n, []).append(host)
    groups: dict[str, list[ServiceNameCount]] = {}
    for cat, cat_map in multi.items():
        groups[cat] = [ServiceNameCount(name=n, count=len(hosts), hosts=hosts) for n, hosts in sorted(cat_map.items())]
    for cat, hosts in single_hosts.items():
        label = ", ".join(sorted(single_names.get(cat, set()))) or cat
        groups[cat] = [ServiceNameCount(name=label, count=len(hosts), hosts=hosts)]

    for cat, all_hosts in cat_hosts.items():
        listen_only = len(all_hosts - named_hosts.get(cat, set()))
        if listen_only:
            groups.setdefault(cat, []).append(ServiceNameCount(name="(포트 탐지)", count=listen_only, hosts=[]))
    return [
        ServiceCatalogGroup(category=cat, total_count=len(cat_hosts.get(cat, set())), services=groups.get(cat, []))
        for cat in SIGNATURE_CATEGORIES
    ]


def _count_os(details: list[ServerDetail]) -> list[OsCount]:
    counts: Counter[tuple[str, str, str]] = Counter()
    kernels: dict[tuple[str, str, str], set[str]] = {}
    for d in details:
        family = "Windows" if d.os_family == "windows" else "Linux" if d.os_family == "linux" else "기타"
        distro = d.os_id or "unknown"
        version = d.os_version or "—"
        key = (family, distro, version)
        counts[key] += 1
        if d.kernel_version:
            kernels.setdefault(key, set()).add(d.kernel_version)
    rows = [
        OsCount(
            family=f,
            distro=di,
            version=v,
            count=n,
            kernel_versions=", ".join(sorted(kernels.get((f, di, v), set()))) or "—",
        )
        for (f, di, v), n in counts.items()
    ]
    rows.sort(key=lambda r: (r.family, r.distro, r.version))
    return rows


def _build_env_metrics(overview: EnvironmentOverview) -> list[JsonObject]:
    util = {b.label: b.pct for b in overview.utilization}
    util_p95 = {b.label: b.pct for b in overview.utilization_p95}
    sat = overview.saturation_donuts

    def _pct(v: float | None) -> str:
        return f"{v:.1f}%" if v is not None else "—"

    def _sat(i: int) -> JsonObject:
        if i < len(sat):
            d = sat[i]
            return {"label": d.label, "value": f"{d.count}대", "sub": f"/ {d.total}대"}
        return {"label": "—", "value": "—", "sub": ""}

    return [
        {"label": "CPU 이용률", "value": _pct(util.get("CPU")), "sub": f"p95 {_pct(util_p95.get('CPU'))}"},
        {"label": "메모리 이용률", "value": _pct(util.get("메모리")), "sub": f"p95 {_pct(util_p95.get('메모리'))}"},
        {"label": "디스크 용량", "value": _pct(util.get("디스크 용량")), "sub": ""},
        _sat(0),
        _sat(1),
        _sat(2),
    ]


def _select_top_risks(rows: list[ReportRowItem], view: ReportView) -> list[ReportRowItem]:
    def _key(r: ReportRowItem) -> tuple[int, float]:
        return (
            RISK_LEVEL_ORDER.get(r.risk_level, 99),
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

    os_by_host: dict[str, str] = {r.hostname: r.os_display for r in rows}

    by_host: dict[str, JsonObject] = {}

    def _slot(public_id: str, hostname: str) -> JsonObject:
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
    out.sort(key=lambda h: (-h.active_count, h.hostname))
    return out


def _extract_capacity_imminent(rows: list[ReportRowItem]) -> list[CapacityImminentItem]:
    out: list[CapacityImminentItem] = []
    for r in rows:
        candidate: tuple[int, str | None, str, float | None] | None = None
        if r.disk_capacity_runway_days is not None and r.disk_capacity_runway_days < right_sizing.DISK_RUNWAY_DAYS:
            candidate = (r.disk_capacity_runway_days, r.disk_capacity_driving_mount, "용량", r.worst_mount_used_pct)
        if (
            r.disk_inode_runway_days is not None
            and r.disk_inode_runway_days < right_sizing.DISK_RUNWAY_DAYS
            and (candidate is None or r.disk_inode_runway_days < candidate[0])
        ):
            candidate = (r.disk_inode_runway_days, r.disk_inode_driving_mount, "inode", None)
        if candidate is None:
            continue
        runway, mount, constraint_label, used_pct = candidate
        if mount is None:
            continue
        out.append(
            CapacityImminentItem(
                public_id=r.public_id,
                hostname=r.hostname,
                worst_mount=mount,
                days_until_full=runway,
                constraint_label=constraint_label,
                used_pct=used_pct,
            )
        )
    out.sort(key=lambda h: (h.days_until_full, h.hostname))
    return out


_UNDER_CAUSE_ORDER = tuple(_CAUSE_LABEL_BY_TRIGGER.values())


def _under_cause_summary(under_hosts: list[CapacityWarningItem]) -> str:
    """발화 원인 집계 라벨 — "메모리 포화 2대 · CPU 이용률 1대".

    라벨은 active_causes 를 그대로 센다 — 여기서 다시 만들면 Windows 의 paging·run queue 포화가
    Linux swap·load 이름으로 나간다.
    한 호스트가 복수 원인이면 각 원인에 1대씩 누적한다 — 합이 자원 부족 대수보다 클 수 있다.
    """
    counts: dict[str, int] = {}
    for h in under_hosts:
        for cause in h.active_causes:
            counts[cause] = counts.get(cause, 0) + 1
    return " · ".join(f"{lbl} {counts[lbl]}대" for lbl in _UNDER_CAUSE_ORDER if lbl in counts)


def _env_summary_bullets(
    overview: EnvironmentOverview,
    attention: AttentionSignals,
    classification_dist: list[ClassificationCount],
    under_hosts: list[CapacityWarningItem],
) -> list[str]:
    classified = {c.key: c.count for c in classification_dist}
    under = classified.get("under_provisioned", 0)
    over = classified.get("over_provisioned", 0)
    idle = classified.get("idle", 0)
    optimal = classified.get("optimal", 0)
    insufficient = classified.get("insufficient_data", 0)

    # 운영 신호는 OS 지원 종료만 — gap·agent_unstable 은 전역 신호라 보고서 윈도우와 의미가 어긋난다.
    ko = right_sizing.RECOMMENDATION_LABEL_KO
    resource = (
        f"vCPU {overview.total_vcpus} | 메모리 {overview.total_memory_gb:.1f} GB | 디스크 {overview.total_disk_gb} GB"
    )
    dist_line = (
        f"자원 적정성 분류 — {ko['under_provisioned']} {under} · {ko['over_provisioned']} {over}"
        f" · {ko['idle']} {idle} · {ko['optimal']} {optimal}"
    )
    if insufficient:
        dist_line += f" · {ko['insufficient_data']} {insufficient}"
    bullets = [
        f"등록 서버 {overview.total}대 ({resource})",
        f"온라인 {overview.online}대 | 오프라인 {overview.offline}대",
        dist_line,
    ]

    efficiency = over + idle
    if under:
        cause = _under_cause_summary(under_hosts)
        if cause:
            bullets.append(f"{ko['under_provisioned']} — {cause}")
        else:
            bullets.append(f"{ko['under_provisioned']} — 관측 부하 대비 할당 자원 부족")
    elif efficiency:
        grp = f"{ko['over_provisioned']}·{ko['idle']}"
        bullets.append(f"{grp} {efficiency}대 — 관측 부하 대비 할당 자원 여유")
    if attention.os_eol_warnings:
        bullets.append(f"OS 지원 종료 {len(attention.os_eol_warnings)}대")
    return bullets


def to_environment_report(
    *,
    view: ReportView,
    time_range: str,
    anchor_at: datetime,
    overview: EnvironmentOverview,
    attention: AttentionSignals,
    base: ReportSummary,
    details: list[ServerDetail],
    generated_at: datetime,
    action: ActionTargets,
    trend: list[JsonObject] | None = None,
) -> EnvironmentReportSummary:
    classification_dist = _count_classifications(base.rows)

    classified_total = sum(c.count for c in classification_dist)
    for c in classification_dist:
        c.pct = round((c.count / classified_total * 100), 1) if classified_total else 0.0
    os_dist = _count_os(details)
    os_family_dist = _to_distribution_bars(overview.os_distribution, OS_FAMILY_LABEL_KO)
    # Linux/Windows 는 0대여도 노출 — 없는 축을 숨기면 화면만으로 기능 범위를 알 수 없다(#E9).
    _os_present = {b.label for b in os_family_dist}
    for _key in ("linux", "windows"):
        if OS_FAMILY_LABEL_KO[_key] not in _os_present:
            os_family_dist.append(DistributionBar(label=OS_FAMILY_LABEL_KO[_key], count=0, pct=0.0))

    _eol_os = Counter(w.meta_text.split(" · ", 1)[0] for w in attention.os_eol_warnings)
    os_eol_breakdown_label = " · ".join(f"{os} {n}대" for os, n in _eol_os.most_common())
    top_risks = _select_top_risks(base.rows, view)
    env_metrics = _build_env_metrics(overview)
    under_hosts = [h for h in action.hosts if h.classification == "under_provisioned"]
    summary = _env_summary_bullets(overview, attention, classification_dist, under_hosts)

    attention_hosts = _extract_attention_hosts(attention, base.rows)
    capacity_imminent = _extract_capacity_imminent(base.rows)
    agent_versions_label = ", ".join(sorted({(d.agent_version or "미상") for d in details}))
    topology = build_network_topology(details)
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
        top_risks=top_risks,
        action=action,
        env_metrics=env_metrics,
        summary_bullets_env=summary,
        service_catalog=_aggregate_service_catalog(base.rows),
        attention_hosts=attention_hosts,
        capacity_imminent=capacity_imminent,
        top_risks_count=len(top_risks),
        attention_hosts_count=len(attention_hosts),
        capacity_imminent_count=len(capacity_imminent),
        os_eol_count=len(attention.os_eol_warnings),
        os_eol_breakdown_label=os_eol_breakdown_label,
        agent_versions_label=agent_versions_label,
        topology=topology,
        trend=trend or [],
    )
