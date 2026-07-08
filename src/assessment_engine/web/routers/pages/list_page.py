"""SSR 페이지 — 환경 개요(`/`) · 서버 목록(`/servers`) · 환경 단위(`/environment/*`).

URL 명사 분리: 환경 단위(개요·자원평가·실시간·성능·토폴로지)는 `/` 와 `/environment/*`, 서버 단위는
`/servers/*`(server_detail).
"""

from datetime import UTC, datetime
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Query, Request

from assessment_engine import recommendation
from assessment_engine.db.repositories.base_diagnostic_repository import (
    DIAGNOSTIC_DEFAULT_TIME_RANGE,
    DiagnosticTimeRange,
)
from assessment_engine.service_classifier import SERVICE_CATEGORIES
from assessment_engine.web.deps import get_service
from assessment_engine.web.services.mappers.shared import (
    DIAGNOSTIC_RANGE_LABEL_KR,
    DISTRO_FILTER_OPTIONS,
    PROVISIONING_CLASS_OPTIONS,
)
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.settings import web_settings
from assessment_engine.web.templating import templates

# 환경 개요(/) · 서버 목록(/servers) · 환경 단위(/environment/*) 3 라우터 — URL 명사 분리.
overview_router = APIRouter()
servers_list_router = APIRouter(prefix="/servers")
environment_router = APIRouter(prefix="/environment")

# 서버 목록 전체 로드 한도 — 기본 20행 표시(client clip), 필터는 client-side hide/show. E2 page 의식적 예외.
_LIST_FETCH_LIMIT = 10_000


@environment_router.get("/metrics")
async def environment_metrics(
    request: Request,
    back: str | None = Query(None),
    ids: str | None = Query(None, description="public_ids(comma) — 선택 N대 한정. 미지정 시 전체 환경."),
    service: QueryService = Depends(get_service),
):
    """환경 성능 추이 (live) — 전체 환경 차트 10종. ids 면 선택 N대 한정. 환경 단위 `/environment` 그룹."""
    valid_pids = await _resolve_selection_pids(service, ids)
    selection_ids = ",".join(valid_pids)
    path = "/environment/metrics" + (f"?ids={selection_ids}" if selection_ids else "")
    return templates.TemplateResponse(
        request=request,
        name="servers/environment_metrics.html",
        context={
            "active_nav": "performance",
            "window_days": recommendation.WINDOW_DAYS,
            "generated_at": datetime.now(UTC),
            "back_url": unquote(back) if back else "/",
            "self_back": quote(path, safe=""),
            "selection_ids": selection_ids,
            "selection_count": len(valid_pids),
        },
    )


@environment_router.get("/realtime")
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
    path = "/environment/realtime" + (f"?ids={selection_ids}" if selection_ids else "")
    realtime = await service.get_environment_realtime(server_ids)
    self_back = quote(path, safe="")
    if fragment == "realtime":
        # 현황 메트릭 fragment 만 — 운영 신호는 느린 신호라 full-page 에만 정적 렌더(fragment 재조회에 불포함).
        return templates.TemplateResponse(
            request=request,
            name="servers/_environment_realtime.html",
            context={"realtime": realtime, "generated_at": now, "self_back": self_back},
        )
    # 운영 신호 — selection(ids) 이면 선택 N대 호스트로 한정, 전체면 환경 전체. limit_each=None = 3 카탈로그 전수 출력.
    if server_ids is not None:
        attention = await service.get_selection_attention(server_ids, now)
    else:
        attention = await service.get_attention_signals(end=now, limit_each=None)
    return templates.TemplateResponse(
        request=request,
        name="servers/realtime.html",
        context={
            "realtime": realtime,
            "attention": attention,
            "agent_restart_threshold": web_settings.agent_restart_alert_threshold,
            "generated_at": now,
            "back_url": unquote(back) if back else "/",
            "self_back": self_back,
            "selection_ids": selection_ids,
            "selection_count": len(valid_pids),
            "active_nav": "realtime",
        },
    )


async def _resolve_selection_pids(service: QueryService, ids: str | None) -> list[str]:
    """ids(comma public_ids) -> 존재하는 public_id 만. 빈/미지정이면 빈 list (= 전체 환경)."""
    public_ids = [pid.strip() for pid in (ids or "").split(",") if pid.strip()]
    if not public_ids:
        return []
    sid_map = await service.resolve_server_ids(public_ids)
    return [pid for pid in public_ids if pid in sid_map]


@environment_router.get("/topology")
async def topology(
    request: Request,
    back: str | None = Query(None),
    service: QueryService = Depends(get_service),
):
    """네트워크 토폴로지 전용 — L3 subnet 공동소속 그래프. 환경 단위 `/environment` 그룹.

    현재 전체 인벤토리 그래프 — 대규모 범위 좁히기(subnet/host 필터)는 후속."""
    topo = await service.get_topology()
    return templates.TemplateResponse(
        request=request,
        name="servers/topology.html",
        context={
            "topology": topo,
            "generated_at": datetime.now(UTC),
            "back_url": unquote(back) if back else "/",
            "self_back": quote("/environment/topology", safe=""),
            "active_nav": "topology",
        },
    )


@environment_router.get("/assessment")
async def assessment(
    request: Request,
    time_range: DiagnosticTimeRange = Query(DIAGNOSTIC_DEFAULT_TIME_RANGE),
    anchor_at: datetime | None = Query(None),
    fragment: str | None = Query(None),
    back: str | None = Query(None),
    service: QueryService = Depends(get_service),
):
    """환경 자원 평가 — 14일 표준 창(WINDOW_DAYS) 분류 + 자원 부족·효율화. 윈도우/앵커 override 가능.

    분류 창은 서버 목록·보고서·환경 개요 카드와 같은 14일(#F10 #E3 정합). 기본값 `DIAGNOSTIC_DEFAULT_TIME_RANGE`.
    환경 단위 `/environment` 그룹. fragment=result: 결과 partial 만 재렌더 (JS swap, 풀 reload 회피)."""
    result = await service.get_environment_assessment(time_range, anchor_at)
    qs = f"?time_range={time_range}" + (f"&anchor_at={quote(anchor_at.isoformat(), safe='')}" if anchor_at else "")
    ctx = {
        "overview": result.overview,
        "action": result.action,
        "time_range": time_range,
        "window_label": DIAGNOSTIC_RANGE_LABEL_KR.get(time_range, time_range),
        "self_back": quote(f"/environment/assessment{qs}", safe=""),
    }
    if fragment == "result":
        return templates.TemplateResponse(request=request, name="servers/_assessment_result.html", context=ctx)
    ctx["active_nav"] = "assessment"
    ctx["back_url"] = unquote(back) if back else "/"
    return templates.TemplateResponse(request=request, name="servers/assessment.html", context=ctx)


@overview_router.get("/")
async def overview(
    request: Request,
    service: QueryService = Depends(get_service),
):
    """환경 개요 (홈, `/`) — 집계 위젯(환경 요약·자원 이용·포화 6도넛·수집 건전성).

    서버 목록은 `/servers`, 환경 단위 분석은 `/environment/*` 로 분리. 집계형 위젯만 본 페이지에 남는다.
    운영 신호는 실시간 현황(`/environment/realtime`)으로 분리. 자동 갱신 없음 — 정적 집계라 진입 시 1회 렌더."""
    overview = await service.get_dashboard_overview()
    ctx = {
        "overview": overview,
        # 페이지 렌더(새로고침) 시각 — 우측 상단 표시용. UTC 전달, 템플릿 kst 필터로 표시(#F2).
        "generated_at": datetime.now(UTC),
        # 자원 적정성·이용·포화 도넛 공통 창 라벨 — WINDOW_DAYS 파생(14일). 분류·이용률·포화 한 창(#E3 정합).
        "classification_window_label": f"{recommendation.WINDOW_DAYS}일",
        "active_nav": "overview",
        "self_back": quote("/", safe=""),
    }
    return templates.TemplateResponse(request=request, name="servers/overview.html", context=ctx)


@servers_list_router.get("")
async def servers_list(
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
    """서버 목록 (`/servers`) — 검색·필터 + 선택 N대 액션(보고서·install·export).

    fragment=rows: 서버목록 행 partial 만 재렌더.
    현재 전체 로드 후 client clip — page/limit Query 는 서버사이드 페이지네이션 도입 시 사용 (E2 page 정책)."""
    if fragment == "rows":
        rows = await service.list_servers(
            1, _LIST_FETCH_LIMIT, None, None, service=None, os_distro=None, classification=None
        )
        return templates.TemplateResponse(
            request=request,
            name="servers/_server_rows.html",
            context={"servers": rows, "self_back": quote("/servers", safe="")},
        )
    servers = await service.list_servers(
        1,
        _LIST_FETCH_LIMIT,
        search,
        is_online,
        service=service_filter,
        os_distro=os_distro,
        classification=classification,
    )
    return templates.TemplateResponse(
        request=request,
        name="servers/list_table.html",
        context={
            "servers": servers,
            "generated_at": datetime.now(UTC),
            "zdm_defaults": {
                "ip": web_settings.zdm_default_ip,
                "user": web_settings.zdm_default_user,
            },
            # OS 필터 옵션 — endoflife 카탈로그 distro 전체(수집 무관, 지원 distro 노출).
            # single source: shared.DISTRO_FILTER_OPTIONS.
            "filter_options": {
                "service_categories": SERVICE_CATEGORIES,
                "distro_options": DISTRO_FILTER_OPTIONS,
                "classifications": PROVISIONING_CLASS_OPTIONS,
            },
            "active_nav": "list",
            # 자식 link (detail / 진단 이력 등) 의 back chain — 본 page URL (filter 상태 보존).
            "self_back": quote(f"{request.url.path}?{request.url.query}", safe=""),
        },
    )
