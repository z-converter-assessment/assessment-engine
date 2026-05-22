"""보고서 이력 페이지 mapper — DiagnosticJobRecord → 표시용 dict (P2 단일 변환).

AI 진단 이력과 분리 (T13) — 보고서 양식·서버 수·재조회 link 만 표시.
"""

from typing import Any
from urllib.parse import quote

from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
from assessment_engine.db.repositories.base_diagnostic_repository import (
    DIAGNOSTIC_RANGE_LABEL_KR,
)

_VIEW_LABEL: dict[str, str] = {
    "customer": "고객 보고서",
    "engineer": "엔지니어 보고서",
}


def _view_from_job_type(job_type: str) -> str:
    """job_type ('customer_report'|'engineer_report') → view ('customer'|'engineer')."""
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


def _result_link(rec: DiagnosticJobRecord, view: str, back: str = "") -> str:
    """재조회 link — scope 에 따라 server/environment 라우터 분기. 윈도우는 time_range 단일 진실.

    back = 진입 페이지 (보고서 이력) URL — 진입한 보고서 페이지의 "이전" 버튼이 이 값으로 되돌아감.
    """
    time_range = _resolve_time_range(rec) or "14d"
    tr_q = quote(time_range, safe="")
    back_suffix = f"&back={back}" if back else ""
    if rec.scope == "environment":
        result = rec.result or {}
        params = [f"view={view}", f"time_range={tr_q}"]
        anchor_at = result.get("anchor_at")
        if anchor_at:
            # anchor_at 의 '+' (UTC offset 부호) 는 URL 에서 space 로 해석되어 422 발생 — '%2B' 로 인코딩.
            params.append(f"anchor_at={quote(str(anchor_at), safe='')}")
        return f"/reports/environment?{'&'.join(params)}{back_suffix}"
    server_public_ids = rec.input_params.get("server_public_ids") or []
    # server scope 1대 보고서는 단일 server detail URL 로 — 환경 양식의 단일 서버 적용 페이지.
    if len(server_public_ids) == 1:
        return f"/servers/{server_public_ids[0]}/report?view={view}&time_range={tr_q}{back_suffix}"
    ids_query = ",".join(server_public_ids)
    return f"/servers/report?ids={ids_query}&view={view}&time_range={tr_q}{back_suffix}"


def to_report_history_item(rec: DiagnosticJobRecord, back: str = "") -> dict[str, Any]:
    """보고서 이력 행 1개 — 발행 시각·양식·서버 수·윈도우·재조회 link.

    재조회 link 는 scope 별 라우터 분기. server scope 는 /servers/report?ids=...&time_range=...,
    environment scope 는 /reports/environment?time_range=...&anchor_at=... (result 저장된 입력 재사용).
    back = 본 이력 페이지 URL — 진입한 보고서의 "이전" 버튼 chain.
    """
    server_public_ids = rec.input_params.get("server_public_ids") or []
    period_days = float(rec.input_params.get("period_days", 14))
    view = _view_from_job_type(rec.job_type)
    return {
        "job_id": rec.id,
        "scope": rec.scope,
        "view": view,
        "view_label": _VIEW_LABEL.get(view, view),
        "server_count": len(server_public_ids),
        "window_label": _window_label(rec, period_days),
        "created_at": rec.created_at,
        "result_link": _result_link(rec, view, back),
    }
