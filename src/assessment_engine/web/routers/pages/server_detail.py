"""서버 상세 SSR — `/servers/{id}` + 5 탭 (cpu/memory/services/performance) + storage/network.

5개 detail 탭이 동일 흐름이라 `_render_server_tab` helper 로 중복 제거.
storage/network 는 별도 service 메서드라 분리.

모든 endpoint 가 `back` Query 받음 + `back_url` context 전달 — back chain 일관 정공
(static-assets.md "네비게이션 규약" 절 단일 진실).
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from assessment_engine.web.deps import get_service, resolve_internal_id
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.services.service_classifier import SERVICE_CATEGORIES
from assessment_engine.web.settings import web_settings
from assessment_engine.web.templating import templates

server_detail_router = APIRouter()


def _safe_back(back: str | None, fallback: str) -> str:
    """back Query 검증 — relative path (/) 만 허용. protocol-relative (//) reject. 미명시 fallback."""
    if back and back.startswith("/") and not back.startswith("//"):
        return back
    return fallback


def _self_back(request: Request) -> str:
    """본 페이지 URL — 자식 link 의 back chain 전달용 (URL-encoded)."""
    return quote(f"{request.url.path}?{request.url.query}", safe="")


async def _render_server_tab(
    request: Request,
    template_name: str,
    *,
    internal_id: int,
    server_id: str,
    back: str | None,
    service: QueryService,
):
    """server 컨텍스트로 detail 탭 템플릿 렌더링. detail/cpu/memory/services/performance 공유."""
    server = await service.get_server(internal_id)
    if not server:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={
            "server": server,
            "back_url": _safe_back(back, f"/servers/{server_id}"),
            "self_back": _self_back(request),
            # services.html 범례 단일 진실 — service_classifier 카탈로그 파생.
            "service_categories": SERVICE_CATEGORIES,
        },
    )


@server_detail_router.get("/{server_id}")
async def get_server(
    request: Request,
    server_id: str,
    back: str | None = Query(None, description="← 이전 link referrer. 미명시 시 /servers/"),
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    # AI 진단 = 서버 엔지니어 보고서 안 본질 catalog 통합 (`/servers/{id}/report?view=engineer`).
    # 서버 detail = 인벤토리·메트릭·메모리·서비스·성능 본질 catalog.
    server = await service.get_server(internal_id)
    if not server:
        raise HTTPException(status_code=404)
    recent_tasks = await service.list_recent_tasks(str(server.public_id), limit=10, cursor=None)
    return templates.TemplateResponse(
        request=request,
        name="servers/detail.html",
        context={
            "server": server,
            "recent_tasks": recent_tasks,
            "back_url": _safe_back(back, "/servers/"),
            "self_back": _self_back(request),
            "zdm_defaults": {
                "ip": web_settings.zdm_default_ip,
                "user": web_settings.zdm_default_user,
            },
        },
    )


@server_detail_router.get("/{server_id}/cpu")
async def get_cpu(
    request: Request,
    server_id: str,
    back: str | None = Query(None),
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    return await _render_server_tab(
        request, "servers/cpu.html", internal_id=internal_id, server_id=server_id, back=back, service=service
    )


@server_detail_router.get("/{server_id}/memory")
async def get_memory(
    request: Request,
    server_id: str,
    back: str | None = Query(None),
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    return await _render_server_tab(
        request, "servers/memory.html", internal_id=internal_id, server_id=server_id, back=back, service=service
    )


@server_detail_router.get("/{server_id}/services")
async def get_services(
    request: Request,
    server_id: str,
    back: str | None = Query(None),
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    return await _render_server_tab(
        request, "servers/services.html", internal_id=internal_id, server_id=server_id, back=back, service=service
    )


@server_detail_router.get("/{server_id}/metrics")
async def get_metrics(
    request: Request,
    server_id: str,
    back: str | None = Query(None),
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    return await _render_server_tab(
        request, "servers/metrics.html", internal_id=internal_id, server_id=server_id, back=back, service=service
    )


# ─── 별도 service 메서드를 쓰는 탭 ───────────────────────────────────────


@server_detail_router.get("/{server_id}/storage")
async def get_storage(
    request: Request,
    server_id: str,
    back: str | None = Query(None),
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    result = await service.get_storage(internal_id)
    if not result:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="servers/storage.html",
        context={
            "storage": result,
            "back_url": _safe_back(back, f"/servers/{server_id}"),
            "self_back": _self_back(request),
        },
    )


@server_detail_router.get("/{server_id}/network")
async def get_network(
    request: Request,
    server_id: str,
    back: str | None = Query(None),
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    result = await service.get_network(internal_id)
    if not result:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="servers/network.html",
        context={
            "network": result,
            "back_url": _safe_back(back, f"/servers/{server_id}"),
            "self_back": _self_back(request),
        },
    )
