"""서버 목록 SSR — `/servers/` (page=1 + 검색/필터 미사용 시 환경 요약·신호).

AI 진단 = 엔지니어 환경 보고서 안 본질 catalog 통합 (대시보드 안 별도 카드 없음).
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request

from assessment_engine.web.deps import get_service
from assessment_engine.web.services.mappers.shared import PROVISIONING_CLASSES
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.services.service_classifier import SERVICE_CATEGORIES
from assessment_engine.web.settings import web_settings
from assessment_engine.web.templating import templates

list_page_router = APIRouter()


@list_page_router.get("/")
async def list_servers(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    is_online: bool | None = Query(None),
    service_filter: str | None = Query(None, alias="service"),
    os_id: str | None = Query(None),
    classification: str | None = Query(None),
    service: QueryService = Depends(get_service),
):
    servers = await service.list_servers(
        page,
        limit,
        search,
        is_online,
        service=service_filter,
        os_id=os_id,
        classification=classification,
    )
    # 첫 페이지 + 검색·필터 미사용일 때만 환경 요약·신호 노출.
    # AI 진단 = 엔지니어 환경 보고서 안 본질 catalog 통합 (대시보드 안 별도 카드 없음).
    overview = None
    attention = None
    if page == 1:
        overview = await service.get_environment_overview()
        attention = await service.get_attention_signals()
    # dropdown option 카탈로그 — single source: service_classifier / shared.py / DB distinct.
    distinct_os_ids = await service.list_distinct_os_ids()
    return templates.TemplateResponse(
        request=request,
        name="servers/list.html",
        context={
            "servers": servers,
            "overview": overview,
            "attention": attention,
            "zdm_defaults": {
                "ip": web_settings.zdm_default_ip,
                "user": web_settings.zdm_default_user,
            },
            "discovery_default_target": web_settings.discovery_default_target,
            "discovery_default_port": web_settings.discovery_default_port,
            "filter_options": {
                "service_categories": SERVICE_CATEGORIES,
                "os_ids": distinct_os_ids,
                "classifications": PROVISIONING_CLASSES,
            },
            # 자식 link (detail / 진단 이력 등) 의 back chain — 본 page URL (filter 상태 보존).
            "self_back": quote(f"{request.url.path}?{request.url.query}", safe=""),
        },
    )
