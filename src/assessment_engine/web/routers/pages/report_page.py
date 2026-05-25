"""서버 보고서 SSR — 선택 N대 (`/servers/report`) + 단일 1대 (`/servers/{id}/report`).

server scope 보고서. 환경 단위 high-level 보고서는 `/reports/environment` 별도 endpoint (T13).
"""

from collections import Counter
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError

from assessment_engine.db.repositories.base_diagnostic_repository import (
    DIAGNOSTIC_RANGE_DAYS,
    DIAGNOSTIC_RANGE_LABEL_KR,
    DiagnosticTimeRange,
)
from assessment_engine.web.deps import get_diagnostic_service, get_service
from assessment_engine.web.services.diagnostic_service import DiagnosticService
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.templating import templates

report_page_router = APIRouter()

_REPORT_VIEW_TITLES: dict[str, str] = {
    "customer": "고객 제출용 (양식 A)",
    "engineer": "엔지니어 검토용 (양식 B)",
}


def _period_days_to_diagnostic_range(period_days: int) -> str:
    """보고서 period_days → AI 진단 time_range 매핑. AI 진단 시간 윈도우 = 보고서와 동기.

    DIAGNOSTIC_RANGE_DAYS valid: 15m / 1h / 6h / 24h / 7d / 14d / 30d.
    """
    if period_days >= 30:
        return "30d"
    if period_days >= 14:
        return "14d"
    if period_days >= 7:
        return "7d"
    return "24h"


@report_page_router.get("/report")
async def report(
    request: Request,
    ids: str = Query(..., description="comma-separated public_id 목록 (선택 N대)"),
    time_range: DiagnosticTimeRange = Query(
        "14d", description="윈도우 — AI 진단 / 환경 보고서와 동일 7개 (15m/1h/6h/24h/7d/14d/30d)"
    ),
    view: Literal["customer", "engineer"] = Query("customer", description="고객용(A) / 엔지니어용(B) 분기"),
    back: str | None = Query(None, description="← 이전 link 의 referrer (서버 상세 등). 미명시 시 /servers/"),
    service: QueryService = Depends(get_service),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """Server scope 보고서 표시 — GET 은 read-only (P3 분리). 발행 record 는 POST /servers/report/emit 책임.

    환경 단위 high-level 보고서는 별도 endpoint (`/reports/environment`) — 양식·데이터 다름 (T13).
    데이터 소스: USE Method 통계 (CPU/MEM p95·peak + swap + load_15m max). 분류는 service 측.
    진단 컬럼: 14일 latest succeeded 진단 N개 batch fetch (#C5 N+1 회피).
    페이지 진입 자체로는 이력 row 생성 안 함 — 이력 "다시 보기" / 북마크 / 직접 URL read-only.
    """
    public_ids = [pid.strip() for pid in ids.split(",") if pid.strip()]
    sid_map = await service.resolve_server_ids(public_ids)
    server_ids = [sid_map[pid] for pid in public_ids if pid in sid_map]
    if not server_ids:
        raise HTTPException(status_code=404, detail="no valid server ids")

    period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
    summary = await service.get_report(server_ids, period_days, view=view)
    # AI 진단 = 보고서 흐름 안 inline 통합 (engineer view 만). 보고서 진입 시 anchor + time_range 기반
    # latest succeeded fetch — 없으면 자동 submit + pending job_ids context 전달 (template 안 polling JS).
    diagnostics_by_pid: dict = {}
    pending_job_ids: dict = {}
    if view == "engineer":
        diag_map = await diag_service.ensure_latest_or_submit("server", public_ids, time_range)
        diagnostics_by_pid = {pid: entry.get("panel") for pid, entry in diag_map.items()}
        pending_job_ids = {pid: entry.get("job_id") for pid, entry in diag_map.items() if entry.get("job_id")}
    # 서버별 detail section — 운영 신호 발화 status (hostname 기준 lookup dict).
    # attention 은 환경 전체 합성이지만 본 보고서는 선택 N대 한정이라 선택 server hostname 만 lookup.
    attention = await service.get_attention_signals()
    selected_hostnames = {r.hostname for r in summary.rows}
    attention_by_host: dict[str, dict[str, str]] = {h: {} for h in selected_hostnames}
    for row in attention.gap_warnings:
        if row.link_text in attention_by_host:
            attention_by_host[row.link_text]["gap"] = row.badge_text
    for row in attention.os_eol_warnings:
        if row.link_text in attention_by_host:
            attention_by_host[row.link_text]["os_eol"] = row.meta_text
    for row in attention.agent_unstable:
        if row.link_text in attention_by_host:
            attention_by_host[row.link_text]["restart"] = row.badge_text

    # KPI grid 합성 — Right-sizing 분류 카운트 + 운영 신호 발화 호스트 수 (사용자 의도: 위험도 명칭 제거).
    rec_counts = Counter(r.recommendation for r in summary.rows)
    under_count = rec_counts.get("under_provisioned", 0)
    optimize_count = rec_counts.get("over_provisioned", 0) + rec_counts.get("idle", 0) + rec_counts.get("shutdown", 0)
    optimal_count = rec_counts.get("optimal", 0)
    attn_active_count = sum(1 for a in attention_by_host.values() if a)
    attn_gap_count = sum(1 for a in attention_by_host.values() if a.get("gap"))
    attn_eol_count = sum(1 for a in attention_by_host.values() if a.get("os_eol"))
    attn_restart_count = sum(1 for a in attention_by_host.values() if a.get("restart"))
    attn_active_kpi_cols = sum(1 for c in (attn_gap_count, attn_eol_count, attn_restart_count) if c > 0) or 1

    # 발행 record 는 POST /servers/report/emit 책임 (PRG 분리) — GET 는 read-only.

    # back url validation — same-origin path (시작 '/' 강제, '//' 제외 — open redirect 방어)
    back_url = back if back and back.startswith("/") and not back.startswith("//") else "/servers/"
    # 현재 URL (path + query) 을 single server detail link 의 back query 로 전달 — 뒤로가기 보장.
    self_back = quote(f"{request.url.path}?{request.url.query}", safe="")
    return templates.TemplateResponse(
        request=request,
        name="servers/report.html",
        context={
            "summary": summary,
            "view": view,
            "view_title": _REPORT_VIEW_TITLES[view],
            "diagnostics_by_pid": diagnostics_by_pid,
            "pending_job_ids": pending_job_ids,
            "back_url": back_url,
            "attention_by_host": attention_by_host,
            "under_count": under_count,
            "optimize_count": optimize_count,
            "optimal_count": optimal_count,
            "attn_active_count": attn_active_count,
            "attn_gap_count": attn_gap_count,
            "attn_eol_count": attn_eol_count,
            "attn_restart_count": attn_restart_count,
            "attn_active_kpi_cols": attn_active_kpi_cols,
            "self_back": self_back,
            "time_range": time_range,
            "time_range_label": DIAGNOSTIC_RANGE_LABEL_KR.get(time_range, time_range),
        },
    )


@report_page_router.post("/report/emit")
async def report_emit(
    ids: str = Query(..., description="comma-separated public_id 목록 (선택 N대)"),
    time_range: DiagnosticTimeRange = Query("14d"),
    view: Literal["customer", "engineer"] = Query("customer"),
    service: QueryService = Depends(get_service),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """Server scope 보고서 발행 record 전용 endpoint (PRG pattern — POST 발행, GET 표시 분리).

    응답 = {view_url} — 클라이언트가 navigate. 다시 보기 / 북마크 / 직접 URL 은 GET 만 호출 → record 안 됨 → 중복 방지.
    """
    public_ids = [pid.strip() for pid in ids.split(",") if pid.strip()]
    sid_map = await service.resolve_server_ids(public_ids)
    valid_pids = [pid for pid in public_ids if pid in sid_map]
    if not valid_pids:
        raise HTTPException(status_code=404, detail="no valid server ids")
    period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
    for pid in valid_pids:
        try:
            await diag_service.record_report_emission(
                view=view,
                scope="server",
                server_public_ids=[pid],
                period_days=period_days,
                time_range=time_range,
            )
        except SQLAlchemyError:
            # PRG view_url 반환 흐름 안 server scope record best-effort fallback (정공 정합 reports.py:107).
            logger.exception("report emission record failed (best-effort) pid={}", pid)
    ids_query = ",".join(valid_pids)
    return {"view_url": f"/servers/report?ids={ids_query}&view={view}&time_range={time_range}"}


@report_page_router.get("/{server_id}/report")
async def single_server_report(
    request: Request,
    server_id: UUID,
    time_range: DiagnosticTimeRange = Query("14d", description="윈도우 — 모달과 동일 7개"),
    view: Literal["customer", "engineer"] = Query("customer"),
    back: str | None = Query(None),
    service: QueryService = Depends(get_service),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """단일 서버 보고서 — 단순화 양식 (1대 컨텍스트, T13).

    선택 N대 보고서 (`/servers/report?ids=...`) 의 hostname link 또는 보고서 이력의 1대 row link 진입.
    환경 양식과 별도 — 1대 한정이라 분류 분포·OS 분포 등 의미 없는 카드 제거, Right-sizing 평가 + AI 진단 중심.
    """
    period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
    summary = await service.get_single_server_report(
        str(server_id),
        period_days=period_days,
        view=view,
        time_range=time_range,
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="server not found")
    # AI 진단 = 보고서 흐름 안 inline 통합 (engineer view 만). 보고서 진입 시 anchor + time_range 기반
    # latest succeeded fetch — 없으면 자동 submit + pending job_id context 전달.
    diagnostics_by_pid: dict = {}
    pending_job_ids: dict = {}
    if view == "engineer":
        diag_map = await diag_service.ensure_latest_or_submit("server", [str(server_id)], time_range)
        diagnostics_by_pid = {pid: entry.get("panel") for pid, entry in diag_map.items()}
        pending_job_ids = {pid: entry.get("job_id") for pid, entry in diag_map.items() if entry.get("job_id")}
    hostname = summary.base.rows[0].hostname if summary.base.rows else str(server_id)
    # 운영 신호 (gap / os_eol / restart) — 해당 hostname 1대 lookup.
    attention = await service.get_attention_signals()
    attention_for_host: dict[str, str] = {}
    for row in attention.gap_warnings:
        if row.link_text == hostname:
            attention_for_host["gap"] = row.badge_text
    for row in attention.os_eol_warnings:
        if row.link_text == hostname:
            attention_for_host["os_eol"] = row.meta_text
    for row in attention.agent_unstable:
        if row.link_text == hostname:
            attention_for_host["restart"] = row.badge_text
    back_url = back if back and back.startswith("/") and not back.startswith("//") else f"/servers/{server_id}"
    # 자식 link (참고자료 등) 의 back query 보존용 — 현재 URL encoding.
    self_back = quote(f"{request.url.path}?{request.url.query}", safe="")
    return templates.TemplateResponse(
        request=request,
        name="servers/single_report.html",
        context={
            "summary": summary,
            "view": view,
            "view_title": _REPORT_VIEW_TITLES[view],
            "back_url": back_url,
            "hostname": hostname,
            "diagnostics_by_pid": diagnostics_by_pid,
            "pending_job_ids": pending_job_ids,
            "attention_for_host": attention_for_host,
            "self_back": self_back,
            "time_range": time_range,
        },
    )
