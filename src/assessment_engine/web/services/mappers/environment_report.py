"""환경 보고서 mapper — ReportSummary + 기존 helper 결과를 EnvironmentReportSummary 로 합성 (P2).

server scope 보고서와 분리된 high-level 양식 — 분류 분포·OS 분포·top risk·view 별 요약.
색·라벨 단일 진실: `assessment_engine.recommendation`.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from assessment_engine import recommendation
from assessment_engine.db.dtos.outbound import ServerDetail
from assessment_engine.db.repositories.base_diagnostic_repository import (
    DIAGNOSTIC_RANGE_LABEL_KR,
)
from assessment_engine.web.services.mappers.shared import (
    _CAPACITY_IMMINENT_DAYS,
    ReportView,
    UTIL_GAUGE_COLOR,
    format_net_rate,
)
from assessment_engine.web.services.mappers.shared import (
    _DONUT_SEGMENT_DEFS as _PROVISIONING_SEGMENT_DEFS,
)
from assessment_engine.web.services.mappers.topology import build_network_topology
from assessment_engine.web.services.service_classifier import SERVICE_CATEGORIES, SINGLE_INSTANCE_CATEGORIES
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
    OsCount,
    ServiceCatalogGroup,
    ServiceHost,
    ServiceNameCount,
)
from assessment_engine.web.view_models.report import ReportRowItem, ReportSummary

# `_PROVISIONING_SEGMENT_DEFS` / `_CAPACITY_IMMINENT_DAYS` 단일 진실 = mappers/shared.py (#E8).
# 본 모듈은 import alias 만 — 환경 보고서·대시보드 도넛·보고서 row 색 통일 (T13).

# 분류별 조치 방향 — 분포 막대 desc 단일 진실 (label=분류명과 어휘 중복 회피, 조치만).
# 대시보드 도넛(_DONUT_SEGMENT_DEFS desc)과 분리 — 보고서 ClassificationCount 에만 적용.
_CLASS_ACTION_KO: dict[str, str] = {
    "under_provisioned": "사양 상향(증설) 검토",
    "over_provisioned": "사양 축소 검토",
    "idle": "용도 재평가",
    "shutdown": "종료 검토",
    "optimal": "적정",
    "insufficient_data": "평가 표본 부족",
}

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
            # 표시 라벨 = right-sizing 한국어 분류명 단일 진실(LABEL_KO). 영어 enum 노출 금지·평행 어휘 금지.
            label=recommendation.LABEL_KO.get(key, label),
            count=counts.get(key, 0),
            # 색 = 게이지 테마 단색 통일 (분류 막대 — 라벨이 의미 전달). _PROVISIONING_SEGMENT_DEFS 다색 미사용.
            color=UTIL_GAUGE_COLOR,
            # desc = 조치 방향만 (label 분류명과 어휘 중복 회피).
            description=_CLASS_ACTION_KO.get(key, description),
        )
        for key, label, color, description in _PROVISIONING_SEGMENT_DEFS
    ]


# OS family key -> 표시명. 구성 막대 라벨 단일 진실 (unknown 은 "미상").
_OS_FAMILY_LABEL: dict[str, str] = {"linux": "Linux", "windows": "Windows", "unknown": "미상"}


def _to_distribution_bars(
    counts: dict[str, int],
    label_map: dict[str, str] | None = None,
) -> list[DistributionBar]:
    """카테고리 카운트 dict -> 구성 분포 list (count DESC, label ASC tie-break).

    pct = 최대 count 대비 비율 — 현재 보고서 표시는 label+대수만 쓰나(게이지 폐기), 스냅샷 복원 호환 위해 필드 유지.
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


def build_metric_trend(cpu_series: list, mem_series: list, disk_series: list) -> list[dict]:
    """환경 CPU·메모리·디스크 시계열(MetricSeries) 세 개를 버킷 시각 기준 merge -> 차트 JS inline plain dict (P2).

    at 은 isoformat str (tojson·JS Date 파싱). 표본 없는 축은 None (차트 gap).
    """

    # SQL avg 는 numeric(Decimal) 반환 — JSON 직렬화(tojson) 위해 float 변환 + 소수 1자리.
    def _f(v):
        return round(float(v), 1) if v is not None else None

    cpu_by = {s.collected_at: _f(s.value) for s in cpu_series}
    mem_by = {s.collected_at: _f(s.value) for s in mem_series}
    disk_by = {s.collected_at: _f(s.value) for s in disk_series}
    timestamps = sorted(set(cpu_by) | set(mem_by) | set(disk_by))
    return [
        {"at": t.isoformat(), "cpu": cpu_by.get(t), "mem": mem_by.get(t), "disk": disk_by.get(t)} for t in timestamps
    ]


def _aggregate_service_catalog(rows: list[ReportRowItem]) -> list[ServiceCatalogGroup]:
    """선택 N대 base.rows 의 workload_groups 를 카테고리 기준 집계 — 카테고리별 서비스명·등장 서버 수 (P2).

    names_label 은 "nginx, gunicorn" comma-separated — split 후 서비스명별 등장 서버 수 카운트. 빈 names
    (listen-only 탐지, T15)는 카테고리만 (services=[]). 전 카테고리 노출(count 0 포함, #E9) — 색 없이
    "카테고리 N --- 서비스명 개수" 형식. total_count = 서비스 count 합.
    """
    # 일반 카테고리: 서비스명별 hosts. single_instance(container 등, E7): docker+containerd 등을 1 런타임 스택으로
    # 보아 카테고리 단위 서버당 1 (서비스명 합집합·서버 distinct 카운트) — 카테고리 카운트 정책과 일관.
    multi: dict[str, dict[str, list[ServiceHost]]] = {}
    single_names: dict[str, set[str]] = {}
    single_hosts: dict[str, list[ServiceHost]] = {}
    for r in rows:
        for g in r.workload_groups:
            host = ServiceHost(hostname=r.hostname, public_id=r.public_id)
            names = [n.strip() for n in (g.names_label or "").split(", ") if n.strip()]
            if g.category in SINGLE_INSTANCE_CATEGORIES:
                single_names.setdefault(g.category, set()).update(names)
                single_hosts.setdefault(g.category, []).append(host)
            else:
                cat_map = multi.setdefault(g.category, {})
                for n in names:
                    cat_map.setdefault(n, []).append(host)
    groups: dict[str, list[ServiceNameCount]] = {}
    for cat, cat_map in multi.items():
        groups[cat] = [ServiceNameCount(name=n, count=len(hosts), hosts=hosts) for n, hosts in sorted(cat_map.items())]
    for cat, hosts in single_hosts.items():
        label = ", ".join(sorted(single_names.get(cat, set()))) or cat
        groups[cat] = [ServiceNameCount(name=label, count=len(hosts), hosts=hosts)]
    # 전 워크로드 카테고리 노출(count 0 포함, #E9). total_count = 서비스 count 합 (카탈로그 정의 순).
    result = []
    for cat in SERVICE_CATEGORIES:
        services = groups.get(cat, [])
        result.append(ServiceCatalogGroup(category=cat, total_count=sum(s.count for s in services), services=services))
    return result


def _count_os(details: list[ServerDetail]) -> list[OsCount]:
    """ServerDetail.os_id + os_version 그룹 카운트, count DESC."""
    counts: Counter[str] = Counter()
    for d in details:
        parts = [p for p in [d.os_id, d.os_version] if p]
        label = " ".join(parts) if parts else "unknown"
        counts[label] += 1
    return [OsCount(os_display=label, count=n) for label, n in counts.most_common()]


def _build_env_metrics(overview: EnvironmentOverview, rows: list[ReportRowItem]) -> list[dict]:
    """환경 현황 메트릭 카드 5축 (P2) — 실시간 '현재 자원 현황' 축과 동기 (CPU·메모리·디스크·네트워크·디스크 I/O).

    값은 전부 보고서 윈도우 통계 — CPU/메모리/디스크 = environment_utilization(capacity-weighted) avg(+p95),
    네트워크/디스크 I/O = base.rows 의 per-server 윈도우 baseline 합(실시간 합산과 동일 의미, 윈도우 평균).
    plain dict list — 스냅샷 직렬화 시 trend 와 동일하게 복원 불요. 값 부재는 "—" placeholder (#E9).
    """
    util = {b.label: b.pct for b in overview.utilization}
    util_p95 = {b.label: b.pct for b in overview.utilization_p95}

    def _pct(v: float | None) -> str:
        return f"{v:.1f}%" if v is not None else "—"

    net_total: float | None = None
    iops_total: float | None = None
    for r in rows:
        if r.net_rx_kbps is not None or r.net_tx_kbps is not None:
            net_total = (net_total or 0.0) + (r.net_rx_kbps or 0.0) + (r.net_tx_kbps or 0.0)
        if r.disk_iops_baseline is not None:
            iops_total = (iops_total or 0.0) + r.disk_iops_baseline
    return [
        {"label": "CPU", "value": _pct(util.get("CPU")), "sub": f"p95 {_pct(util_p95.get('CPU'))}"},
        {"label": "메모리", "value": _pct(util.get("메모리")), "sub": f"p95 {_pct(util_p95.get('메모리'))}"},
        {"label": "디스크", "value": _pct(util.get("디스크")), "sub": ""},
        {"label": "네트워크", "value": format_net_rate(net_total) or "—", "sub": ""},
        {"label": "디스크 I/O", "value": f"{iops_total:.0f} IOPS" if iops_total is not None else "—", "sub": ""},
    ]


def _select_top_risks(rows: list[ReportRowItem], view: ReportView) -> list[ReportRowItem]:
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


_EFFICIENCY_PRIORITY: dict[str, int] = {"shutdown": 0, "idle": 1, "over_provisioned": 2}
_EFFICIENCY_TARGET_N = 30


def _select_efficiency_targets(rows: list[ReportRowItem]) -> list[ReportRowItem]:
    """효율화(감축·정리) 대상 호스트 — over/idle/shutdown 을 정리 시급도 순 상위 N.

    shutdown(거의 미사용) > idle(저활용) > over(과프로비전), 동순위는 cpu_p95 ASC(저활용 우선).
    """
    targets = [r for r in rows if r.recommendation in _EFFICIENCY_PRIORITY]
    return sorted(
        targets, key=lambda r: (_EFFICIENCY_PRIORITY[r.recommendation], r.cpu_p95_pct or 0.0)
    )[:_EFFICIENCY_TARGET_N]


@dataclass
class EfficiencySummary:
    """효율화 검토 대상 표 데이터 — 보고서·자원 평가 공용 단일 진실 (`build_efficiency_summary`).

    hosts: 정리 시급도 순 상위 N (표 행). target_count/vcpus/memory_gb: 전체(절단 전) 대상 수·점유 자원 합.
    """

    hosts: list[ReportRowItem]
    hosts_count: int
    target_count: int
    target_vcpus: int
    target_memory_gb: float


def build_efficiency_summary(rows: list[ReportRowItem]) -> EfficiencySummary:
    """효율화 대상 호스트(top-N) + 점유 자원 합 — over/idle/shutdown 단일 산식.

    보고서(`to_environment_report`)와 자원 평가 페이지(`get_environment_assessment`)가 공유 — 분류·정렬
    어휘 일관. 점유 자원 합(target_*)은 절단 전 전체 대상 기준, hosts 는 상위 N.
    """
    hosts = _select_efficiency_targets(rows)
    eff_rows = [r for r in rows if r.recommendation in _EFFICIENCY_PRIORITY]
    return EfficiencySummary(
        hosts=hosts,
        hosts_count=len(hosts),
        target_count=len(eff_rows),
        target_vcpus=sum(r.cpu_cores or 0 for r in eff_rows),
        target_memory_gb=round(sum(r.mem_total_gb or 0.0 for r in eff_rows), 1),
    )


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


# 자원 부족 trigger badge 정의 순서(스왑·CPU·메모리·Load·디스크, attention.to_capacity_warning_item) 보존 — 요약 표시 안정화.
_UNDER_CAUSE_ORDER = ("스왑", "CPU", "메모리", "Load", "디스크")


def _under_cause_summary(under_hosts: list[CapacityWarningItem]) -> str:
    """자원 부족 호스트들의 active trigger 를 원인별 집계 -> "스왑 2대 · CPU 1대".

    원인 라벨은 CapacityTriggerBadge.label 단일 진실 재사용 (요약 전용 매핑 신설 안 함, P2).
    한 호스트가 복수 원인이면 각 원인에 1대씩 누적 — 합이 자원 부족 대수보다 클 수 있음(원인 가시화 목적).
    """
    counts: dict[str, int] = {}
    for h in under_hosts:
        for t in h.triggers:
            if t.active:
                counts[t.label] = counts.get(t.label, 0) + 1
    return " · ".join(f"{lbl} {counts[lbl]}대" for lbl in _UNDER_CAUSE_ORDER if lbl in counts)


def _env_summary_bullets(
    overview: EnvironmentOverview,
    attention: AttentionSignals,
    classification_dist: list[ClassificationCount],
    under_hosts: list[CapacityWarningItem],
) -> list[str]:
    """환경 단위 정성 요약 — customer/engineer 동일 내용 (양식 무관 동기화)."""
    classified = {c.key: c.count for c in classification_dist}
    under = classified.get("under_provisioned", 0)
    over = classified.get("over_provisioned", 0)
    idle = classified.get("idle", 0)
    shutdown = classified.get("shutdown", 0)
    optimal = classified.get("optimal", 0)
    insufficient = classified.get("insufficient_data", 0)

    # 분류 어휘 LABEL_KO 단일 진실(분포 막대·표와 동일 어휘). 운영 신호는 OS 지원 종료만(C1) —
    # gap/agent_unstable 전역 신호는 window 의미 불일치로 보고서·요약 미표시.
    ko = recommendation.LABEL_KO
    resource = (
        f"vCPU {overview.total_vcpus} / 메모리 {overview.total_memory_gb:.1f} GB / 디스크 {overview.total_disk_gb} GB"
    )
    dist_line = (
        f"자원 적정성 분류 — {ko['under_provisioned']} {under} · {ko['over_provisioned']} {over}"
        f" · {ko['idle']} {idle} · {ko['shutdown']} {shutdown} · {ko['optimal']} {optimal}"
    )
    if insufficient:
        dist_line += f" · {ko['insufficient_data']} {insufficient}"
    bullets = [
        f"등록 서버 {overview.total}대 ({resource})",
        f"온라인 {overview.online}대 / 오프라인 {overview.offline}대",
        dist_line,
    ]
    # 주요 현상 강조 — 조치 지시 없이 현상·진단만 (분류명 그대로).
    efficiency = over + idle + shutdown
    if under:
        cause = _under_cause_summary(under_hosts)
        if cause:
            bullets.append(f"{ko['under_provisioned']} — {cause}")
        else:
            bullets.append(f"{ko['under_provisioned']} — 관측 부하 대비 할당 자원 부족")
    elif efficiency:
        grp = f"{ko['over_provisioned']}·{ko['idle']}·{ko['shutdown']}"
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
    under_provisioned_hosts: list[CapacityWarningItem] | None = None,
    trend: list[dict] | None = None,
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
    # Linux/Windows 는 0대여도 항상 노출 (#E9 발화 — "윈도우 0대" 표기). count DESC 뒤에 0대 보강.
    _os_present = {b.label for b in os_family_dist}
    for _key in ("linux", "windows"):
        if _OS_FAMILY_LABEL[_key] not in _os_present:
            os_family_dist.append(DistributionBar(label=_OS_FAMILY_LABEL[_key], count=0, pct=0.0))
    workload_dist = _to_distribution_bars(overview.role_distribution)
    # OS 지원 종료 OS별 집계 (customer 나열) — os_eol_warnings.meta_text="{os} · EOL {date}" 에서 os 추출, 대수 DESC.
    _eol_os = Counter(w.meta_text.split(" · ", 1)[0] for w in attention.os_eol_warnings)
    os_eol_breakdown_label = " · ".join(f"{os} {n}대" for os, n in _eol_os.most_common())
    top_risks = _select_top_risks(base.rows, view)
    efficiency = build_efficiency_summary(base.rows)
    env_metrics = _build_env_metrics(overview, base.rows)
    under_hosts = (
        under_provisioned_hosts if under_provisioned_hosts is not None else list(overview.under_provisioned_hosts)
    )
    summary = _env_summary_bullets(overview, attention, classification_dist, under_hosts)
    # engineer 보고서 전용 — 운영 신호 호스트 통합 / capacity 임박 / 표본 부족.
    # customer 도 동일 헬퍼 호출 (로직 단일, 미사용 필드는 template 분기로 노출 안 함).
    attention_hosts = _extract_attention_hosts(attention, base.rows)
    capacity_imminent = _extract_capacity_imminent(base.rows)
    # 고객 의사결정 보조 (기존 분류·신호 단일 진실 재사용, 새 분류 도입 0).
    insufficient_count = sum(1 for r in base.rows if r.recommendation == "insufficient_data")
    evaluated_count = len(base.rows) - insufficient_count
    # 에이전트 버전 목록 (중복 제거·정렬, 미상 포함) — 관리 현황. 어느 호스트인지는 미표시.
    agent_versions_label = ", ".join(sorted({(d.agent_version or "미상") for d in details}))
    # 네트워크 토폴로지 — 발행 시점 정적 스냅샷 (대시보드와 동일 mapper 단일 진실).
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
        workload_dist=workload_dist,
        workload_unknown_count=overview.role_unknown_count,
        workload_identified_count=overview.total - overview.role_unknown_count,
        top_risks=top_risks,
        efficiency_hosts=efficiency.hosts,
        env_metrics=env_metrics,
        summary_bullets_env=summary,
        under_provisioned_hosts=under_hosts,
        under_provisioned_metric_labels=[m.label for m in under_hosts[0].metrics] if under_hosts else [],
        service_catalog=_aggregate_service_catalog(base.rows),
        attention_hosts=attention_hosts,
        capacity_imminent=capacity_imminent,
        # 템플릿 P3 회피 — 카운트 mapper precompute (#E1 P3).
        top_risks_count=len(top_risks),
        efficiency_hosts_count=efficiency.hosts_count,
        attention_hosts_count=len(attention_hosts),
        capacity_imminent_count=len(capacity_imminent),
        under_provisioned_hosts_count=len(under_hosts),
        evaluated_count=evaluated_count,
        os_eol_count=len(attention.os_eol_warnings),
        os_eol_breakdown_label=os_eol_breakdown_label,
        efficiency_target_count=efficiency.target_count,
        efficiency_target_vcpus=efficiency.target_vcpus,
        efficiency_target_memory_gb=efficiency.target_memory_gb,
        agent_versions_label=agent_versions_label,
        topology=topology,
        trend=trend or [],
    )
