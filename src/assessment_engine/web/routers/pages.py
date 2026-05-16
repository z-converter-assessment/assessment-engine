"""SSR 페이지 라우터.

의존성 주입 정석: `internal_id`는 `resolve_internal_id` Depends 체인으로 받음 — 각 핸들러가
public_id resolve + 404 분기를 직접 다루지 않는다.

5개 detail 탭(detail/cpu/memory/services/performance)이 동일 흐름이라 `_render_server_tab` helper로
중복 제거. 다른 service 메서드를 쓰는 storage/network는 별도 핸들러.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from assessment_engine.web.deps import get_diagnostic_service, get_service, resolve_internal_id
from assessment_engine.web.services.diagnostic_service import DiagnosticService, to_panel_payload
from assessment_engine.web.services.query_service import QueryService
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
    # 첫 페이지 + 검색·필터 미사용일 때만 환경 요약·신호 + 환경 진단(latest) 노출
    overview = None
    attention = None
    last_env_diagnostic = None
    if page == 1 and not search and is_online is None:
        overview = await service.get_environment_overview()
        attention = await service.get_attention_signals()
        last_env_diagnostic = await diag_service.get_latest("environment", None, "14d")
    return templates.TemplateResponse(
        request=request,
        name="servers/list.html",
        context={
            "servers": servers,
            "overview": overview,
            "attention": attention,
            "last_env_diagnostic": to_panel_payload(last_env_diagnostic),
        },
    )


_REPORT_VIEW_TITLES: dict[str, str] = {
    "customer": "고객 제출용 (양식 A)",
    "engineer": "엔지니어 검토용 (양식 B)",
}


@pages_router.get("/report")
async def report(
    request: Request,
    ids: str = Query(..., description="comma-separated public_id 목록"),
    period_days: int = Query(14, ge=1, le=90),
    view: Literal["customer", "engineer"] = Query("customer", description="고객용(A) / 엔지니어용(B) 분기"),
    service: QueryService = Depends(get_service),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """Assessment 보고서 — view 파라미터로 양식 A/B 분기 (한 endpoint, 같은 SQL).

    ids는 query string으로 받음 (선택 N대 -> 새 탭). 큰 N이면 URL 길이 한계 — 추후 POST·session.
    데이터 소스: USE Method 통계 (CPU/MEM p95·peak + swap + load_15m max). 분류는 service 측.
    진단 컬럼: 14일 latest succeeded 진단 N개 batch fetch (#C5 N+1 회피).
    """
    public_ids = [pid.strip() for pid in ids.split(",") if pid.strip()]
    sid_map = await service.resolve_server_ids(public_ids)
    server_ids = [sid_map[pid] for pid in public_ids if pid in sid_map]
    if not server_ids:
        raise HTTPException(status_code=404, detail="no valid server ids")

    summary = await service.get_report(server_ids, period_days, view=view)
    diagnostics_by_pid = await diag_service.get_many_latest_server(public_ids, "14d")
    return templates.TemplateResponse(
        request=request,
        name="servers/report.html",
        context={
            "summary": summary,
            "view": view,
            "view_title": _REPORT_VIEW_TITLES[view],
            "diagnostics_by_pid": diagnostics_by_pid,
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
    last_server_diagnostic = await diag_service.get_latest("server", str(server.public_id), "14d")
    recent_tasks = await service.list_recent_tasks(str(server.public_id), limit=10, cursor=None)
    return templates.TemplateResponse(
        request=request,
        name="servers/detail.html",
        context={
            "server": server,
            "last_server_diagnostic": to_panel_payload(last_server_diagnostic),
            "recent_tasks": recent_tasks,
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