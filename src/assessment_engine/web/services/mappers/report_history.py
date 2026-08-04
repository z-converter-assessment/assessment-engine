"""보고서 이력 페이지 mapper — DiagnosticJobRecord → 표시용 dict (P2 단일 변환)."""

from typing import Any

from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
from assessment_engine.json_types import json_list, json_obj
from assessment_engine.web.services.mappers.shared import DIAGNOSTIC_RANGE_LABEL_KR

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
    """environment scope 등록 서버 수 — 발행 스냅샷 base.total.

    environment 보고서는 input_params.server_public_ids 가 비어 있다(전체 대상이라 선택 list 부재)
    — len() 으로는 항상 0 이라 "환경 전체 (0대)" 오해를 부른다. 발행 시점 스냅샷의
    등록 서버 총수(base.total)를 대신 표시. 스냅샷 부재(pending/running/failed)면 None —
    표시 단계에서 수량을 생략한다("환경 전체").
    """
    snapshot = json_obj(rec.result or {}, "snapshot")
    base = json_obj(snapshot, "base")
    total = base.get("total")
    return total if isinstance(total, int) else None


def _result_link(rec: DiagnosticJobRecord, back: str = "") -> str:
    """재조회 link — 발행된 정적 스냅샷 `?job={id}` 로. scope/서버 수에 따라 라우터만 분기.

    스냅샷이 발행 시점 데이터를 그대로 보관하므로 윈도우·anchor 재구성 불필요 (요구: 이력 동적변화 0).
    back = 진입 페이지 (보고서 이력) URL — 진입한 보고서 페이지의 "이전" 버튼이 이 값으로 되돌아감.
    """
    back_suffix = f"&back={back}" if back else ""
    if rec.scope == "environment":
        return f"/reports/environment?job={rec.id}{back_suffix}"
    server_public_ids = json_list(rec.input_params, "server_public_ids")
    # server scope 1대는 단일 양식(`/servers/{pid}/report`), 2대+ 는 N대 표(`/reports/servers`).
    if len(server_public_ids) == 1:
        return f"/servers/{server_public_ids[0]}/report?job={rec.id}{back_suffix}"
    return f"/reports/servers?job={rec.id}{back_suffix}"


def to_report_history_item(rec: DiagnosticJobRecord, back: str = "") -> dict[str, Any]:
    """보고서 이력 행 1개 — 발행 시각·양식·서버 수·윈도우·재조회 link."""
    server_public_ids = json_list(rec.input_params, "server_public_ids")
    period_days = float(rec.input_params.get("period_days", 14))
    view = _view_from_job_type(rec.job_type)
    # environment scope 는 선택 list 부재라 등록 서버 총수(스냅샷 base.total)로 산출,
    # server scope 는 선택 개수. (#E9 "환경 전체 (N대)" 모호성 제거)
    if rec.scope == "environment":
        server_count = _environment_server_count(rec)
    else:
        server_count = len(server_public_ids)
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
