"""서버 상세 SSR — `/servers/{id}` + 5 탭 (cpu/memory/services/performance) + storage/network.

5개 detail 탭이 동일 흐름이라 `_render_server_tab` helper 로 중복 제거.
storage/network 는 별도 service 메서드라 분리.

모든 endpoint 가 `back` Query 받음 + `back_url` context 전달 — back chain 일관 정공
(static-assets.md "네비게이션 규약" 절 단일 진실).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from assessment_engine.service_classifier import SERVICE_CATEGORIES
from assessment_engine.web.deps import get_service, resolve_internal_id
from assessment_engine.web.routers._back import safe_back, self_back
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.settings import get_web_settings
from assessment_engine.web.templating import templates

server_detail_router = APIRouter(prefix="/servers")


async def _render_server_tab(
    request: Request,
    template_name: str,
    *,
    internal_id: int,
    server_id: str,
    back: str | None,
    service: QueryService,
    include_period: bool = False,
    resource_index: int | None = None,
):
    """server 컨텍스트로 detail 탭 템플릿 렌더링. detail/cpu/memory/services/performance 공유.

    include_period=True — '최근 N일' 이용률·포화 평가(get_period_assessment) 도 함께 조회해 context 에 배선.
    자원별 상세 탭(cpu 등)이 서버 세부와 동일 14일 데이터를 보여줘야 할 때만 켠다(불요한 쿼리 회피).
    resource_index — period.resources[cpu=0/mem=1/disk=2/net=3] 중 그 탭 전용 1개를 골라 resource_period 로
    전달(P3: 템플릿 selectattr 금지라 라우터에서 인덱싱 — Python 층 단순 접근, 계산 아님).
    """
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
            # services.html 범례 단일 진실 — service_classifier 카탈로그 파생.
            "service_categories": SERVICE_CATEGORIES,
        },
    )


@server_detail_router.get("/{server_id}")
async def get_server(
    request: Request,
    server_id: str,
    back: str | None = Query(None, description="← 이전 link referrer. 미명시 시 / (환경 개요)"),
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    server = await service.get_server(internal_id)
    if not server:
        raise HTTPException(status_code=404)
    # 운영 신호 — 전구간 재부팅·에이전트 재시작 + OS 지원종료 (window 집계라 inventory 캐시와 분리 조회).
    stability = await service.get_server_stability(server)
    # '최근 14일' 평가 카드 — 자원별 이용률(p95)+포화 2축 (right-sizing 분류 창). 실시간 카드와 분리, SSR precompute.
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
    back: str | None = Query(None),
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    return await _render_server_tab(
        request, "servers/cpu.html", internal_id=internal_id, server_id=server_id, back=back, service=service,
        include_period=True, resource_index=0,
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
        request, "servers/memory.html", internal_id=internal_id, server_id=server_id, back=back, service=service,
        include_period=True, resource_index=1,
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


# 별도 service 메서드를 쓰는 탭 (공유 helper 미사용)


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
    # period.resources[cpu=0/mem=1/disk=2/net=3] — 스토리지(용량+I/O 통합) 탭 전용 1개(_render_server_tab 과
    # 동일 규약, storage 는 별도 service 메서드라 여기서 직접 배선).
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
    back: str | None = Query(None),
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    result = await service.get_network(internal_id)
    if not result:
        raise HTTPException(status_code=404)
    # period.resources[cpu=0/mem=1/disk=2/net=3] — 네트워크 탭 전용 1개(_render_server_tab 과 동일 규약,
    # network 는 별도 service 메서드라 여기서 직접 배선).
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
