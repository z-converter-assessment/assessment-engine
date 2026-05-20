"""진단 결과 페이지 (SSR) — N개 발행된 진단 job의 결과·진행상황 한 화면에서 표시.

흐름: 진단 발행(라우터·모달 등) → 응답 job_ids → `/diagnostics?ids=j1,j2,j3`로 이동.
초기 SSR로 가능한 결과(succeeded)는 즉시 렌더, pending/running 카드는 JS polling 진행.

라우터는 pages_router(prefix=`/servers`)와 별개 — `/diagnostics`로 독립.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from assessment_engine.web.deps import get_diagnostic_service
from assessment_engine.web.services.diagnostic_service import DiagnosticService, to_panel_payload
from assessment_engine.web.services.mappers.diagnostic import to_history_item
from assessment_engine.web.templating import templates

diagnostic_results_router = APIRouter(prefix="/diagnostics", tags=["pages"])

_MAX_IDS_PER_PAGE = 100


@diagnostic_results_router.get("")
async def show_results(
    request: Request,
    ids: str = Query(..., description="comma-separated job ids"),
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
            job["report_link_customer"] = f"/reports/environment?{qs}&view=customer"
            job["report_link_engineer"] = f"/reports/environment?{qs}&view=engineer"
        jobs.append(job)

    return templates.TemplateResponse(
        request=request,
        name="diagnostics/results.html",
        context={"jobs": jobs},
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
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """AI 진단 발행 이력 — 운영자 회고용. created_at DESC, 최근 N일.

    보고서 (customer/engineer) 는 별도 페이지 `/reports/history` 에서 관리 (T13).
    server_public_ids 지정 시 input_params JSONB ANY 매칭으로 해당 서버들 진단만 노출.
    """
    scope_filter = None if scope == "all" else scope
    records = await diag_service.list_recent(
        days,
        scope_filter,
        server_public_ids,
        job_type="ai_diagnostic",
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
        },
    )
