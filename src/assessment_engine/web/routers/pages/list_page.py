"""SSR 페이지 — 환경 개요(`/`) · 서버 목록(`/servers`) · 환경 단위(`/environment/*`).

URL 을 명사로 가른다 — 환경 단위(개요·자원평가·실시간·성능·토폴로지)와 서버 단위(`/servers/*`).
"""

from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Query, Request

from assessment_engine.db.repositories.query.types import (
    DIAGNOSTIC_DEFAULT_TIME_RANGE,
    TimeRange,
)
from assessment_engine.domain import right_sizing
from assessment_engine.domain.service_classifier import SERVICE_CATEGORIES
from assessment_engine.web.deps import QueryServiceDep
from assessment_engine.web.routers._back import BackUrl, safe_back, self_back, self_back_of
from assessment_engine.web.routers._fragment import RealtimeFragment, ResultFragment, RowsFragment
from assessment_engine.web.services.mappers.constants import DIAGNOSTIC_RANGE_LABEL_KR, PROVISIONING_CLASS_OPTIONS
from assessment_engine.web.services.mappers.os_eol import DISTRO_FILTER_OPTIONS
from assessment_engine.web.services.query import QueryService
from assessment_engine.web.settings import get_web_settings
from assessment_engine.web.templating import templates

overview_router = APIRouter()
servers_list_router = APIRouter(prefix="/servers")
environment_router = APIRouter(prefix="/environment")

# 전체를 한 번에 받아 client 가 clip·필터한다 — E2 page 페이지네이션 정책의 의식적 예외.
_LIST_FETCH_LIMIT = 10_000


@environment_router.get("/metrics")
async def environment_metrics(
    request: Request,
    service: QueryServiceDep,
    back: BackUrl = None,
    ids: Annotated[str | None, Query(description="public_ids(comma) — 선택 N대 한정. 미지정 시 전체 환경.")] = None,
):
    """환경 성능 추이 (live) — 전체 환경 차트 10종. ids 면 선택 N대 한정."""
    valid_pids = await _resolve_selection_pids(service, ids)
    selection_ids = ",".join(valid_pids)
    # 판정 crossing 서버 수 차트의 Y축 고정 상한 — 선택 N대로 들어와도 fleet 전체 규모를 쓴다("이론상 최대치").
    # 상단 바와 같은 total_count 산식이어야 해 get_fleet_status 를 재사용한다.
    total_hosts = (await service.get_fleet_status()).total_count
    return templates.TemplateResponse(
        request=request,
        name="servers/environment_metrics.html",
        context={
            "active_nav": "performance",
            "window_days": right_sizing.WINDOW_DAYS,
            "generated_at": datetime.now(UTC),
            "back_url": safe_back(back, "/"),
            "self_back": self_back_of("/environment/metrics", f"ids={selection_ids}" if selection_ids else ""),
            "selection_ids": selection_ids,
            "selection_count": len(valid_pids),
            "total_hosts": total_hosts,
        },
    )


@environment_router.get("/realtime")
async def environment_realtime(
    request: Request,
    service: QueryServiceDep,
    back: BackUrl = None,
    fragment: RealtimeFragment = None,
    ids: Annotated[str | None, Query(description="public_ids(comma) — 선택 N대 한정. 미지정 시 전체 환경.")] = None,
):
    """실시간 현황 — 현재 평균 활용률 + 서버별 부하. ids 면 선택 N대 한정.

    fragment=realtime: 메트릭 partial 만 재렌더 (JS 폴링이 mount innerHTML 교체).
    """
    now = datetime.now(UTC)
    valid_pids = await _resolve_selection_pids(service, ids)
    server_ids = None
    if valid_pids:
        sid_map = await service.resolve_server_ids(valid_pids)
        server_ids = [sid_map[p] for p in valid_pids]
    selection_ids = ",".join(valid_pids)
    realtime = await service.get_environment_realtime(server_ids)
    self_back = self_back_of("/environment/realtime", f"ids={selection_ids}" if selection_ids else "")
    if fragment == "realtime":
        # 운영 신호는 느린 신호라 full-page 에만 정적 렌더한다 — 폴링 재조회에 넣지 않는다.
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
            "back_url": safe_back(back, "/"),
            "self_back": self_back,
            "selection_ids": selection_ids,
            "selection_count": len(valid_pids),
            "active_nav": "realtime",
        },
    )


async def _resolve_selection_pids(
    service: QueryService,
    ids: str | None,
) -> list[str]:
    """ids(comma public_ids) -> 존재하는 public_id 만. 빈/미지정이면 빈 list (= 전체 환경)."""
    public_ids = [pid.strip() for pid in (ids or "").split(",") if pid.strip()]
    if not public_ids:
        return []
    sid_map = await service.resolve_server_ids(public_ids)
    return [pid for pid in public_ids if pid in sid_map]


@environment_router.get("/topology")
async def topology(
    request: Request,
    service: QueryServiceDep,
    back: BackUrl = None,
):
    """네트워크 토폴로지 — L3 subnet 공동소속 그래프 (전체 인벤토리, 범위 필터 없음)."""
    topo = await service.get_topology()
    return templates.TemplateResponse(
        request=request,
        name="servers/topology.html",
        context={
            "topology": topo,
            "generated_at": datetime.now(UTC),
            "back_url": safe_back(back, "/"),
            "self_back": self_back_of("/environment/topology"),
            "active_nav": "topology",
        },
    )


@environment_router.get("/assessment")
async def assessment(
    request: Request,
    service: QueryServiceDep,
    time_range: TimeRange = DIAGNOSTIC_DEFAULT_TIME_RANGE,
    anchor_at: datetime | None = None,
    fragment: ResultFragment = None,
    back: BackUrl = None,
):
    """환경 자원 평가 — 표준 창(WINDOW_DAYS) 분류 + 자원 부족·효율화. 윈도우/앵커 override 가능.

    분류 창은 서버 목록·보고서·환경 개요 카드와 같다(#E3 정합). fragment=result: 결과 partial 만 재렌더.
    """
    result = await service.get_environment_assessment(time_range, anchor_at)
    qs = f"time_range={time_range}" + (f"&anchor_at={quote(anchor_at.isoformat(), safe='')}" if anchor_at else "")
    ctx: dict[str, Any] = {
        "overview": result.overview,
        "action": result.action,
        "time_range": time_range,
        "window_label": DIAGNOSTIC_RANGE_LABEL_KR.get(time_range, time_range),
        "self_back": self_back_of("/environment/assessment", qs),
    }
    if fragment == "result":
        return templates.TemplateResponse(request=request, name="servers/_assessment_result.html", context=ctx)
    ctx["active_nav"] = "assessment"
    ctx["back_url"] = safe_back(back, "/")
    return templates.TemplateResponse(request=request, name="servers/assessment.html", context=ctx)


@overview_router.get("/")
async def overview(
    request: Request,
    service: QueryServiceDep,
):
    """환경 개요 (홈) — 집계 위젯만. 자동 갱신 없음(정적 집계라 진입 시 1회 렌더)."""
    overview = await service.get_dashboard_overview()
    ctx: dict[str, Any] = {
        "overview": overview,
        "generated_at": datetime.now(UTC),
        # 도넛 3종 공통 창 라벨 — 분류·이용률·포화가 한 창이라는 표시(#E3 정합).
        "classification_window_label": f"{right_sizing.WINDOW_DAYS}일",
        "active_nav": "overview",
        "self_back": self_back_of("/"),
    }
    return templates.TemplateResponse(request=request, name="servers/overview.html", context=ctx)


@servers_list_router.get("")
async def servers_list(
    request: Request,
    service: QueryServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    search: str | None = None,
    is_online: bool | None = None,
    service_filter: Annotated[str | None, Query(alias="service")] = None,
    os_distro: str | None = None,
    classification: str | None = None,
    os_eol: str | None = None,
    fragment: RowsFragment = None,
):
    """서버 목록 — 검색·필터 + 선택 N대 액션(보고서·install·export).

    page/limit 은 지금 쓰이지 않는다(전체 로드 후 client clip) — 서버사이드 페이지네이션 도입 시 진입점.
    fragment=rows: 행 partial 만 재렌더.
    """
    if fragment == "rows":
        rows = await service.list_servers(
            1, _LIST_FETCH_LIMIT, None, None, service=None, os_distro=None, classification=None
        )
        return templates.TemplateResponse(
            request=request,
            name="servers/_server_rows.html",
            context={"servers": rows, "self_back": self_back_of("/servers")},
        )
    servers = await service.list_servers(
        1,
        _LIST_FETCH_LIMIT,
        search,
        is_online,
        service=service_filter,
        os_distro=os_distro,
        classification=classification,
        os_eol=os_eol,
    )
    return templates.TemplateResponse(
        request=request,
        name="servers/list_table.html",
        context={
            "servers": servers,
            "generated_at": datetime.now(UTC),
            "zdm_defaults": {
                "ip": get_web_settings().zdm_default_ip,
                "user": get_web_settings().zdm_default_user,
            },
            # OS 옵션은 수집 결과가 아니라 endoflife 카탈로그 전체 — 지원 distro 를 다 노출한다.
            "filter_options": {
                "service_categories": SERVICE_CATEGORIES,
                "distro_options": DISTRO_FILTER_OPTIONS,
                "classifications": PROVISIONING_CLASS_OPTIONS,
            },
            "active_nav": "list",
            # 자식 link 의 back chain — 쿼리스트링째 담아 filter 상태를 보존한다.
            "self_back": self_back(request),
        },
    )
