import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates

from app.api.deps import get_service
from app.services.query_service import QueryService

router = APIRouter(prefix="/servers", tags=["query"])

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/")
async def list_servers(request: Request, service: QueryService = Depends(get_service)):
    servers = await service.list_servers()
    return templates.TemplateResponse(request=request, name="servers/list.html", context={"servers": servers})


@router.get("/{server_id}")
async def get_server(server_id: uuid.UUID, request: Request, service: QueryService = Depends(get_service)):
    server = await service.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return templates.TemplateResponse(request=request, name="servers/detail.html", context={"server": server})


@router.get("/{server_id}/history")
async def get_history(server_id: uuid.UUID, request: Request, service: QueryService = Depends(get_service)):
    server, history = await service.get_history(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return templates.TemplateResponse(request=request, name="servers/history.html", context={"server": server, "history": history})