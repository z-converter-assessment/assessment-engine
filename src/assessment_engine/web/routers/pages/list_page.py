"""서버 목록 SSR — `/servers/` (page=1 + 검색/필터 미사용 시 환경 요약·신호).

AI 진단 = 엔지니어 환경 보고서 안 본질 catalog 통합 (대시보드 안 별도 카드 없음).
"""

from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request

from assessment_engine import recommendation
from assessment_engine.web.deps import get_service
from assessment_engine.web.services.mappers.shared import DISTRO_FILTER_OPTIONS, PROVISIONING_CLASS_OPTIONS
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.services.service_classifier import SERVICE_CATEGORIES
from assessment_engine.web.settings import web_settings
from assessment_engine.web.templating import templates

list_page_router = APIRouter()

# 대시보드 목록 전체 로드 한도 — 기본 20행만 표시(client "더보기" clip)하되, 필터 적용 시 조건 맞는
# 전부를 보여주려면 client 에 전체가 있어야 한다(필터는 client-side hide/show). E2 page 기반의 의식적 예외:
# 대시보드 단일 화면은 환경요약·realtime 도 이미 전 서버를 로드하므로 목록도 전체 로드로 일관.
_LIST_FETCH_LIMIT = 10_000


@list_page_router.get("/")
async def list_servers(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    is_online: bool | None = Query(None),
    service_filter: str | None = Query(None, alias="service"),
    os_distro: str | None = Query(None),
    classification: str | None = Query(None),
    fragment: str | None = Query(None),
    service: QueryService = Depends(get_service),
):
    # 자동 갱신 fragment — live(환경요약·운영신호·실시간 메트릭) / rows(서버목록 행) 분리 렌더.
    # list.js 30초 폴링이 교체. 별도 path 대신 ?fragment= 분기 — /servers/{public_id} UUID 라우트 충돌 회피.
    # page 1 전체 기준(필터는 client 재적용).
    if fragment == "live":
        live = await service.get_dashboard_live()
        return templates.TemplateResponse(
            request=request,
            name="servers/_dashboard_live.html",
            context={
                "overview": live.overview,
                "attention": live.attention,
                "realtime": live.realtime,
                "topology": live.topology,
                "trend": live.trend,
                "generated_at": datetime.now(UTC),
                "window_days": recommendation.WINDOW_DAYS,
                "self_back": quote("/servers/", safe=""),
            },
        )
    if fragment == "rows":
        rows = await service.list_servers(
            1, _LIST_FETCH_LIMIT, None, None, service=None, os_distro=None, classification=None
        )
        return templates.TemplateResponse(
            request=request,
            name="servers/_server_rows.html",
            context={"servers": rows, "self_back": quote("/servers/", safe="")},
        )
    # page/limit Query 는 하위호환용 — 대시보드는 전체 로드 후 client 가 20행 clip("더보기")·필터 적용.
    servers = await service.list_servers(
        1,
        _LIST_FETCH_LIMIT,
        search,
        is_online,
        service=service_filter,
        os_distro=os_distro,
        classification=classification,
    )
    # 첫 페이지 + 검색·필터 미사용일 때만 환경 요약·신호 노출.
    # AI 진단 = 엔지니어 환경 보고서 안 본질 catalog 통합 (대시보드 안 별도 카드 없음).
    overview = None
    attention = None
    realtime = None
    topology = None
    trend = None
    if page == 1:
        live = await service.get_dashboard_live()
        overview, attention, realtime = live.overview, live.attention, live.realtime
        # 토폴로지는 자동갱신 라이브 fragment 밖에서 1회 렌더 (정적 인벤토리 — 30초 폴링 대상 아님).
        topology = live.topology
        trend = live.trend
    return templates.TemplateResponse(
        request=request,
        name="servers/list.html",
        context={
            "servers": servers,
            "overview": overview,
            "attention": attention,
            "realtime": realtime,
            "topology": topology,
            "trend": trend,
            # 페이지 렌더(새로고침) 시각 — 우측 상단 갱신 시각 표시용. UTC 전달, 템플릿 kst 필터로 표시(#F2).
            "generated_at": datetime.now(UTC),
            "window_days": recommendation.WINDOW_DAYS,
            "zdm_defaults": {
                "ip": web_settings.zdm_default_ip,
                "user": web_settings.zdm_default_user,
            },
            "discovery_default_target": web_settings.discovery_default_target,
            "discovery_default_port": web_settings.discovery_default_port,
            # OS 필터 옵션 — endoflife 카탈로그 distro 전체(수집 무관, 지원 distro 노출).
            # single source: shared.DISTRO_FILTER_OPTIONS.
            "filter_options": {
                "service_categories": SERVICE_CATEGORIES,
                "distro_options": DISTRO_FILTER_OPTIONS,
                "classifications": PROVISIONING_CLASS_OPTIONS,
            },
            # 자식 link (detail / 진단 이력 등) 의 back chain — 본 page URL (filter 상태 보존).
            "self_back": quote(f"{request.url.path}?{request.url.query}", safe=""),
        },
    )
