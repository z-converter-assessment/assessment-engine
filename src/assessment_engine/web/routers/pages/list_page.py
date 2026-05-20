"""서버 목록 SSR — `/servers/` (page=1 + 검색/필터 미사용 시 환경 요약·신호 + 환경 진단 latest)."""

from fastapi import APIRouter, Depends, Query, Request

from assessment_engine.web.deps import get_diagnostic_service, get_service
from assessment_engine.web.services.diagnostic_service import DiagnosticService, to_panel_payload
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.settings import diagnostic_settings, web_settings
from assessment_engine.web.templating import templates

list_page_router = APIRouter()


@list_page_router.get("/")
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
