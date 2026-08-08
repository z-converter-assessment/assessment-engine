"""보고서 이력 페이지 mapper — DiagnosticJobRecord → 표시용 dict (P2 단일 변환)."""

from typing import TYPE_CHECKING, Any

from assessment_engine.json_types import json_list, json_obj
from assessment_engine.web.services.mappers.constants import DIAGNOSTIC_RANGE_LABEL_KR

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import DiagnosticJobRecord

_VIEW_LABEL: dict[str, str] = {
    "customer": "고객 보고서",
    "engineer": "엔지니어 보고서",
}


def _view_from_job_type(job_type: str) -> str:
    if job_type == "engineer_report":
        return "engineer"
    return "customer"


def _resolve_time_range(rec: DiagnosticJobRecord) -> str | None:
    """발행 윈도우 식별자 복원 — input_params.time_range 우선, fallback result.time_range."""
    tr = rec.input_params.get("time_range")
    if tr:
        return str(tr)
    result = rec.result or {}
    tr = result.get("time_range")
    return str(tr) if tr else None


def _window_label(rec: DiagnosticJobRecord, period_days: float) -> str:
    """윈도우 표시 라벨 — time_range 식별자 우선 (1일 미만 윈도우 보존), fallback period_days."""
    time_range = _resolve_time_range(rec)
    if time_range:
        return DIAGNOSTIC_RANGE_LABEL_KR.get(time_range, time_range)
    if period_days >= 1:
        return f"{int(period_days)}일"
    return f"{period_days}일"


def _environment_server_count(rec: DiagnosticJobRecord) -> int | None:
    snapshot = json_obj(rec.result or {}, "snapshot")
    base = json_obj(snapshot, "base")
    total = base.get("total")
    return total if isinstance(total, int) else None


def _result_link(rec: DiagnosticJobRecord, back: str = "") -> str:
    back_suffix = f"&back={back}" if back else ""
    if rec.scope == "environment":
        return f"/reports/environment?job={rec.id}{back_suffix}"
    server_public_ids = json_list(rec.input_params, "server_public_ids")

    if len(server_public_ids) == 1:
        return f"/servers/{server_public_ids[0]}/report?job={rec.id}{back_suffix}"
    return f"/reports/servers?job={rec.id}{back_suffix}"


def to_report_history_item(rec: DiagnosticJobRecord, back: str = "") -> dict[str, Any]:
    server_public_ids = json_list(rec.input_params, "server_public_ids")
    period_days = float(rec.input_params.get("period_days", 14))
    view = _view_from_job_type(rec.job_type)

    server_count = _environment_server_count(rec) if rec.scope == "environment" else len(server_public_ids)
    return {
        "job_id": rec.id,
        "scope": rec.scope,
        "view": view,
        "view_label": _VIEW_LABEL.get(view, view),
        "server_count": server_count,
        "window_label": _window_label(rec, period_days),
        "created_at": rec.created_at,
        "result_link": _result_link(rec, back),
    }
