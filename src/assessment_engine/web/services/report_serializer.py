"""보고서 정적 스냅샷 직렬화 — 발행 시점 ViewModel <-> diagnostic_jobs.result JSONB.

발행(POST emit) 시 mapper 가 모든 파생(badge_class·pct·dash_length 등)을 채운 완성 ViewModel 을
JSONB dict 로 저장(`*_to_result_dict`), GET(세부·이력 포함) 시 저장된 dict 를 ViewModel 로
복원(`load_report_snapshot`)해 정적 렌더. 재계산·재진단 없음 — 발행 시점 데이터 그대로
(요구: 정적 보관 + 이력 동적변화 0).

cache_serializer.py 와 동일 패턴 (asdict + json datetime, 역직렬화 nested 재구성).
AttentionSignals.catalog/has_any 는 @property 라 asdict 누락 — 역직렬화 시 ViewModel 재구성으로
property 자동 복원 (dict 직접 template 전달 시 `attention.catalog` 접근이 깨짐).

result JSONB 구조 (job_type customer_report/engineer_report 공통):
  {
    "kind": "report_summary" | "env_report",   # 역직렬화 분기 (server N대 vs 환경/단일서버)
    "snapshot": {...},                          # ViewModel asdict (datetime ISO str)
    "view": "customer" | "engineer",
    "narrative": str | None,                    # engineer AI narrative (worker 가 채움)
    "narrative_status": "none"|"pending"|"succeeded"|"failed",  # customer=none
  }
"""

import dataclasses
import json
from datetime import datetime

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
    EnvironmentReportSummary,
    InsufficientHostItem,
    OsCount,
)
from assessment_engine.web.view_models.report import (
    ReportRowItem,
    ReportSummary,
    ReportTotals,
)

REPORT_KIND_SUMMARY = "report_summary"  # server scope N대 (ReportSummary)
REPORT_KIND_ENV = "env_report"  # 환경 + 단일서버 (EnvironmentReportSummary)


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
# result 구조 helper — 발행(POST emit)·worker·GET 공유 단일 진실
# ──────────────────────────────────────────────────────────────────────────
def build_report_result(
    *,
    kind: str,
    snapshot: dict,
    view: str,
    narrative: str | None = None,
    narrative_status: str = "none",
) -> dict:
    """발행 시점 보고서 스냅샷 + narrative 상태를 result JSONB dict 로 묶음."""
    return {
        "kind": kind,
        "snapshot": snapshot,
        "view": view,
        "narrative": narrative,
        "narrative_status": narrative_status,
    }


def load_report_snapshot(result: dict) -> ReportSummary | EnvironmentReportSummary:
    """result['snapshot'] 을 kind 별 ViewModel 로 복원 (정적 렌더용)."""
    snapshot = result["snapshot"]
    if result["kind"] == REPORT_KIND_SUMMARY:
        return report_summary_from_dict(snapshot)
    return env_report_from_dict(snapshot)


# ──────────────────────────────────────────────────────────────────────────
# ReportSummary (server scope N대 보고서 — servers/report.html)
# ──────────────────────────────────────────────────────────────────────────
def report_summary_to_dict(vm: ReportSummary) -> dict:
    return _to_jsonable(vm)


def report_summary_from_dict(d: dict) -> ReportSummary:
    data = dict(d)
    data["rows"] = [ReportRowItem(**r) for r in data.get("rows") or []]
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
    data["top_risks"] = [ReportRowItem(**r) for r in data.get("top_risks") or []]
    uph = data.get("under_provisioned_hosts") or []
    data["under_provisioned_hosts"] = [_capacity_warning_from_dict(c) for c in uph]
    data["attention_hosts"] = [AttentionHostItem(**a) for a in data.get("attention_hosts") or []]
    data["capacity_imminent"] = [CapacityImminentItem(**c) for c in data.get("capacity_imminent") or []]
    data["insufficient_hosts"] = [InsufficientHostItem(**i) for i in data.get("insufficient_hosts") or []]
    data["anchor_at"] = _dt(data.get("anchor_at"))
    data["generated_at"] = _dt(data.get("generated_at"))
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
