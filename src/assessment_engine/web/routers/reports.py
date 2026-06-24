"""보고서 라우터 — 환경 단위 발행 + (고객/엔지니어) 발행 이력 (T13).

흐름:
- /reports/environment: 환경 단위 high-level 양식. /reports/servers (server scope)와 별도 template.
  GET `?job={id}` = 정적 스냅샷, job 없음 = live read-only preview. POST `/environment/emit` = 발행.
- /reports/history: 보고서 발행 이력. job_type='customer_report'|'engineer_report'.
"""

from datetime import datetime
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from assessment_engine.db.repositories.base_diagnostic_repository import (
    DIAGNOSTIC_DEFAULT_TIME_RANGE,
    DiagnosticTimeRange,
)
from assessment_engine.web.deps import get_diagnostic_service
from assessment_engine.web.services.diagnostic_service import DiagnosticService, _normalize_anchor
from assessment_engine.web.services.mappers.report_history import to_report_history_item
from assessment_engine.web.services.mappers.shared import build_service_badge_reference
from assessment_engine.web.services.report_serializer import (
    REPORT_KIND_ENV,
    env_report_from_dict,
)
from assessment_engine.web.templating import templates

reports_router = APIRouter(prefix="/reports", tags=["pages"])
# 참고(기준·임계값)는 보고서가 아닌 독립 reference — /reference (사이드바 참고 그룹).
reference_router = APIRouter(tags=["pages"])

_VIEW_TITLES: dict[str, str] = {
    "customer": "고객 제출용",
    "engineer": "엔지니어 검토용",
}


@reports_router.get("/environment")
async def environment_report(
    request: Request,
    job: str | None = Query(None, description="발행된 보고서 job_id — 정적 스냅샷 렌더"),
    time_range: DiagnosticTimeRange = Query(
        DIAGNOSTIC_DEFAULT_TIME_RANGE,
        description="윈도우 (live preview) — 7개 (15m/1h/6h/24h/7d/14d/30d)",
    ),
    anchor_at: datetime | None = Query(None, description="분석 기준 시각 (live preview). 미명시 시 현재"),
    view: Literal["customer", "engineer"] = Query("customer"),
    back: str | None = Query(None, description="← 이전 link 의 referrer. 미명시 시 /servers/"),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """환경 단위 보고서 — job 있으면 정적 스냅샷, 없으면 발행 전 컨트롤(본문은 발행해야 생성)."""
    back_url = back if back and back.startswith("/") and not back.startswith("//") else "/"
    self_back = quote(f"{request.url.path}?{request.url.query}", safe="")

    if job:
        return await _render_environment_snapshot(request, job, back_url, self_back, diag_service)

    # 발행 전 — 컨트롤만 노출(본문 미표시, summary 미계산). 발행(POST emit)해야 스냅샷 생성 + 화면 표시 + 이력 추가.
    return templates.TemplateResponse(
        request=request,
        name="reports/environment.html",
        context={
            "active_nav": "environment",
            "summary": None,
            "view": view,
            "view_title": _VIEW_TITLES[view],
            "back_url": back_url,
            "self_back": self_back,
            "report_job_id": None,
            "time_range": time_range,
        },
    )


def _render_job_progress(request: Request, rec, back_url: str):
    """비동기 보고서 job 이 succeeded 아님 — 진행(pending/running) 폴링 또는 실패(failed) 안내 화면.

    pending/running 은 report-poll.js 가 status 폴링 -> 완료 시 reload. failed 는 재발행 안내(폴링 안 함).
    환경·selection·단일 GET 공용 — 모든 보고서 scope 가 같은 비동기 생성 흐름이라 진행 화면 단일.
    """
    return templates.TemplateResponse(
        request=request,
        name="reports/report_pending.html",
        context={
            "job_id": rec.id,
            "status": rec.status,
            "error": rec.error_message if rec.status == "failed" else None,
            "back_url": back_url,
        },
    )


async def _render_environment_snapshot(
    request: Request,
    job_id: str,
    back_url: str,
    self_back: str,
    diag_service: DiagnosticService,
):
    """발행된 환경 보고서 렌더 — succeeded 면 정적 스냅샷, 그 외(pending/running/failed)는 진행 화면."""
    rec = await diag_service.get_report_snapshot(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="report snapshot not found")
    if rec.status != "succeeded":
        return _render_job_progress(request, rec, back_url)
    if rec.result is None or rec.result.get("kind") != REPORT_KIND_ENV:
        raise HTTPException(status_code=404, detail="report snapshot not found")
    result = rec.result
    summary = env_report_from_dict(result["snapshot"])
    view = result.get("view", "engineer")
    return templates.TemplateResponse(
        request=request,
        name="reports/environment.html",
        context={
            "active_nav": "environment",
            "summary": summary,
            "view": view,
            "view_title": _VIEW_TITLES.get(view, view),
            "back_url": back_url,
            "self_back": self_back,
            "report_job_id": rec.id,
            "time_range": rec.input_params.get("time_range", DIAGNOSTIC_DEFAULT_TIME_RANGE),
        },
    )


@reports_router.post("/environment/emit")
async def environment_report_emit(
    time_range: DiagnosticTimeRange = Query(DIAGNOSTIC_DEFAULT_TIME_RANGE),
    anchor_at: datetime | None = Query(None),
    view: Literal["customer", "engineer"] = Query("customer"),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """환경 보고서 발행 (PRG) — parent job enqueue 후 즉시 `?job={id}` 반환(워커가 비동기 생성).

    응답 view_url = `?job={id}` — 클라이언트가 navigate -> 생성 중이면 진행 화면 + 폴링, 완료 시 스냅샷.
    같은 input 활성 충돌(더블클릭) 시 기존 job_id 회수(enqueue_report). 등록 서버 0 등 생성 불가는
    워커가 job 을 failed 로 전이 -> GET 이 실패 화면 표시.
    """
    anchor = _normalize_anchor(anchor_at)
    job_id = await diag_service.enqueue_report(
        view=view,
        scope="environment",
        server_public_ids=[],
        time_range=time_range,
        anchor_at=anchor,
    )
    return {"view_url": f"/reports/environment?job={job_id}"}


_HISTORY_PAGE_LIMIT = 20


@reports_router.get("/history")
async def history(
    request: Request,
    days: int = Query(90, ge=0, le=36500, description="최근 N일 (0 = 전체)"),
    view: Literal["all", "customer", "engineer"] = Query(
        "all",
        description="양식 필터: 전체 / 고객 / 엔지니어",
    ),
    scope: Literal["all", "environment", "selection", "single"] = Query(
        "all",
        description="범위 필터: 전체 / 환경 / 선택 N대 / 서버 1대",
    ),
    server_public_ids: list[str] | None = Query(
        None,
        description="특정 서버 관련 보고서만 필터. 서버 목록 'N대 선택 + 보고서 이력' 진입 시 자동 채워짐.",
    ),
    limit: int = Query(_HISTORY_PAGE_LIMIT, ge=1, le=10000, description="표시 한도 — '더보기' 클릭 시 누적(20씩)"),
    fragment: bool = Query(False, description="HTML partial 만 반환 — JS 즉시 filter (full page reload 회피)"),
    back: str | None = Query(None, description="← 이전 link referrer"),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """보고서 발행 이력 — 운영자 회고용. created_at DESC. 기본 20건 + "더보기"(limit 누적).

    fragment=True: 결과 partial 만 반환 — JS 가 filter 변경·더보기 시 즉시 fetch + DOM 교체 (서버 목록과 동일 UX).
    """
    records, total = await diag_service.list_reports(
        days,
        view,
        server_public_ids,
        limit=limit,
        scope=None if scope == "all" else scope,
    )
    # 본 이력 페이지 URL — 진입한 보고서의 "이전" 버튼이 돌아올 위치 (back chain).
    history_back = quote(f"{request.url.path}?{request.url.query}", safe="")
    items = [to_report_history_item(r, history_back) for r in records]
    back_url = back if back and back.startswith("/") and not back.startswith("//") else "/"
    context = {
        "active_nav": "history",
        "items": items,
        "items_count": len(items),  # P3 정공 — template 안 `length` 계산 회피, server precompute.
        "total": total,
        "has_more": len(items) < total,
        "page_limit": _HISTORY_PAGE_LIMIT,
        "days": days,
        "view": view,
        "scope": scope,
        "server_public_ids": server_public_ids,
        "back_url": back_url,
    }
    template = "reports/_history_results.html" if fragment else "reports/history.html"
    return templates.TemplateResponse(request=request, name=template, context=context)


@reports_router.get("/{job_id}/status")
async def report_status(
    job_id: str,
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """비동기 보고서 생성 진행 상태 — report-poll.js 폴링용 JSON. 미존재 404.

    failed 의 error 는 도메인 사유만(워커가 raw 예외는 'internal error' 로 sanitize, F8).
    정적 라우트(/environment·/history·/servers·*/emit)와 segment 구조가 달라 충돌 없음.
    """
    rec = await diag_service.get_report_snapshot(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "status": rec.status,
        "progress_stage": rec.progress_stage,
        "error": rec.error_message if rec.status == "failed" else None,
    }


@reference_router.get("/reference")
async def right_sizing_thresholds(
    request: Request,
    back: str | None = Query(None, description="← 이전 link referrer. 미명시 시 / (환경 개요)"),
):
    """Right-sizing 분류 임계값 reference 페이지 — 환경 엔지니어 보고서에서 link 로 분리.

    `recommendation` 모듈 단일 진실의 분류·USE 축·입력·임계·근거 표 + 출처 설명.
    보고서·진단 양쪽이 참조하는 reference 자료 — 본 페이지에서만 한 번 정의 (T13).
    """
    back_url = back if back and back.startswith("/") and not back.startswith("//") else "/"
    return templates.TemplateResponse(
        request=request,
        name="reports/right_sizing_thresholds.html",
        context={
            "active_nav": "thresholds",
            "back_url": back_url,
            "service_badges": build_service_badge_reference(),
        },
    )


# total 은 list_reports 가 필터 후 전체 건수로 반환 — retention 90일 가정이라 COUNT 비용 수용 (E2 일반 정책 예외).
