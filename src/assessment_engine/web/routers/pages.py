"""SSR 페이지 라우터.

의존성 주입 정석: `internal_id`는 `resolve_internal_id` Depends 체인으로 받음 — 각 핸들러가
public_id resolve + 404 분기를 직접 다루지 않는다.

5개 detail 탭(detail/cpu/memory/services/performance)이 동일 흐름이라 `_render_server_tab` helper로
중복 제거. 다른 service 메서드를 쓰는 storage/network는 별도 핸들러.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from assessment_engine.web.deps import get_service, resolve_internal_id
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
):
    servers = await service.list_servers(page, limit, search, is_online)
    return templates.TemplateResponse(
        request=request,
        name="servers/list.html",
        context={"servers": servers},
    )


# ─── server 컨텍스트로 렌더링하는 5개 탭 ───────────────────────────────────

@pages_router.get("/{server_id}")
async def get_server(
    request: Request,
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    return await _render_server_tab(request, "servers/detail.html",
                                    internal_id=internal_id, service=service)


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