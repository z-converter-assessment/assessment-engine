"""보고서 정적 스냅샷 직렬화 — 발행 시점 ViewModel <-> diagnostic_jobs.result JSONB.

발행(POST emit) 시 mapper 가 모든 파생(badge_class·pct·dash_length 등)을 채운 완성 ViewModel 을
JSONB dict 로 저장(`report_summary_to_dict`·`env_report_to_dict`), GET(세부·이력 포함) 시 저장된 dict 를
ViewModel 로 복원(`*_from_dict`)해 정적 렌더. 재계산·재진단 없음 — 발행 시점
데이터 그대로 (요구: 정적 보관 + 이력 동적변화 0).

cache_serializer.py 와 동일 패턴 (asdict + json datetime, 역직렬화 nested 재구성).
AttentionSignals.catalog/has_any 는 @property 라 asdict 누락 — 역직렬화 시 ViewModel 재구성으로
property 자동 복원 (dict 직접 template 전달 시 `attention.catalog` 접근이 깨짐).

result JSONB 구조·키 단일 진실은 `report/result.py`.
본 모듈은 그 구조 안 `snapshot` 의 ViewModel <-> JsonObject 직렬화만 담당.
"""

import dataclasses

# result 구조 계약(키·dict 조립)은 report/result.py 단일 진실 — web view_models 에 의존하지
# 않는 중립 모듈에 분리.
from assessment_engine.json_types import JsonObject, json_list, json_obj
from assessment_engine.web.services.serialization import parse_dt, to_jsonable
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


# --------------------------------------------------------------------------
# ReportSummary (server scope 보고서 base — EnvironmentReportSummary.base 직렬화·복원에 사용)
# --------------------------------------------------------------------------
def report_summary_to_dict(vm: ReportSummary) -> JsonObject:
    return to_jsonable(vm)


def _drop_unknown_fields(cls: type, data: JsonObject) -> JsonObject:
    """저장된 스냅샷에 남아있으나 현재 dataclass 에 없는 키 제거 — 보고서는 발행 시점 정적 스냅샷이라

    (C1) 필드를 나중에 빼도 과거 스냅샷의 JSONB 는 옛 키를 그대로 보관한다. `cls(**data)` 가 그 키를
    unexpected keyword argument 로 거부하지 않도록 현재 스키마 기준으로 걸러낸다(에이전트 wire 계약의
    `extra=ignore` 와 동일 취지, #B).
    """
    known = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in known}


def _build[T](cls: type[T], data: JsonObject) -> T:
    """dict -> dataclass 재구성 단일 진입점 — 모든 nested `cls(**data)` 는 본 helper 경유 의무.

    `_drop_unknown_fields` 를 항상 적용해 필드 제거 마이그레이션 후 과거 스냅샷의 잔존 키가 TypeError 로
    복원을 깨지 않는다(#C1 정적 스냅샷 removal-tolerance). 명시 kwargs 재구성(PeriodResource 등 get(key,
    default))은 이미 제거 내성이 있어 본 helper 불요 — spread(`**`)만 본 함수로.
    """
    return cls(**_drop_unknown_fields(cls, data))


# 보고서는 발행 시점 정적 스냅샷이라 과거 값이 그대로 되살아난다. 지원 단계 어휘가 바뀌기 전에
# 발행된 스냅샷은 옛 문자열을 갖고 있고, 표시 계층이 새 값만 분기하므로 여기서 옮겨 준다.
_LEGACY_OS_EOL_STATUS = {"eol": "ended", "extended": "security_only", "supported": "full"}


def _report_row_from_dict(r: JsonObject) -> ReportRowItem:
    """ReportRowItem 복원 — nested 구동 서비스 2 필드(list[dataclass]) 재구성 포함."""
    data = dict(r)
    data["workload_groups"] = [_build(ReportWorkloadGroup, g) for g in json_list(data, "workload_groups")]
    data["listen_ports_detail"] = [_build(ReportListenItem, p) for p in json_list(data, "listen_ports_detail")]
    status = data.get("os_eol_status")
    if status in _LEGACY_OS_EOL_STATUS:
        data["os_eol_status"] = _LEGACY_OS_EOL_STATUS[status]
    return _build(ReportRowItem, data)


def report_summary_from_dict(d: JsonObject) -> ReportSummary:
    data = dict(d)
    data["rows"] = [_report_row_from_dict(r) for r in json_list(data, "rows")]
    totals = data.get("totals")
    data["totals"] = _build(ReportTotals, totals) if totals else ReportTotals(0, 0.0, 0)
    data["generated_at"] = parse_dt(data.get("generated_at"))
    data["anchor_at"] = parse_dt(data.get("anchor_at"))
    return _build(ReportSummary, data)


# --------------------------------------------------------------------------
# EnvironmentReportSummary (환경 + 단일서버 보고서 — reports/environment.html, servers/single_report.html)
# --------------------------------------------------------------------------
def env_report_to_dict(vm: EnvironmentReportSummary) -> JsonObject:
    return to_jsonable(vm)


def env_report_from_dict(d: JsonObject) -> EnvironmentReportSummary:
    data = dict(d)
    data["overview"] = _overview_from_dict(data["overview"])
    data["attention"] = _attention_from_dict(data["attention"])
    data["base"] = report_summary_from_dict(data["base"])
    data["classification_dist"] = [_build(ClassificationCount, c) for c in json_list(data, "classification_dist")]
    data["os_distribution"] = [_build(OsCount, o) for o in json_list(data, "os_distribution")]
    data["os_family_dist"] = [_build(DistributionBar, b) for b in json_list(data, "os_family_dist")]
    topo = data.get("topology")
    if topo:
        # subnets nested 복원 (SubnetGroup/SubnetHost) — 보고서 서브넷 요약 표가 .net_key/.host_count 접근.
        # elements 는 Cytoscape용 plain dict list 라 그대로 보존.
        topo["subnets"] = [
            SubnetGroup(
                net_key=s["net_key"],
                host_count=s.get("host_count", 0),
                hosts=[_build(SubnetHost, h) for h in json_list(s, "hosts")],
            )
            for s in json_list(topo, "subnets")
        ]
        data["topology"] = _build(NetworkTopology, topo)
    else:
        data["topology"] = None
    # trend 는 plain dict list (at=isoformat str) — 라운드트립 시 그대로 보존 (복원 불필요).
    data["top_risks"] = [_report_row_from_dict(r) for r in json_list(data, "top_risks")]
    # 통합 조치 대상 표 — hosts nested(CapacityWarningItem, metrics 포함) 복원.
    data["action"] = _action_targets_from_dict(json_obj(data, "action"))
    data["service_catalog"] = [
        ServiceCatalogGroup(
            category=g["category"],
            total_count=g.get("total_count", 0),
            services=[
                ServiceNameCount(
                    name=s["name"], count=s["count"], hosts=[_build(ServiceHost, h) for h in json_list(s, "hosts")]
                )
                for s in json_list(g, "services")
            ],
        )
        for g in json_list(data, "service_catalog")
    ]
    data["attention_hosts"] = [_build(AttentionHostItem, a) for a in json_list(data, "attention_hosts")]
    data["capacity_imminent"] = [_build(CapacityImminentItem, c) for c in json_list(data, "capacity_imminent")]
    data["anchor_at"] = parse_dt(data.get("anchor_at"))
    data["generated_at"] = parse_dt(data.get("generated_at"))
    si = data.get("server_inventory")
    if si:
        sid = dict(si)
        sid["ip_internal"] = [_build(IpAddr, a) for a in json_list(sid, "ip_internal")]
        sid["ip_external"] = [_build(IpAddr, a) for a in json_list(sid, "ip_external")]
        sid["boot_time"] = parse_dt(sid.get("boot_time"))
        sid["agent_started_at"] = parse_dt(sid.get("agent_started_at"))
        sid["last_seen_at"] = parse_dt(sid.get("last_seen_at"))
        data["server_inventory"] = _build(ServerInventorySnapshot, sid)
    mb = data.get("memory_breakdown")
    if mb:
        data["memory_breakdown"] = _build(MemoryBreakdown, mb)
    cb = data.get("cpu_breakdown")
    if cb:
        data["cpu_breakdown"] = _build(CpuBreakdown, cb)
    pa = data.get("period_assessment")
    data["period_assessment"] = _period_assessment_from_dict(pa) if pa else None
    data["storage_tree"] = [_storage_node_from_dict(n) for n in json_list(data, "storage_tree")]
    data["network_interfaces"] = [_network_interface_from_dict(n) for n in json_list(data, "network_interfaces")]
    return _build(EnvironmentReportSummary, data)


def _period_signal_rows(rows: list[JsonObject] | None) -> list[PeriodSignalRow]:
    return [_build(PeriodSignalRow, r) for r in rows or []]


def _period_assessment_from_dict(d: JsonObject) -> PeriodAssessment:
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
                for g in json_list(r, "extra_groups")
            ],
            error_rows=[_build(PeriodErrorRow, e) for e in json_list(r, "error_rows")],
            verdict_label2=r.get("verdict_label2", ""),
            verdict_color2=r.get("verdict_color2", ""),
        )
        for r in json_list(d, "resources")
    ]
    return PeriodAssessment(
        resources=resources,
        error_rows=[_build(PeriodErrorRow, e) for e in json_list(d, "error_rows")],
        window_days=d.get("window_days", 0),
        classification_label=d.get("classification_label", ""),
        classification_color=d.get("classification_color", ""),
    )


def _storage_node_from_dict(d: JsonObject) -> StorageNode:
    """StorageNode 복원 — children 재귀 트리(storage.html 과 동일 구조)."""
    data = dict(d)
    data["children"] = [_storage_node_from_dict(c) for c in json_list(data, "children")]
    return _build(StorageNode, data)


def _network_interface_from_dict(d: JsonObject) -> NetworkInterfaceInfo:
    data = dict(d)
    data["addresses"] = [_build(NetIfaceAddress, a) for a in json_list(data, "addresses")]
    return _build(NetworkInterfaceInfo, data)


def _overview_from_dict(d: JsonObject) -> EnvironmentOverview:
    data = dict(d)
    data["utilization"] = [_build(UtilizationBar, u) for u in json_list(data, "utilization")]
    data["utilization_p95"] = [_build(UtilizationBar, u) for u in json_list(data, "utilization_p95")]
    data["risk_donut"] = [_build(RiskDonutSegment, s) for s in json_list(data, "risk_donut")]
    data["saturation_donuts"] = [_build(SaturationDonut, s) for s in json_list(data, "saturation_donuts")]
    data["error_fleet"] = [_build(FleetErrorItem, e) for e in json_list(data, "error_fleet")]
    uph = json_list(data, "under_provisioned_hosts")
    data["under_provisioned_hosts"] = [_capacity_warning_from_dict(c) for c in uph]
    return _build(EnvironmentOverview, data)


def _attention_from_dict(d: JsonObject) -> AttentionSignals:
    # catalog/has_any 는 @property — field 만 재구성하면 자동 복원.
    return AttentionSignals(
        gap_warnings=[_attention_row_from_dict(r) for r in json_list(d, "gap_warnings")],
        os_eol_warnings=[_attention_row_from_dict(r) for r in json_list(d, "os_eol_warnings")],
        agent_unstable=[_attention_row_from_dict(r) for r in json_list(d, "agent_unstable")],
    )


def _attention_row_from_dict(d: JsonObject) -> AttentionRow:
    data = dict(d)
    data["meta_at"] = parse_dt(data.get("meta_at"))
    return _build(AttentionRow, data)


def _capacity_warning_from_dict(d: JsonObject) -> CapacityWarningItem:
    data = dict(d)
    # active_causes 는 list[str] — 스칼라라 별도 복원 불요. 지금 dataclass 에 없는 필드를 들고 있는 과거
    # 스냅샷은 _drop_unknown_fields 가 걸러낸다.
    return _build(CapacityWarningItem, data)


def _action_targets_from_dict(d: JsonObject) -> ActionTargets:
    data = dict(d)
    # hosts = CapacityWarningItem list. 지금 dataclass 에 없는 키는 _drop_unknown_fields 가 걸러낸다.
    data["hosts"] = [_capacity_warning_from_dict(c) for c in json_list(data, "hosts")]
    return _build(ActionTargets, data)
