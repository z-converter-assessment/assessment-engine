"""보고서 정적 스냅샷 직렬화 — 발행 시점 ViewModel <-> diagnostic_jobs.result JSONB.

발행(POST emit) 시 mapper 가 모든 파생(badge_class·pct·dash_length 등)을 채운 완성 ViewModel 을
JSONB dict 로 저장(`report_summary_to_dict`·`env_report_to_dict`), GET(세부·이력 포함) 시 저장된 dict 를
ViewModel 로 복원(`*_from_dict`)해 정적 렌더. 재계산·재진단 없음 — 발행 시점
데이터 그대로 (요구: 정적 보관 + 이력 동적변화 0).

cache_serializer.py 와 동일 패턴 (asdict + json datetime, 역직렬화 nested 재구성).
AttentionSignals.catalog/has_any 는 @property 라 asdict 누락 — 역직렬화 시 ViewModel 재구성으로
property 자동 복원 (dict 직접 template 전달 시 `attention.catalog` 접근이 깨짐).

result JSONB 구조·키·narrative entry 단일 진실은 `diagnostic.report_result` (worker 공유 계약).
본 모듈은 그 구조 안 `snapshot` 의 ViewModel <-> dict 직렬화만 담당.
"""

import dataclasses
import json
from datetime import datetime

# result 구조 계약(키·dict 조립)은 diagnostic.report_result 단일 진실 — worker(diagnostic 패키지)가
# web.services 를 import 못 하므로 중립 모듈에 분리. 본 모듈은 ViewModel <-> dict 직렬화만 담당.
from assessment_engine.diagnostic.report_result import (  # noqa: F401 (re-export)
    ENV_NARRATIVE_KEY,
    REPORT_KIND_ENV,
    build_narrative_entry,
    build_report_result,
)
from assessment_engine.web.view_models.attention import (
    AttentionRow,
    AttentionSignals,
    CapacityTriggerBadge,
    CapacityWarningItem,
    EnvironmentOverview,
    RiskDonutSegment,
    UtilizationBar,
)
from assessment_engine.web.view_models.environment_report import (
    AttentionHostItem,
    CapacityImminentItem,
    ClassificationCount,
    CpuBreakdown,
    DistributionBar,
    EnvironmentReportSummary,
    InsufficientHostItem,
    MemoryBreakdown,
    OsCount,
    ServerInventory,
    ServiceCatalogGroup,
    ServiceHost,
    ServiceNameCount,
    VolumeUsage,
)
from assessment_engine.web.view_models.report import (
    ReportListenItem,
    ReportRowItem,
    ReportServiceUnit,
    ReportSummary,
    ReportTotals,
    ReportWorkloadGroup,
)
from assessment_engine.web.view_models.server import IpAddr
from assessment_engine.web.view_models.topology import NetworkTopology


def _json_default(obj: object) -> str:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Cannot serialize {type(obj)}")


def _dt(v: object) -> datetime | None:
    return datetime.fromisoformat(v) if isinstance(v, str) else v  # type: ignore[arg-type]


def _to_jsonable(vm: object) -> dict:
    """dataclass ViewModel -> JSONB 저장 가능 dict (datetime -> ISO str, nested 재귀)."""
    return json.loads(json.dumps(dataclasses.asdict(vm), default=_json_default))


# ──────────────────────────────────────────────────────────────────────────
# ReportSummary (server scope 보고서 base — EnvironmentReportSummary.base 직렬화·복원에 사용)
# ──────────────────────────────────────────────────────────────────────────
def report_summary_to_dict(vm: ReportSummary) -> dict:
    return _to_jsonable(vm)


def _report_row_from_dict(r: dict) -> ReportRowItem:
    """ReportRowItem 복원 — nested 구동 서비스 3 필드(list[dataclass]) 재구성 포함."""
    data = dict(r)
    data["workload_groups"] = [ReportWorkloadGroup(**g) for g in data.get("workload_groups") or []]
    data["service_units"] = [ReportServiceUnit(**u) for u in data.get("service_units") or []]
    data["listen_ports_detail"] = [ReportListenItem(**p) for p in data.get("listen_ports_detail") or []]
    return ReportRowItem(**data)


def report_summary_from_dict(d: dict) -> ReportSummary:
    data = dict(d)
    data["rows"] = [_report_row_from_dict(r) for r in data.get("rows") or []]
    totals = data.get("totals")
    data["totals"] = ReportTotals(**totals) if totals else ReportTotals(0, 0.0, 0)
    data["generated_at"] = _dt(data.get("generated_at"))
    data["anchor_at"] = _dt(data.get("anchor_at"))
    return ReportSummary(**data)


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
    data["workload_dist"] = [DistributionBar(**b) for b in data.get("workload_dist") or []]
    topo = data.get("topology")
    data["topology"] = NetworkTopology(**topo) if topo else None
    # trend 는 plain dict list (at=isoformat str) — 라운드트립 시 그대로 보존 (복원 불필요).
    data["top_risks"] = [_report_row_from_dict(r) for r in data.get("top_risks") or []]
    uph = data.get("under_provisioned_hosts") or []
    data["under_provisioned_hosts"] = [_capacity_warning_from_dict(c) for c in uph]
    data["service_catalog"] = [
        ServiceCatalogGroup(
            category=g["category"],
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
    data["insufficient_hosts"] = [InsufficientHostItem(**i) for i in data.get("insufficient_hosts") or []]
    data["anchor_at"] = _dt(data.get("anchor_at"))
    data["generated_at"] = _dt(data.get("generated_at"))
    si = data.get("server_inventory")
    if si:
        sid = dict(si)
        sid["ip_internal"] = [IpAddr(**a) for a in sid.get("ip_internal") or []]
        sid["ip_external"] = [IpAddr(**a) for a in sid.get("ip_external") or []]
        sid["boot_time"] = _dt(sid.get("boot_time"))
        data["server_inventory"] = ServerInventory(**sid)
    data["volumes"] = [VolumeUsage(**v) for v in data.get("volumes") or []]
    mb = data.get("memory_breakdown")
    if mb:
        data["memory_breakdown"] = MemoryBreakdown(**mb)
    cb = data.get("cpu_breakdown")
    if cb:
        data["cpu_breakdown"] = CpuBreakdown(**cb)
    return EnvironmentReportSummary(**data)


def _overview_from_dict(d: dict) -> EnvironmentOverview:
    data = dict(d)
    data["utilization"] = [UtilizationBar(**u) for u in data.get("utilization") or []]
    data["risk_donut"] = [RiskDonutSegment(**s) for s in data.get("risk_donut") or []]
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
    data["triggers"] = [CapacityTriggerBadge(**t) for t in data.get("triggers") or []]
    return CapacityWarningItem(**data)
