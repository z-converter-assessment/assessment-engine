from fastapi import APIRouter, Depends, HTTPException, Query, Request

from web.deps import get_service
from web.services.query_service import QueryService
from web.template_setup import templates

pages_router = APIRouter(prefix="/servers", tags=["pages"])


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


async def _resolve(public_id: str, service: QueryService) -> int:
    server_id = await service.resolve_server_id(public_id)
    if server_id is None:
        raise HTTPException(status_code=404)
    return server_id


@pages_router.get("/{server_id}")
async def get_server(
    server_id: str,
    request: Request,
    service: QueryService = Depends(get_service),
):
    internal_id = await _resolve(server_id, service)
    result = await service.get_server(internal_id)
    if not result:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="servers/detail.html",
        context={"server": result},
    )


@pages_router.get("/{server_id}/storage")
async def get_storage(
    server_id: str,
    request: Request,
    service: QueryService = Depends(get_service),
):
    internal_id = await _resolve(server_id, service)
    result = await service.get_storage(internal_id)
    if not result:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="servers/storage.html",
        context={"storage": result},
    )


@pages_router.get("/{server_id}/cpu")
async def get_cpu(
    server_id: str,
    request: Request,
    service: QueryService = Depends(get_service),
):
    internal_id = await _resolve(server_id, service)
    result = await service.get_server(internal_id)
    if not result:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="servers/cpu.html",
        context={"server": result},
    )


@pages_router.get("/{server_id}/memory")
async def get_memory(
    server_id: str,
    request: Request,
    service: QueryService = Depends(get_service),
):
    internal_id = await _resolve(server_id, service)
    result = await service.get_server(internal_id)
    if not result:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="servers/memory.html",
        context={"server": result},
    )


@pages_router.get("/{server_id}/network")
async def get_network(
    server_id: str,
    request: Request,
    service: QueryService = Depends(get_service),
):
    internal_id = await _resolve(server_id, service)
    result = await service.get_network(internal_id)
    if not result:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="servers/network.html",
        context={"network": result},
    )


@pages_router.get("/{server_id}/services")
async def get_services(
    server_id: str,
    request: Request,
    service: QueryService = Depends(get_service),
):
    internal_id = await _resolve(server_id, service)
    result = await service.get_server(internal_id)
    if not result:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="servers/services.html",
        context={"server": result},
    )


@pages_router.get("/{server_id}/performance")
async def get_performance(
    server_id: str,
    request: Request,
    service: QueryService = Depends(get_service),
):
    internal_id = await _resolve(server_id, service)
    server = await service.get_server(internal_id)
    if not server:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="servers/performance.html",
        context={"server": server},
    )