"""보고서 라우터 — 환경 단위 발행 + (고객/엔지니어) 발행 이력 (T13).

흐름:
- /reports/environment: 환경 단위 high-level 양식. /servers/report (server scope)와 별도 template.
- /reports/history: AI 진단 이력과 분리된 보고서 발행 이력. job_type='customer_report'|'engineer_report'.
"""

from datetime import datetime
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger

from assessment_engine.db.repositories.base_diagnostic_repository import DiagnosticTimeRange
from assessment_engine.web.deps import get_diagnostic_service, get_service
from assessment_engine.web.services.diagnostic_service import DiagnosticService
from assessment_engine.web.services.mappers.report_history import to_report_history_item
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.templating import templates

reports_router = APIRouter(prefix="/reports", tags=["pages"])

_VIEW_TITLES: dict[str, str] = {
    "customer": "고객 제출용 (양식 A)",
    "engineer": "엔지니어 검토용 (양식 B)",
}


@reports_router.get("/environment")
async def environment_report(
    request: Request,
    time_range: DiagnosticTimeRange = Query(
        "14d",
        description="윈도우 — AI 진단과 동일 7개 (15m/1h/6h/24h/7d/14d/30d)",
    ),
    anchor_at: datetime | None = Query(None, description="분석 기준 시각 (ISO 8601). 미명시 시 현재"),
    view: Literal["customer", "engineer"] = Query("customer"),
    back: str | None = Query(None, description="← 이전 link 의 referrer. 미명시 시 /servers/"),
    service: QueryService = Depends(get_service),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """환경 단위 보고서 — 전체 등록 서버 대상 high-level 양식.

    server scope (/servers/report?ids=...) 와 별도 template (`reports/environment.html`).
    발행 이력은 best-effort row INSERT (scope='environment', job_type='customer_report'|'engineer_report').
    """
    summary = await service.get_environment_report(time_range, anchor_at, view=view)
    if summary.overview.total == 0:
        raise HTTPException(status_code=404, detail="no registered servers")

    public_ids = await service.list_all_server_public_ids()
    try:
        await diag_service.record_report_emission(
            view=view,
            scope="environment",
            server_public_ids=public_ids,
            period_days=summary.base.period_days,
            time_range=time_range,
            summary_meta={
                "anchor_at": summary.anchor_at.isoformat(),
            },
        )
    except Exception:
        logger.exception("env report emission record failed (best-effort)")

    back_url = back if back and back.startswith("/") and not back.startswith("//") else "/servers/"
    # 자식 link (참고자료 등) 의 back query 보존용 — 현재 URL encoding.
    self_back = quote(f"{request.url.path}?{request.url.query}", safe="")
    return templates.TemplateResponse(
        request=request,
        name="reports/environment.html",
        context={
            "summary": summary,
            "view": view,
            "view_title": _VIEW_TITLES[view],
            "back_url": back_url,
            "self_back": self_back,
        },
    )


_HISTORY_PAGE_LIMIT = 20
# 전체보기 mode — retention 90일 가정 + 운영자 회고용. 한 번에 SSR (페이지네이션 폐기, 단순화).
_HISTORY_FULL_LIMIT = 10000


@reports_router.get("/history")
async def history(
    request: Request,
    days: int = Query(90, ge=0, le=36500, description="최근 N일 (0 = 전체)"),
    view: Literal["all", "customer", "engineer"] = Query(
        "all",
        description="양식 필터: 전체 / 고객 / 엔지니어",
    ),
    scope: Literal["all", "environment", "server"] = Query(
        "all",
        description="범위 필터: 전체 / 환경 / 서버 1대",
    ),
    server_public_ids: list[str] | None = Query(
        None,
        description="특정 서버 관련 보고서만 필터. 서버 목록 'N대 선택 + 보고서 이력' 진입 시 자동 채워짐.",
    ),
    full: bool = Query(False, description="전체 보기 (기본 20건 → 전체)"),
    back: str | None = Query(None, description="← 이전 link referrer"),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """보고서 발행 이력 — 운영자 회고용. created_at DESC. SSR 첫 20건 + "전체보기" 옵션 (full=1).

    full=False (default): 최근 20 건. 운영자 빠른 확인.
    full=True: 모든 row. 한 번에 SSR (페이지네이션 없음 — 단순화, retention 90일 가정).
    """
    limit = _HISTORY_FULL_LIMIT if full else _HISTORY_PAGE_LIMIT
    records = await diag_service.list_reports(
        days,
        view,
        server_public_ids,
        cursor=None,
        limit=limit,
        scope=None if scope == "all" else scope,
    )
    # 본 이력 페이지 URL — 진입한 보고서의 "이전" 버튼이 돌아올 위치 (back chain).
    history_back = quote(f"{request.url.path}?{request.url.query}", safe="")
    items = [to_report_history_item(r, history_back) for r in records]
    back_url = back if back and back.startswith("/") and not back.startswith("//") else "/servers/"
    return templates.TemplateResponse(
        request=request,
        name="reports/history.html",
        context={
            "items": items,
            "days": days,
            "view": view,
            "scope": scope,
            "server_public_ids": server_public_ids,
            "full": full,
            "show_all_link": (not full) and len(records) == _HISTORY_PAGE_LIMIT,
            "back_url": back_url,
        },
    )


@reports_router.get("/right-sizing-thresholds")
async def right_sizing_thresholds(
    request: Request,
    back: str | None = Query(None, description="← 이전 link referrer. 미명시 시 /servers/"),
):
    """Right-sizing 분류 임계값 reference 페이지 — 환경 엔지니어 보고서에서 link 로 분리.

    `recommendation` 모듈 단일 진실의 분류·USE 축·입력·임계·근거 표 + 출처 설명.
    보고서·진단 양쪽이 참조하는 reference 자료 — 본 페이지에서만 한 번 정의 (T13).
    """
    back_url = back if back and back.startswith("/") and not back.startswith("//") else "/servers/"
    return templates.TemplateResponse(
        request=request,
        name="reports/right_sizing_thresholds.html",
        context={"back_url": back_url},
    )


# history.json endpoint 폐기 — JS 무한 fetch loop 버그 + UX 단순화 ("전체보기" 단일 옵션 SSR).
# 페이지네이션 자체 폐기, retention 90일 가정으로 _HISTORY_FULL_LIMIT (10000) 한 번에 SSR.
