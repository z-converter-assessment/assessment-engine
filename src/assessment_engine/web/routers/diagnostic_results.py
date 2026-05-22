"""진단 결과 페이지 (SSR) — N개 발행된 진단 job의 결과·진행상황 한 화면에서 표시.

흐름: 진단 발행(라우터·모달 등) → 응답 job_ids → `/diagnostics?ids=j1,j2,j3`로 이동.
초기 SSR로 가능한 결과(succeeded)는 즉시 렌더, pending/running 카드는 JS polling 진행.

라우터는 pages_router(prefix=`/servers`)와 별개 — `/diagnostics`로 독립.
"""

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from assessment_engine.web.deps import get_diagnostic_service
from assessment_engine.web.services.diagnostic_service import DiagnosticService, to_panel_payload
from assessment_engine.web.services.mappers.diagnostic import to_history_item
from assessment_engine.web.templating import templates

diagnostic_results_router = APIRouter(prefix="/diagnostics", tags=["pages"])

_MAX_IDS_PER_PAGE = 100
# 진단 이력 — 보고서 이력과 같은 패턴 (단일 진실, static-assets.md "이력 페이지 규약" 절).
# 두 이력 (`/reports/history` / `/diagnostics/history`) 모두 동일한 _HISTORY_PAGE_LIMIT / _HISTORY_FULL_LIMIT 값.
_HISTORY_PAGE_LIMIT = 20
_HISTORY_FULL_LIMIT = 10000


def _safe_back(back: str | None, fallback: str) -> str:
    if back and back.startswith("/") and not back.startswith("//"):
        return back
    return fallback


def _self_back(request: Request) -> str:
    return quote(f"{request.url.path}?{request.url.query}", safe="")


@diagnostic_results_router.get("")
async def show_results(
    request: Request,
    ids: str = Query(..., description="comma-separated job ids"),
    back: str | None = Query(None, description="← 이전 link referrer"),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    job_ids = [s.strip() for s in ids.split(",") if s.strip()]
    if not job_ids:
        raise HTTPException(status_code=400, detail="ids required")
    if len(job_ids) > _MAX_IDS_PER_PAGE:
        raise HTTPException(status_code=400, detail=f"max {_MAX_IDS_PER_PAGE} ids per page")

    records = await diag_service.get_many(job_ids)
    # 입력 순서 보존 — DB return 순서는 임의
    by_id = {r.id: r for r in records}
    ordered = [by_id.get(jid) for jid in job_ids]

    # 환경 scope job 마다 환경 보고서 URL 미리 합성 (iframe 으로 같은 페이지에 toggle).
    # /reports/environment 는 전체 등록 서버 자동. 진단 job 의 time_range·anchor_at 그대로 전달
    # 해 같은 윈도우·기준 시각 보고서 합성 — 진단 결과와 시간 정합.
    self_back = _self_back(request)
    jobs: list[dict] = []
    for jid, rec in zip(job_ids, ordered, strict=True):
        job: dict = {"job_id": jid, "payload": to_panel_payload(rec)}
        if rec is not None and rec.scope == "environment":
            time_range = rec.input_params.get("time_range", "14d")
            anchor_at = rec.input_params.get("anchor_at")
            parts = [f"time_range={time_range}"]
            if anchor_at:
                parts.append(f"anchor_at={anchor_at}")
            qs = "&".join(parts)
            job["report_link_customer"] = f"/reports/environment?{qs}&view=customer&back={self_back}"
            job["report_link_engineer"] = f"/reports/environment?{qs}&view=engineer&back={self_back}"
        jobs.append(job)

    return templates.TemplateResponse(
        request=request,
        name="diagnostics/results.html",
        context={
            "jobs": jobs,
            "back_url": _safe_back(back, "/servers/"),
            "self_back": self_back,
        },
    )


@diagnostic_results_router.get("/history")
async def history(
    request: Request,
    days: int = Query(90, ge=0, le=36500, description="최근 N일 (0 = 전체, retention 미적용 시 무제한 누적 가능)"),
    scope: Literal["all", "server", "environment"] = Query("all"),
    server_public_ids: list[str] | None = Query(
        None,
        description=(
            "server scope 이력을 특정 서버들로 필터 (반복 query param 또는 단일). 1대=단일 link, 다중=multi-select 진입"
        ),
    ),
    full: bool = Query(False, description="전체 보기 (기본 20건 → 전체)"),
    back: str | None = Query(None, description="← 이전 link referrer"),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """AI 진단 발행 이력 — 운영자 회고용. created_at DESC, 최근 N일.

    보고서 (customer/engineer) 는 별도 페이지 `/reports/history` 에서 관리 (T13).
    server_public_ids 지정 시 input_params JSONB ANY 매칭으로 해당 서버들 진단만 노출.
    full=False (default): 최근 20건. full=True: 모든 row 한 번에 SSR — 보고서 이력과 같은 패턴.
    """
    scope_filter = None if scope == "all" else scope
    limit = _HISTORY_FULL_LIMIT if full else _HISTORY_PAGE_LIMIT
    records = await diag_service.list_recent(
        days,
        scope_filter,
        server_public_ids,
        job_type="ai_diagnostic",
        limit=limit,
    )
    items = [to_history_item(r) for r in records]
    return templates.TemplateResponse(
        request=request,
        name="diagnostics/history.html",
        context={
            "items": items,
            "days": days,
            "scope": scope,
            "server_public_ids": server_public_ids,
            "full": full,
            "show_all_link": (not full) and len(records) == _HISTORY_PAGE_LIMIT,
            "back_url": _safe_back(back, "/servers/"),
            "self_back": _self_back(request),
        },
    )
