"""서버 상세 SSR — `/servers/{id}` + 탭(cpu·memory·services·metrics·storage·network).

모든 endpoint 가 `back` Query 를 받고 `back_url` 을 context 로 넘긴다 — back chain 규약은
docs/reference/web/static-assets.md "네비게이션 규약" 절.
"""

from fastapi import APIRouter, HTTPException, Request

from assessment_engine.domain.service_classifier import SERVICE_CATEGORIES
from assessment_engine.web.deps import QueryServiceDep, ServerIdDep
from assessment_engine.web.routers._back import BackUrl, safe_back, self_back
from assessment_engine.web.services.query import QueryService
from assessment_engine.web.settings import get_web_settings
from assessment_engine.web.templating import templates

server_detail_router = APIRouter(prefix="/servers")


async def _render_server_tab(
    request: Request,
    template_name: str,
    *,
    internal_id: int,
    server_id: str,
    back: BackUrl,
    service: QueryService,
    include_period: bool = False,
    resource_index: int | None = None,
):
    server = await service.get_server(internal_id)
    if not server:
        raise HTTPException(status_code=404)
    period = await service.get_period_assessment(internal_id) if include_period else None
    resource_period = period.resources[resource_index] if period and resource_index is not None else None
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={
            "server": server,
            "period": period,
            "resource_period": resource_period,
            "back_url": safe_back(back, f"/servers/{server_id}"),
            "self_back": self_back(request),
            "service_categories": SERVICE_CATEGORIES,
        },
    )


@server_detail_router.get("/{server_id}")
async def get_server(
    request: Request,
    server_id: str,
    internal_id: ServerIdDep,
    service: QueryServiceDep,
    back: BackUrl = None,
):
    server = await service.get_server(internal_id)
    if not server:
        raise HTTPException(status_code=404)
    # 운영 신호는 window 집계라 inventory 캐시와 분리 조회.
    stability = await service.get_server_stability(server)

    period = await service.get_period_assessment(internal_id)
    # 최근 작업은 전체 노출 — task.install 이력은 서버 1대당 수십 건 규모라 큰 상한으로 사실상 전부 출력.
    recent_tasks = await service.list_recent_tasks(str(server.public_id), limit=1000, cursor=None)
    return templates.TemplateResponse(
        request=request,
        name="servers/detail.html",
        context={
            "server": server,
            "stability": stability,
            "period": period,
            "recent_tasks": recent_tasks,
            "back_url": safe_back(back, "/"),
            "self_back": self_back(request),
            "zdm_defaults": {
                "ip": get_web_settings().zdm_default_ip,
                "user": get_web_settings().zdm_default_user,
            },
        },
    )


@server_detail_router.get("/{server_id}/cpu")
async def get_cpu(
    request: Request,
    server_id: str,
    internal_id: ServerIdDep,
    service: QueryServiceDep,
    back: BackUrl = None,
):
    return await _render_server_tab(
        request,
        "servers/cpu.html",
        internal_id=internal_id,
        server_id=server_id,
        back=back,
        service=service,
        include_period=True,
        resource_index=0,
    )


@server_detail_router.get("/{server_id}/memory")
async def get_memory(
    request: Request,
    server_id: str,
    internal_id: ServerIdDep,
    service: QueryServiceDep,
    back: BackUrl = None,
):
    return await _render_server_tab(
        request,
        "servers/memory.html",
        internal_id=internal_id,
        server_id=server_id,
        back=back,
        service=service,
        include_period=True,
        resource_index=1,
    )


@server_detail_router.get("/{server_id}/services")
async def get_services(
    request: Request,
    server_id: str,
    internal_id: ServerIdDep,
    service: QueryServiceDep,
    back: BackUrl = None,
):
    return await _render_server_tab(
        request, "servers/services.html", internal_id=internal_id, server_id=server_id, back=back, service=service
    )


@server_detail_router.get("/{server_id}/metrics")
async def get_metrics(
    request: Request,
    server_id: str,
    internal_id: ServerIdDep,
    service: QueryServiceDep,
    back: BackUrl = None,
):
    return await _render_server_tab(
        request, "servers/metrics.html", internal_id=internal_id, server_id=server_id, back=back, service=service
    )


@server_detail_router.get("/{server_id}/storage")
async def get_storage(
    request: Request,
    server_id: str,
    internal_id: ServerIdDep,
    service: QueryServiceDep,
    back: BackUrl = None,
):
    result = await service.get_storage(internal_id)
    if not result:
        raise HTTPException(status_code=404)

    period = await service.get_period_assessment(internal_id)
    resource_period = period.resources[2] if period else None
    return templates.TemplateResponse(
        request=request,
        name="servers/storage.html",
        context={
            "storage": result,
            "period": period,
            "resource_period": resource_period,
            "back_url": safe_back(back, f"/servers/{server_id}"),
            "self_back": self_back(request),
        },
    )


@server_detail_router.get("/{server_id}/network")
async def get_network(
    request: Request,
    server_id: str,
    internal_id: ServerIdDep,
    service: QueryServiceDep,
    back: BackUrl = None,
):
    result = await service.get_network(internal_id)
    if not result:
        raise HTTPException(status_code=404)

    period = await service.get_period_assessment(internal_id)
    resource_period = period.resources[3] if period else None
    return templates.TemplateResponse(
        request=request,
        name="servers/network.html",
        context={
            "network": result,
            "period": period,
            "resource_period": resource_period,
            "back_url": safe_back(back, f"/servers/{server_id}"),
            "self_back": self_back(request),
        },
    )
