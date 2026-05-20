"""SSR 페이지 라우터.

의존성 주입 정석: `internal_id`는 `resolve_internal_id` Depends 체인으로 받음 — 각 핸들러가
public_id resolve + 404 분기를 직접 다루지 않는다.

5개 detail 탭(detail/cpu/memory/services/performance)이 동일 흐름이라 `_render_server_tab` helper로
중복 제거. 다른 service 메서드를 쓰는 storage/network는 별도 핸들러.
"""
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger

from assessment_engine.db.repositories.base_diagnostic_repository import (
    DIAGNOSTIC_RANGE_DAYS,
    DIAGNOSTIC_RANGE_LABEL_KR,
    DiagnosticTimeRange,
)
from assessment_engine.web.deps import get_diagnostic_service, get_service, resolve_internal_id
from assessment_engine.web.services.diagnostic_service import DiagnosticService, to_panel_payload
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.settings import diagnostic_settings, web_settings
from assessment_engine.web.template_setup import templates

pages_router = APIRouter(prefix="/servers", tags=["pages"])


async def _render_server_tab(
    request: Request,
    template_name: str,
    *,
    internal_id: int,
    service: QueryService,
):
    """server 컨텍스트로 detail 탭 템플릿 렌더링. detail/cpu/memory/services/performance 공유."""
    server = await service.get_server(internal_id)
    if not server:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={"server": server},
    )


@pages_router.get("/")
async def list_servers(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    is_online: bool | None = Query(None),
    service: QueryService = Depends(get_service),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    servers = await service.list_servers(page, limit, search, is_online)
    # 첫 페이지 + 검색·필터 미사용일 때만 환경 요약·신호 + 환경 진단(latest) 노출.
    # 진단 기능 비활성(DIAGNOSTIC_ENABLED=false) 시 latest fetch 자체 skip — historical row 표시 차단.
    overview = None
    attention = None
    last_env_diagnostic = None
    if page == 1 and not search and is_online is None:
        overview = await service.get_environment_overview()
        attention = await service.get_attention_signals()
        if diagnostic_settings.diagnostic_enabled:
            last_env_diagnostic = await diag_service.get_latest("environment", None, "14d")
    return templates.TemplateResponse(
        request=request,
        name="servers/list.html",
        context={
            "servers": servers,
            "overview": overview,
            "attention": attention,
            "last_env_diagnostic": to_panel_payload(last_env_diagnostic),
            "zdm_defaults": {
                "ip": web_settings.zdm_default_ip,
                "user": web_settings.zdm_default_user,
            },
        },
    )


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


@pages_router.get("/report")
async def report(
    request: Request,
    ids: str = Query(..., description="comma-separated public_id 목록 (선택 N대)"),
    time_range: DiagnosticTimeRange = Query("14d", description="윈도우 — AI 진단 / 환경 보고서와 동일 7개 (15m/1h/6h/24h/7d/14d/30d)"),
    view: Literal["customer", "engineer"] = Query("customer", description="고객용(A) / 엔지니어용(B) 분기"),
    back: str | None = Query(None, description="← 이전 link 의 referrer (서버 상세 등). 미명시 시 /servers/"),
    service: QueryService = Depends(get_service),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """Server scope 보고서 — 선택 N대 호스트의 row 단위 상세 (양식 A/B).

    환경 단위 high-level 보고서는 별도 endpoint (`/reports/environment`) — 양식·데이터 다름 (T13).
    데이터 소스: USE Method 통계 (CPU/MEM p95·peak + swap + load_15m max). 분류는 service 측.
    진단 컬럼: 14일 latest succeeded 진단 N개 batch fetch (#C5 N+1 회피).
    """
    public_ids = [pid.strip() for pid in ids.split(",") if pid.strip()]
    sid_map = await service.resolve_server_ids(public_ids)
    server_ids = [sid_map[pid] for pid in public_ids if pid in sid_map]
    if not server_ids:
        raise HTTPException(status_code=404, detail="no valid server ids")

    period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
    summary = await service.get_report(server_ids, period_days, view=view)
    # 진단 비활성 시 보고서의 AI 진단 컬럼도 historical row 표시 차단.
    diagnostics_by_pid: dict = {}
    if diagnostic_settings.diagnostic_enabled:
        diagnostics_by_pid = await diag_service.get_many_latest_server(public_ids, time_range)
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
    from collections import Counter as _Counter  # noqa: PLC0415
    rec_counts = _Counter(r.recommendation for r in summary.rows)
    under_count = rec_counts.get("under_provisioned", 0)
    optimize_count = (
        rec_counts.get("over_provisioned", 0)
        + rec_counts.get("idle", 0)
        + rec_counts.get("shutdown", 0)
    )
    optimal_count = rec_counts.get("optimal", 0)
    attn_active_count = sum(1 for a in attention_by_host.values() if a)
    attn_gap_count = sum(1 for a in attention_by_host.values() if a.get("gap"))
    attn_eol_count = sum(1 for a in attention_by_host.values() if a.get("os_eol"))
    attn_restart_count = sum(1 for a in attention_by_host.values() if a.get("restart"))
    attn_active_kpi_cols = sum(
        1 for c in (attn_gap_count, attn_eol_count, attn_restart_count) if c > 0
    ) or 1

    # 발행 이력 row INSERT (best-effort) — 서버별 N row 분리 (1대 1 row).
    # N대 발행이면 보고서 이력에 N row 추가 — 서버 단위 추적·필터 정합.
    # 같은 input 활성 충돌이나 DB 오류로 실패해도 응답 흐름 영향 없음.
    for pid in public_ids:
        try:
            await diag_service.record_report_emission(
                view=view,
                scope="server",
                server_public_ids=[pid],
                period_days=period_days,
                time_range=time_range,
            )
        except Exception:
            logger.exception("report emission record failed (best-effort) pid={}", pid)

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


# ─── server 컨텍스트로 렌더링하는 5개 탭 ───────────────────────────────────

@pages_router.get("/{server_id}")
async def get_server(
    request: Request,
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    server = await service.get_server(internal_id)
    if not server:
        raise HTTPException(status_code=404)
    # 진단 기능 비활성 시 latest fetch skip — historical row 표시 차단 (대시보드와 정합).
    last_server_diagnostic = None
    if diagnostic_settings.diagnostic_enabled:
        last_server_diagnostic = await diag_service.get_latest("server", str(server.public_id), "14d")
    recent_tasks = await service.list_recent_tasks(str(server.public_id), limit=10, cursor=None)
    return templates.TemplateResponse(
        request=request,
        name="servers/detail.html",
        context={
            "server": server,
            "last_server_diagnostic": to_panel_payload(last_server_diagnostic),
            "recent_tasks": recent_tasks,
            "zdm_defaults": {
                "ip": web_settings.zdm_default_ip,
                "user": web_settings.zdm_default_user,
            },
        },
    )


@pages_router.get("/{server_id}/report")
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
        str(server_id), period_days=period_days, view=view, time_range=time_range,
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="server not found")
    # 진단 비활성 시 AI 진단 카드 historical row 표시 차단 (대시보드·다중 보고서와 정합).
    diagnostics_by_pid: dict = {}
    if diagnostic_settings.diagnostic_enabled:
        diagnostics_by_pid = await diag_service.get_many_latest_server([str(server_id)], time_range)
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
            "attention_for_host": attention_for_host,
            "self_back": self_back,
        },
    )


@pages_router.get("/{server_id}/cpu")
async def get_cpu(
    request: Request,
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    return await _render_server_tab(request, "servers/cpu.html",
                                    internal_id=internal_id, service=service)


@pages_router.get("/{server_id}/memory")
async def get_memory(
    request: Request,
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    return await _render_server_tab(request, "servers/memory.html",
                                    internal_id=internal_id, service=service)


@pages_router.get("/{server_id}/services")
async def get_services(
    request: Request,
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    return await _render_server_tab(request, "servers/services.html",
                                    internal_id=internal_id, service=service)


@pages_router.get("/{server_id}/performance")
async def get_performance(
    request: Request,
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    return await _render_server_tab(request, "servers/performance.html",
                                    internal_id=internal_id, service=service)


# ─── 별도 service 메서드를 쓰는 탭 ───────────────────────────────────────

@pages_router.get("/{server_id}/storage")
async def get_storage(
    request: Request,
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    result = await service.get_storage(internal_id)
    if not result:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="servers/storage.html",
        context={"storage": result},
    )


@pages_router.get("/{server_id}/network")
async def get_network(
    request: Request,
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    result = await service.get_network(internal_id)
    if not result:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="servers/network.html",
        context={"network": result},
    )