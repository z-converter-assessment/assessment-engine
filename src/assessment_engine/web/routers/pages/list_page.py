"""서버 목록 SSR — `/servers/` (page=1 + 검색/필터 미사용 시 환경 요약·신호).

AI 진단 = 엔지니어 환경 보고서 안 본질 catalog 통합 (대시보드 안 별도 카드 없음).
"""

from datetime import UTC, datetime
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Query, Request

from assessment_engine import recommendation
from assessment_engine.db.repositories.base_diagnostic_repository import DIAGNOSTIC_RANGE_LABEL_KR
from assessment_engine.db.repositories.query.types import AUTO_BUCKET
from assessment_engine.web.deps import get_service
from assessment_engine.web.services.mappers.shared import DISTRO_FILTER_OPTIONS, PROVISIONING_CLASS_OPTIONS
from assessment_engine.web.services.query_service import DASHBOARD_TIME_RANGE, QueryService
from assessment_engine.web.services.service_classifier import SERVICE_CATEGORIES
from assessment_engine.web.settings import web_settings
from assessment_engine.web.templating import templates

list_page_router = APIRouter()

# 환경 부하 추이 집계 단위 라벨 — DASHBOARD_TIME_RANGE 윈도우의 AUTO_BUCKET 한국어 표기 (표제용).
_BUCKET_KO = {
    "1m": "1분",
    "5m": "5분",
    "15m": "15분",
    "30m": "30분",
    "1h": "1시간",
    "3h": "3시간",
    "6h": "6시간",
    "12h": "12시간",
    "1d": "1일",
}
_TREND_BUCKET_LABEL = _BUCKET_KO[AUTO_BUCKET[DASHBOARD_TIME_RANGE]]
# 대시보드 윈도우 한국어 라벨 ("6시간") — window_meta 표제 "최근 {라벨}" 표기.
_DASHBOARD_WINDOW_LABEL = DIAGNOSTIC_RANGE_LABEL_KR[DASHBOARD_TIME_RANGE]

# 대시보드 목록 전체 로드 한도 — 기본 20행만 표시(client "더보기" clip)하되, 필터 적용 시 조건 맞는
# 전부를 보여주려면 client 에 전체가 있어야 한다(필터는 client-side hide/show). E2 page 기반의 의식적 예외:
# 대시보드 단일 화면은 환경요약·realtime 도 이미 전 서버를 로드하므로 목록도 전체 로드로 일관.
_LIST_FETCH_LIMIT = 10_000


@list_page_router.get("/environment/metrics")
async def environment_metrics(
    request: Request,
    back: str | None = Query(None),
    ids: str | None = Query(None, description="public_ids(comma) — 선택 N대 한정. 미지정 시 전체 환경."),
    service: QueryService = Depends(get_service),
):
    """환경 성능 추이 (live) — 전체 환경 차트 10종. ids 면 선택 N대 한정.

    server_detail 의 `/{server_id}/metrics`(UUID) 보다 먼저 등록(list_page_router 우선 include)이라
    'environment' 가 UUID 매칭 422 로 가지 않고 본 라우트로 잡힘.
    실시간 메트릭은 `/environment/realtime` 로 분리(현황 모니터링과 시계열 추이는 별개 용도)."""
    valid_pids = await _resolve_selection_pids(service, ids)
    selection_ids = ",".join(valid_pids)
    path = "/servers/environment/metrics" + (f"?ids={selection_ids}" if selection_ids else "")
    return templates.TemplateResponse(
        request=request,
        name="servers/environment_metrics.html",
        context={
            "window_days": recommendation.WINDOW_DAYS,
            "generated_at": datetime.now(UTC),
            "back_url": unquote(back) if back else "/servers/",
            "self_back": quote(path, safe=""),
            "selection_ids": selection_ids,
            "selection_count": len(valid_pids),
        },
    )


@list_page_router.get("/environment/realtime")
async def environment_realtime(
    request: Request,
    back: str | None = Query(None),
    fragment: str | None = Query(None),
    ids: str | None = Query(None, description="public_ids(comma) — 선택 N대 한정. 미지정 시 전체 환경."),
    service: QueryService = Depends(get_service),
):
    """실시간 메트릭 (live 현황 모니터링) — 현재 평균 활용률 + 현재 부하 상위. ids 면 선택 N대 한정.

    fragment=realtime: 실시간 메트릭 partial 만 재렌더 (JS 30초 폴링이 mount innerHTML 교체)."""
    now = datetime.now(UTC)
    valid_pids = await _resolve_selection_pids(service, ids)
    server_ids = None
    if valid_pids:
        sid_map = await service.resolve_server_ids(valid_pids)
        server_ids = [sid_map[p] for p in valid_pids]
    selection_ids = ",".join(valid_pids)
    path = "/servers/environment/realtime" + (f"?ids={selection_ids}" if selection_ids else "")
    realtime = await service.get_environment_realtime(server_ids)
    self_back = quote(path, safe="")
    if fragment == "realtime":
        return templates.TemplateResponse(
            request=request,
            name="servers/_environment_realtime.html",
            context={"realtime": realtime, "generated_at": now, "self_back": self_back},
        )
    return templates.TemplateResponse(
        request=request,
        name="servers/realtime.html",
        context={
            "realtime": realtime,
            "generated_at": now,
            "back_url": unquote(back) if back else "/servers/",
            "self_back": self_back,
            "selection_ids": selection_ids,
            "selection_count": len(valid_pids),
        },
    )


async def _resolve_selection_pids(service: QueryService, ids: str | None) -> list[str]:
    """ids(comma public_ids) -> 존재하는 public_id 만. 빈/미지정이면 빈 list (= 전체 환경)."""
    public_ids = [pid.strip() for pid in (ids or "").split(",") if pid.strip()]
    if not public_ids:
        return []
    sid_map = await service.resolve_server_ids(public_ids)
    return [pid for pid in public_ids if pid in sid_map]


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
                "topology": live.topology,
                "trend": live.trend,
                "generated_at": datetime.now(UTC),
                "window_label": _DASHBOARD_WINDOW_LABEL,
                "window_range": DASHBOARD_TIME_RANGE,
                "trend_bucket_label": _TREND_BUCKET_LABEL,
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
    topology = None
    trend = None
    if page == 1:
        live = await service.get_dashboard_live()
        overview, attention = live.overview, live.attention
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
            "topology": topology,
            "trend": trend,
            # 페이지 렌더(새로고침) 시각 — 우측 상단 갱신 시각 표시용. UTC 전달, 템플릿 kst 필터로 표시(#F2).
            "generated_at": datetime.now(UTC),
            "window_label": _DASHBOARD_WINDOW_LABEL,
            "window_range": DASHBOARD_TIME_RANGE,
            "trend_bucket_label": _TREND_BUCKET_LABEL,
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
