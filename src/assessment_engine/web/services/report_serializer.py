"""보고서 정적 스냅샷 직렬화 — 발행 시점 ViewModel <-> diagnostic_jobs.result JSONB.

발행(POST emit) 시 mapper 가 모든 파생(badge_class·pct·dash_length 등)을 채운 완성 ViewModel 을
JSONB dict 로 저장(`report_summary_to_dict`·`env_report_to_dict`), GET(세부·이력 포함) 시 저장된 dict 를
ViewModel 로 복원(`*_from_dict`)해 정적 렌더. 재계산·재진단 없음 — 발행 시점
데이터 그대로 (요구: 정적 보관 + 이력 동적변화 0).

cache_serializer.py 와 동일 패턴 (asdict + json datetime, 역직렬화 nested 재구성).
AttentionSignals.catalog/has_any 는 @property 라 asdict 누락 — 역직렬화 시 ViewModel 재구성으로
property 자동 복원 (dict 직접 template 전달 시 `attention.catalog` 접근이 깨짐).

result JSONB 구조·키 단일 진실은 `diagnostic.report_result`.
본 모듈은 그 구조 안 `snapshot` 의 ViewModel <-> dict 직렬화만 담당.
"""

import dataclasses

# result 구조 계약(키·dict 조립)은 diagnostic.report_result 단일 진실 — web view_models 에 의존하지
# 않는 중립 모듈에 분리.
from assessment_engine.diagnostic.report_result import (  # noqa: F401 (re-export)
    REPORT_KIND_ENV,
    build_report_result,
)
from assessment_engine.web.services.serialization_util import parse_dt as _dt
from assessment_engine.web.services.serialization_util import to_jsonable as _to_jsonable
from assessment_engine.web.view_models.attention import (
    ActionTargets,
    AttentionRow,
    AttentionSignals,
    CapacityWarningItem,
    EnvironmentOverview,
    FleetErrorItem,
    RiskDonutSegment,
    SaturationDonut,
    UtilizationBar,
)
from assessment_engine.web.view_models.environment_report import (
    AttentionHostItem,
    CapacityImminentItem,
    ClassificationCount,
    CpuBreakdown,
    DistributionBar,
    EnvironmentReportSummary,
    MemoryBreakdown,
    OsCount,
    ServerInventorySnapshot,
    ServiceCatalogGroup,
    ServiceHost,
    ServiceNameCount,
)
from assessment_engine.web.view_models.metric import (
    PeriodAssessment,
    PeriodErrorRow,
    PeriodExtraGroup,
    PeriodResource,
    PeriodSignalRow,
)
from assessment_engine.web.view_models.report import (
    ReportListenItem,
    ReportRowItem,
    ReportSummary,
    ReportTotals,
    ReportWorkloadGroup,
)
from assessment_engine.web.view_models.server import IpAddr, NetIfaceAddress, NetworkInterfaceInfo, StorageNode
from assessment_engine.web.view_models.topology import NetworkTopology, SubnetGroup, SubnetHost


# ──────────────────────────────────────────────────────────────────────────
# ReportSummary (server scope 보고서 base — EnvironmentReportSummary.base 직렬화·복원에 사용)
# ──────────────────────────────────────────────────────────────────────────
def report_summary_to_dict(vm: ReportSummary) -> dict:
    return _to_jsonable(vm)


def _drop_unknown_fields(cls: type, data: dict) -> dict:
    """저장된 스냅샷에 남아있으나 현재 dataclass 에 없는 키 제거 — 보고서는 발행 시점 정적 스냅샷이라

    (C1) 필드를 나중에 빼도 과거 스냅샷의 JSONB 는 옛 키를 그대로 보관한다. `cls(**data)` 가 그 키를
    unexpected keyword argument 로 거부하지 않도록 현재 스키마 기준으로 걸러낸다(에이전트 wire 계약의
    `extra=ignore` 와 동일 취지, #B).
    """
    known = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in known}


def _report_row_from_dict(r: dict) -> ReportRowItem:
    """ReportRowItem 복원 — nested 구동 서비스 2 필드(list[dataclass]) 재구성 포함."""
    data = dict(r)
    data["workload_groups"] = [ReportWorkloadGroup(**g) for g in data.get("workload_groups") or []]
    data["listen_ports_detail"] = [ReportListenItem(**p) for p in data.get("listen_ports_detail") or []]
    return ReportRowItem(**_drop_unknown_fields(ReportRowItem, data))


def report_summary_from_dict(d: dict) -> ReportSummary:
    data = dict(d)
    data["rows"] = [_report_row_from_dict(r) for r in data.get("rows") or []]
    totals = data.get("totals")
    data["totals"] = ReportTotals(**totals) if totals else ReportTotals(0, 0.0, 0)
    data["generated_at"] = _dt(data.get("generated_at"))
    data["anchor_at"] = _dt(data.get("anchor_at"))
    return ReportSummary(**_drop_unknown_fields(ReportSummary, data))


# ──────────────────────────────────────────────────────────────────────────
# EnvironmentReportSummary (환경 + 단일서버 보고서 — reports/environment.html, servers/single_report.html)
# ──────────────────────────────────────────────────────────────────────────
def env_report_to_dict(vm: EnvironmentReportSummary) -> dict:
    return _to_jsonable(vm)


def env_report_from_dict(d: dict) -> EnvironmentReportSummary:
    data = dict(d)
    data["overview"] = _overview_from_dict(data["overview"])
    data["attention"] = _attention_from_dict(data["attention"])
    data["base"] = report_summary_from_dict(data["base"])
    data["classification_dist"] = [ClassificationCount(**c) for c in data.get("classification_dist") or []]
    data["os_distribution"] = [OsCount(**o) for o in data.get("os_distribution") or []]
    data["os_family_dist"] = [DistributionBar(**b) for b in data.get("os_family_dist") or []]
    topo = data.get("topology")
    if topo:
        # subnets nested 복원 (SubnetGroup/SubnetHost) — 보고서 서브넷 요약 표가 .net_key/.host_count 접근.
        # elements 는 Cytoscape용 plain dict list 라 그대로 보존.
        topo["subnets"] = [
            SubnetGroup(
                net_key=s["net_key"],
                host_count=s.get("host_count", 0),
                hosts=[SubnetHost(**h) for h in s.get("hosts") or []],
            )
            for s in topo.get("subnets") or []
        ]
        data["topology"] = NetworkTopology(**topo)
    else:
        data["topology"] = None
    # trend 는 plain dict list (at=isoformat str) — 라운드트립 시 그대로 보존 (복원 불필요).
    data["top_risks"] = [_report_row_from_dict(r) for r in data.get("top_risks") or []]
    # 통합 조치 대상 표 — hosts nested(CapacityWarningItem, metrics 포함) 복원.
    data["action"] = _action_targets_from_dict(data.get("action") or {})
    data["service_catalog"] = [
        ServiceCatalogGroup(
            category=g["category"],
            total_count=g.get("total_count", 0),
            services=[
                ServiceNameCount(
                    name=s["name"], count=s["count"], hosts=[ServiceHost(**h) for h in s.get("hosts") or []]
                )
                for s in g.get("services") or []
            ],
        )
        for g in data.get("service_catalog") or []
    ]
    data["attention_hosts"] = [AttentionHostItem(**a) for a in data.get("attention_hosts") or []]
    data["capacity_imminent"] = [CapacityImminentItem(**c) for c in data.get("capacity_imminent") or []]
    data["anchor_at"] = _dt(data.get("anchor_at"))
    data["generated_at"] = _dt(data.get("generated_at"))
    si = data.get("server_inventory")
    if si:
        sid = dict(si)
        sid["ip_internal"] = [IpAddr(**a) for a in sid.get("ip_internal") or []]
        sid["ip_external"] = [IpAddr(**a) for a in sid.get("ip_external") or []]
        sid["boot_time"] = _dt(sid.get("boot_time"))
        sid["agent_started_at"] = _dt(sid.get("agent_started_at"))
        sid["last_seen_at"] = _dt(sid.get("last_seen_at"))
        data["server_inventory"] = ServerInventorySnapshot(**sid)
    mb = data.get("memory_breakdown")
    if mb:
        data["memory_breakdown"] = MemoryBreakdown(**mb)
    cb = data.get("cpu_breakdown")
    if cb:
        data["cpu_breakdown"] = CpuBreakdown(**cb)
    pa = data.get("period_assessment")
    data["period_assessment"] = _period_assessment_from_dict(pa) if pa else None
    data["storage_tree"] = [_storage_node_from_dict(n) for n in data.get("storage_tree") or []]
    data["network_interfaces"] = [_network_interface_from_dict(n) for n in data.get("network_interfaces") or []]
    return EnvironmentReportSummary(**_drop_unknown_fields(EnvironmentReportSummary, data))


def _period_signal_rows(rows: list[dict] | None) -> list[PeriodSignalRow]:
    return [PeriodSignalRow(**r) for r in rows or []]


def _period_assessment_from_dict(d: dict) -> PeriodAssessment:
    """PeriodAssessment 복원 — 자원별 util_rows/sat_rows/extra_groups/error_rows 중첩 재구성.

    단일 서버 보고서(engineer) 전용 스냅샷 필드 — 서버 상세 페이지의 get_period_assessment 는 항상 라이브
    재계산이라 이 복원 경로를 타지 않는다(본 함수는 report_serializer 전용).
    """
    resources = [
        PeriodResource(
            name=r["name"],
            util_rows=_period_signal_rows(r.get("util_rows")),
            util_over=r.get("util_over", 0),
            sat_rows=_period_signal_rows(r.get("sat_rows")),
            sat_over=r.get("sat_over", 0),
            has_util=r.get("has_util", True),
            detail_slug=r.get("detail_slug", ""),
            verdict_label=r.get("verdict_label", ""),
            verdict_color=r.get("verdict_color", ""),
            extra_groups=[
                PeriodExtraGroup(label=g["label"], rows=_period_signal_rows(g.get("rows")))
                for g in r.get("extra_groups") or []
            ],
            error_rows=[PeriodErrorRow(**e) for e in r.get("error_rows") or []],
            verdict_label2=r.get("verdict_label2", ""),
            verdict_color2=r.get("verdict_color2", ""),
        )
        for r in d.get("resources") or []
    ]
    return PeriodAssessment(
        resources=resources,
        error_rows=[PeriodErrorRow(**e) for e in d.get("error_rows") or []],
        window_days=d.get("window_days", 0),
        classification_label=d.get("classification_label", ""),
        classification_color=d.get("classification_color", ""),
    )


def _storage_node_from_dict(d: dict) -> StorageNode:
    """StorageNode 복원 — children 재귀 트리(storage.html 과 동일 구조)."""
    data = dict(d)
    data["children"] = [_storage_node_from_dict(c) for c in data.get("children") or []]
    return StorageNode(**data)


def _network_interface_from_dict(d: dict) -> NetworkInterfaceInfo:
    data = dict(d)
    data["addresses"] = [NetIfaceAddress(**a) for a in data.get("addresses") or []]
    return NetworkInterfaceInfo(**data)


def _overview_from_dict(d: dict) -> EnvironmentOverview:
    data = dict(d)
    data["utilization"] = [UtilizationBar(**u) for u in data.get("utilization") or []]
    data["utilization_p95"] = [UtilizationBar(**u) for u in data.get("utilization_p95") or []]
    data["risk_donut"] = [RiskDonutSegment(**s) for s in data.get("risk_donut") or []]
    data["saturation_donuts"] = [SaturationDonut(**s) for s in data.get("saturation_donuts") or []]
    data["error_fleet"] = [FleetErrorItem(**e) for e in data.get("error_fleet") or []]
    uph = data.get("under_provisioned_hosts") or []
    data["under_provisioned_hosts"] = [_capacity_warning_from_dict(c) for c in uph]
    return EnvironmentOverview(**data)


def _attention_from_dict(d: dict) -> AttentionSignals:
    # catalog/has_any 는 @property — field 만 재구성하면 자동 복원.
    return AttentionSignals(
        gap_warnings=[_attention_row_from_dict(r) for r in d.get("gap_warnings") or []],
        os_eol_warnings=[_attention_row_from_dict(r) for r in d.get("os_eol_warnings") or []],
        agent_unstable=[_attention_row_from_dict(r) for r in d.get("agent_unstable") or []],
    )


def _attention_row_from_dict(d: dict) -> AttentionRow:
    data = dict(d)
    data["meta_at"] = _dt(data.get("meta_at"))
    return AttentionRow(**data)


def _capacity_warning_from_dict(d: dict) -> CapacityWarningItem:
    data = dict(d)
    # active_causes 는 list[str] — 스칼라라 별도 복원 불요. metrics(구 필드, ADR 0056 이전 스냅샷)는
    # _drop_unknown_fields 가 걸러낸다 — CapacityMetric 자체도 폐기.
    return CapacityWarningItem(**_drop_unknown_fields(CapacityWarningItem, data))


def _action_targets_from_dict(d: dict) -> ActionTargets:
    data = dict(d)
    # hosts = CapacityWarningItem list. metric_labels(구 필드)는 _drop_unknown_fields 가 걸러낸다.
    data["hosts"] = [_capacity_warning_from_dict(c) for c in data.get("hosts") or []]
    return ActionTargets(**_drop_unknown_fields(ActionTargets, data))
