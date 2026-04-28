from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates

from web.deps import get_service
from web.services.query_service import QueryService
from web.template_filters import disksize, kbps, kst

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.filters["kst"]      = kst
templates.env.filters["disksize"] = disksize
templates.env.filters["kbps"]     = kbps

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


@pages_router.get("/{server_id}")
async def get_server(
    server_id: int,
    request: Request,
    service: QueryService = Depends(get_service),
):
    result = await service.get_server(server_id)
    if not result:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="servers/detail.html",
        context={"server": result},
    )


@pages_router.get("/{server_id}/storage")
async def get_storage(
    server_id: int,
    request: Request,
    service: QueryService = Depends(get_service),
):
    result = await service.get_storage(server_id)
    if not result:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="servers/storage.html",
        context={"storage": result},
    )


@pages_router.get("/{server_id}/chart")
async def get_chart(
    server_id: int,
    request: Request,
    service: QueryService = Depends(get_service),
):
    result = await service.get_server(server_id)
    if not result:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="servers/chart.html",
        context={"server": result},
    )


@pages_router.get("/{server_id}/network")
async def get_network(
    server_id: int,
    request: Request,
    service: QueryService = Depends(get_service),
):
    result = await service.get_network(server_id)
    if not result:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="servers/network.html",
        context={"network": result},
    )