"""서버 보고서 SSR — 선택 N대 (`/reports/servers`) + 단일 1대 (`/servers/{id}/report`).

server scope 보고서. 환경 단위 high-level 보고서는 `/reports/environment` 별도 endpoint (T13).

발행/표시 분리 (PRG):
- POST `/reports/servers/emit` (ids 1개=단일 양식, 2개+=N대 표 양식) — 발행 시점 정적 스냅샷.
  응답 view_url = `?job={id}` (단일은 `/servers/{pid}/report?job=`).
- GET `?job={id}` — 저장된 정적 스냅샷 렌더 (재계산·재진단 없음, 이력 동적변화 0).
- GET (job 없음) — live read-only preview. 진단 트리거 없음.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from assessment_engine.db.repositories.query.types import (
    DIAGNOSTIC_DEFAULT_TIME_RANGE,
    TimeRange,
)
from assessment_engine.web.deps import DiagnosticServiceDep, QueryServiceDep
from assessment_engine.web.routers._back import BackUrl, safe_back, self_back
from assessment_engine.web.routers._report_snapshot import (
    VIEW_TITLES,
    LoadedSnapshot,
    load_snapshot,
    render_job_progress,
)
from assessment_engine.web.services.diagnostic_service import DiagnosticService
from assessment_engine.web.services.report import attention_by_host, attention_for_host, normalize_anchor
from assessment_engine.web.templating import templates

# 단일 보고서는 서버 단위(/servers/{id}/report), N대 선택 보고서는 보고서 그룹(/reports/servers) — URL 명사 분리.
report_single_router = APIRouter(prefix="/servers")
report_multi_router = APIRouter(prefix="/reports")


@report_multi_router.get("/servers")
async def report(
    request: Request,
    service: QueryServiceDep,
    diag_service: DiagnosticServiceDep,
    ids: Annotated[
        str | None, Query(description="comma-separated public_id 목록 (live preview). job 모드 시 무시")
    ] = None,
    job: Annotated[str | None, Query(description="발행된 보고서 job_id — 정적 스냅샷 렌더")] = None,
    time_range: Annotated[
        TimeRange, Query(description="윈도우 (live preview). job 모드 시 input_params 사용")
    ] = DIAGNOSTIC_DEFAULT_TIME_RANGE,
    view: Annotated[
        Literal["customer", "engineer"], Query(description="고객용(A) / 엔지니어용(B) (live preview)")
    ] = "customer",
    back: BackUrl = None,
):
    """Server scope N대 보고서 — job 있으면 정적 스냅샷, 없으면 live read-only preview."""
    back_url = safe_back(back, "/")
    self_back_url = self_back(request)

    if job:
        return await _render_summary_snapshot(request, job, back_url, self_back_url, diag_service)

    # live read-only preview — 진단 트리거 없음.
    public_ids = [pid.strip() for pid in (ids or "").split(",") if pid.strip()]
    summary = await service.get_selection_report(public_ids, view=view, time_range=time_range)
    if summary is None:
        raise HTTPException(status_code=404, detail="no valid server ids")
    attention = await service.get_attention_signals(limit_each=None)
    by_host = attention_by_host({r.hostname for r in summary.base.rows}, attention)
    return templates.TemplateResponse(
        request=request,
        name="servers/report.html",
        context={
            "summary": summary,
            "view": view,
            "view_title": VIEW_TITLES[view],
            "report_job_id": None,
            "child_jobs": {},
            "back_url": back_url,
            "attention_by_host": by_host,
            "self_back": self_back_url,
            "time_range": time_range,
        },
    )


async def _render_summary_snapshot(
    request: Request,
    job_id: str,
    back_url: str,
    self_back_url: str,
    diag_service: DiagnosticService,
):
    """발행된 N대 selection 보고서 정적 스냅샷 렌더 (EnvironmentReportSummary) + 운영신호 (aux)."""
    loaded = await load_snapshot(job_id, diag_service)
    if not isinstance(loaded, LoadedSnapshot):
        return render_job_progress(request, loaded, back_url)
    return templates.TemplateResponse(
        request=request,
        name="servers/report.html",
        context={
            "summary": loaded.summary,
            "view": loaded.view,
            "view_title": loaded.view_title,
            "report_job_id": loaded.record.id,
            "child_jobs": loaded.result.get("child_jobs", {}),
            "back_url": back_url,
            "attention_by_host": loaded.aux("attention_by_host"),
            "self_back": self_back_url,
            "time_range": loaded.time_range,
        },
    )


@report_multi_router.post("/servers/emit")
async def report_emit(
    diag_service: DiagnosticServiceDep,
    ids: Annotated[str, Query(description="comma-separated public_id 목록 (1개=단일 양식, 2개+=N대 표)")],
    time_range: TimeRange = DIAGNOSTIC_DEFAULT_TIME_RANGE,
    view: Literal["customer", "engineer"] = "customer",
    anchor_at: Annotated[str | None, Query(description="발행 기준 시각 (ISO 8601). 미명시 시 발행 시점")] = None,
):
    """Server scope 보고서 발행 (PRG) — parent job enqueue 후 즉시 `?job={id}` 반환(워커가 비동기 생성).

    ids 1개=단일 양식(`/servers/{pid}/report?job=`), 2개+=N대 표(`/reports/servers?job=`). 워커가
    child 단일 보고서 N건 + selection 본문을 단일 단위로 생성(부분 누락 차단). 유효 id 0 은 워커가
    job 을 failed 로 전이 -> GET 이 실패 화면 표시. 같은 input 더블클릭은 기존 job 으로 합류(멱등).
    """
    public_ids = [pid.strip() for pid in ids.split(",") if pid.strip()]
    if not public_ids:
        raise HTTPException(status_code=404, detail="no server ids")
    anchor = normalize_anchor(datetime.fromisoformat(anchor_at) if anchor_at else None)
    job_id = await diag_service.enqueue_report(
        view=view,
        scope="server",
        server_public_ids=public_ids,
        time_range=time_range,
        anchor_at=anchor,
    )
    # 양식 분기는 ids 개수로 사전 결정 (생성 전이라도 URL 명사는 확정 — child 양식 vs N대 표 양식).
    if len(public_ids) == 1:
        return {"view_url": f"/servers/{public_ids[0]}/report?job={job_id}"}
    return {"view_url": f"/reports/servers?job={job_id}"}


@report_single_router.get("/{server_id}/report")
async def single_server_report(
    request: Request,
    server_id: UUID,
    service: QueryServiceDep,
    diag_service: DiagnosticServiceDep,
    job: Annotated[str | None, Query(description="발행된 보고서 job_id — 정적 스냅샷 렌더")] = None,
    time_range: Annotated[TimeRange, Query(description="윈도우 (live preview)")] = DIAGNOSTIC_DEFAULT_TIME_RANGE,
    view: Literal["customer", "engineer"] = "customer",
    back: BackUrl = None,
):
    """단일 서버 보고서 — job 있으면 정적 스냅샷, 없으면 live read-only preview (단순화 양식, T13)."""
    self_back_url = self_back(request)
    back_url = safe_back(back, f"/servers/{server_id}")

    if job:
        return await _render_single_snapshot(request, job, back_url, self_back_url, diag_service)

    # 본문·aux 가 동일 attention 공유 — preview 는 anchor 없음(현재 시각). single 내부 재계산 회피.
    attention = await service.get_attention_signals(limit_each=None)
    summary = await service.get_single_server_report(
        str(server_id), view=view, time_range=time_range, attention=attention
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="server not found")
    hostname = summary.base.rows[0].hostname if summary.base.rows else str(server_id)
    return templates.TemplateResponse(
        request=request,
        name="servers/single_report.html",
        context={
            "summary": summary,
            "view": view,
            "view_title": VIEW_TITLES[view],
            "back_url": back_url,
            "hostname": hostname,
            "report_job_id": None,
            "attention_for_host": attention_for_host(hostname, attention),
            "self_back": self_back_url,
            "time_range": time_range,
        },
    )


async def _render_single_snapshot(
    request: Request,
    job_id: str,
    back_url: str,
    self_back_url: str,
    diag_service: DiagnosticService,
):
    """발행된 단일 서버 보고서 정적 스냅샷 렌더 (EnvironmentReportSummary) + 운영신호 (aux)."""
    loaded = await load_snapshot(job_id, diag_service)
    if not isinstance(loaded, LoadedSnapshot):
        return render_job_progress(request, loaded, back_url)
    rows = loaded.summary.base.rows
    return templates.TemplateResponse(
        request=request,
        name="servers/single_report.html",
        context={
            "summary": loaded.summary,
            "view": loaded.view,
            "view_title": loaded.view_title,
            "back_url": back_url,
            "hostname": rows[0].hostname if rows else "server",
            "report_job_id": loaded.record.id,
            "attention_for_host": loaded.aux("attention_for_host"),
            "self_back": self_back_url,
            "time_range": loaded.time_range,
        },
    )
