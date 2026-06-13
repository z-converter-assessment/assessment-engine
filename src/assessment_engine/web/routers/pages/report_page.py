"""서버 보고서 SSR — 선택 N대 (`/servers/report`) + 단일 1대 (`/servers/{id}/report`).

server scope 보고서. 환경 단위 high-level 보고서는 `/reports/environment` 별도 endpoint (T13).

발행/표시 분리 (PRG):
- POST `/servers/report/emit` (ids 1개=단일 양식, 2개+=N대 표 양식) — 발행 시점 정적 스냅샷 + (engineer) narrative
  worker 발행. 응답 view_url = `?job={id}` (단일은 `/servers/{pid}/report?job=`).
- GET `?job={id}` — 저장된 정적 스냅샷 렌더 (재계산·재진단 없음, 이력 동적변화 0).
- GET (job 없음) — live read-only preview. 진단 트리거 없음 (engineer narrative 영역은 "발행 시 생성" 표시).
"""

from datetime import datetime
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from assessment_engine.db.repositories.base_diagnostic_repository import (
    DIAGNOSTIC_DEFAULT_TIME_RANGE,
    DiagnosticTimeRange,
)
from assessment_engine.web.deps import get_diagnostic_service, get_service
from assessment_engine.web.services.diagnostic_service import DiagnosticService, _normalize_anchor
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.services.report_serializer import (
    REPORT_KIND_ENV,
    env_report_from_dict,
    env_report_to_dict,
)
from assessment_engine.web.templating import templates
from assessment_engine.web.view_models.attention import AttentionSignals

# 단일 보고서는 서버 단위(/servers/{id}/report), N대 선택 보고서는 보고서 그룹(/reports/servers) — URL 명사 분리.
report_single_router = APIRouter(prefix="/servers")
report_multi_router = APIRouter(prefix="/reports")

_REPORT_VIEW_TITLES: dict[str, str] = {
    "customer": "고객 제출용",
    "engineer": "엔지니어 검토용",
}


def _attention_for_host(hostname: str, attention: AttentionSignals) -> dict[str, str]:
    """1대 호스트 운영 신호 (gap/os_eol/restart) lookup — 발행 시 aux 캡처 + live preview 공용."""
    out: dict[str, str] = {}
    for row in attention.gap_warnings:
        if row.link_text == hostname:
            out["gap"] = row.badge_text
    for row in attention.os_eol_warnings:
        if row.link_text == hostname:
            out["os_eol"] = row.meta_text
    for row in attention.agent_unstable:
        if row.link_text == hostname:
            out["restart"] = row.badge_text
    return out


def _attention_by_host(hostnames: set[str], attention: AttentionSignals) -> dict[str, dict[str, str]]:
    """선택 N대 운영 신호 lookup dict {hostname: {gap, os_eol, restart}} — 발행 aux + live preview 공용."""
    by_host: dict[str, dict[str, str]] = {h: {} for h in hostnames}
    for row in attention.gap_warnings:
        if row.link_text in by_host:
            by_host[row.link_text]["gap"] = row.badge_text
    for row in attention.os_eol_warnings:
        if row.link_text in by_host:
            by_host[row.link_text]["os_eol"] = row.meta_text
    for row in attention.agent_unstable:
        if row.link_text in by_host:
            by_host[row.link_text]["restart"] = row.badge_text
    return by_host


@report_multi_router.get("/servers")
async def report(
    request: Request,
    ids: str | None = Query(None, description="comma-separated public_id 목록 (live preview). job 모드 시 무시"),
    job: str | None = Query(None, description="발행된 보고서 job_id — 정적 스냅샷 렌더"),
    time_range: DiagnosticTimeRange = Query(
        DIAGNOSTIC_DEFAULT_TIME_RANGE, description="윈도우 (live preview). job 모드 시 input_params 사용"
    ),
    view: Literal["customer", "engineer"] = Query("customer", description="고객용(A) / 엔지니어용(B) (live preview)"),
    back: str | None = Query(None, description="← 이전 link 의 referrer. 미명시 시 /servers/"),
    service: QueryService = Depends(get_service),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """Server scope N대 보고서 — job 있으면 정적 스냅샷, 없으면 live read-only preview."""
    back_url = back if back and back.startswith("/") and not back.startswith("//") else "/"
    self_back = quote(f"{request.url.path}?{request.url.query}", safe="")

    if job:
        return await _render_summary_snapshot(request, job, back_url, self_back, diag_service)

    # live read-only preview — 진단 트리거 없음 (engineer narrative 영역은 "발행 시 생성").
    public_ids = [pid.strip() for pid in (ids or "").split(",") if pid.strip()]
    summary = await service.get_selection_report(public_ids, view=view, time_range=time_range)
    if summary is None:
        raise HTTPException(status_code=404, detail="no valid server ids")
    attention = await service.get_attention_signals(limit_each=None)
    attention_by_host = _attention_by_host({r.hostname for r in summary.base.rows}, attention)
    return templates.TemplateResponse(
        request=request,
        name="servers/report.html",
        context={
            "summary": summary,
            "view": view,
            "view_title": _REPORT_VIEW_TITLES[view],
            "env_narrative": None,
            "narrative_status": "none",
            "narrative_key": None,  # selection 자체엔 AI 진단 없음 (개별 보고서 child 가 per-pid narrative 표시)
            "report_job_id": None,
            "child_jobs": {},
            "back_url": back_url,
            "attention_by_host": attention_by_host,
            "self_back": self_back,
            "time_range": time_range,
        },
    )


async def _render_summary_snapshot(
    request: Request,
    job_id: str,
    back_url: str,
    self_back: str,
    diag_service: DiagnosticService,
):
    """발행된 N대 selection 보고서 정적 스냅샷 렌더 (EnvironmentReportSummary) + narrative/운영신호 (aux)."""
    rec = await diag_service.get_one(job_id)
    if rec is None or rec.result is None or rec.result.get("kind") != REPORT_KIND_ENV:
        raise HTTPException(status_code=404, detail="report snapshot not found")
    result = rec.result
    summary = env_report_from_dict(result["snapshot"])
    view = result.get("view", "engineer")
    time_range = rec.input_params.get("time_range", DIAGNOSTIC_DEFAULT_TIME_RANGE)
    attention_by_host = result.get("aux", {}).get("attention_by_host", {})
    return templates.TemplateResponse(
        request=request,
        name="servers/report.html",
        context={
            "summary": summary,
            "view": view,
            "view_title": _REPORT_VIEW_TITLES.get(view, view),
            "env_narrative": None,
            "narrative_status": result.get("narrative_status", "none"),
            "narrative_key": None,  # selection 자체엔 AI 진단 없음 (개별 보고서 child 가 per-pid narrative 표시)
            "report_job_id": rec.id,
            "child_jobs": result.get("child_jobs", {}),
            "back_url": back_url,
            "attention_by_host": attention_by_host,
            "self_back": self_back,
            "time_range": time_range,
        },
    )


@report_multi_router.post("/servers/emit")
async def report_emit(
    ids: str = Query(..., description="comma-separated public_id 목록 (1개=단일 양식, 2개+=N대 표)"),
    time_range: DiagnosticTimeRange = Query(DIAGNOSTIC_DEFAULT_TIME_RANGE),
    view: Literal["customer", "engineer"] = Query("customer"),
    anchor_at: str | None = Query(None, description="발행 기준 시각 (ISO 8601). 미명시 시 발행 시점"),
    service: QueryService = Depends(get_service),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """Server scope 보고서 발행 (PRG) — 발행 시점 정적 스냅샷 + (engineer) narrative worker 발행.

    응답 view_url = `?job={id}` — 클라이언트가 navigate. ids 1개면 단일 양식(`/servers/{pid}/report?job=`),
    2개+ 면 N대 표 양식(`/servers/report?job=`). engineer 면 worker 가 narrative 채운 뒤 polling 갱신.
    """
    public_ids = [pid.strip() for pid in ids.split(",") if pid.strip()]
    sid_map = await service.resolve_server_ids(public_ids)
    valid_pids = [pid for pid in public_ids if pid in sid_map]
    if not valid_pids:
        raise HTTPException(status_code=404, detail="no valid server ids")
    anchor = _normalize_anchor(datetime.fromisoformat(anchor_at) if anchor_at else None)
    attention = await service.get_attention_signals(limit_each=None)

    if len(valid_pids) == 1:
        single_job = await _emit_single_report(
            service, diag_service, valid_pids[0], view, time_range, anchor, attention
        )
        if single_job is None:
            raise HTTPException(status_code=404, detail="server not found")
        return {"view_url": f"/servers/{valid_pids[0]}/report?job={single_job}"}

    # 개별 단일 보고서 먼저 발행 (이력 개별 조회). engineer 는 publish=False child — narrative 는
    # 부모 표 worker 가 server 별로 1회 생성한 값을 복사(단일 생성·표·개별 항상 일관). customer 는 즉시 succeeded.
    child_jobs: dict[str, str] = {}
    for pid in valid_pids:
        cid = await _emit_single_report(
            service, diag_service, pid, view, time_range, anchor, attention, publish=False
        )
        if cid:
            child_jobs[pid] = cid

    # N대 selection 보고서 (환경 보고서 양식, kind=ENV) — engineer 면 child_jobs 전달 (세부 목록 정적 link).
    summary = await service.get_selection_report(
        valid_pids, view=view, time_range=time_range, anchor_at=anchor
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="no valid server ids")
    table_job = await diag_service.emit_report(
        view=view,
        scope="server",
        kind=REPORT_KIND_ENV,
        snapshot=env_report_to_dict(summary),
        server_public_ids=valid_pids,
        time_range=time_range,
        anchor_at=anchor,
        aux={"attention_by_host": _attention_by_host({r.hostname for r in summary.base.rows}, attention)},
        # 양 view 모두 child_jobs 저장 — 세부 서버 목록 hostname -> 개별 보고서(child) 정적 link.
        child_jobs=child_jobs,
    )
    return {"view_url": f"/reports/servers?job={table_job}"}


async def _emit_single_report(
    service: QueryService,
    diag_service: DiagnosticService,
    pid: str,
    view: str,
    time_range: str,
    anchor: datetime,
    attention: AttentionSignals,
    publish: bool = True,
) -> str | None:
    """단일 서버 보고서 발행 (kind=ENV) — 단독 emit(publish=True) + N대 fan-out child(publish=False) 공용.

    publish=False (engineer child): pending 저장만, worker publish 생략 — 부모 표 worker 가 narrative 복사.
    미존재 시 None.
    """
    summary = await service.get_single_server_report(
        pid, view=view, time_range=time_range, anchor_at=anchor
    )
    if summary is None:
        return None
    hostname = summary.base.rows[0].hostname if summary.base.rows else pid
    return await diag_service.emit_report(
        view=view,
        scope="server",
        kind=REPORT_KIND_ENV,
        snapshot=env_report_to_dict(summary),
        server_public_ids=[pid],
        time_range=time_range,
        anchor_at=anchor,
        aux={"attention_for_host": _attention_for_host(hostname, attention)},
        publish=publish,
    )


@report_single_router.get("/{server_id}/report")
async def single_server_report(
    request: Request,
    server_id: UUID,
    job: str | None = Query(None, description="발행된 보고서 job_id — 정적 스냅샷 렌더"),
    time_range: DiagnosticTimeRange = Query(DIAGNOSTIC_DEFAULT_TIME_RANGE, description="윈도우 (live preview)"),
    view: Literal["customer", "engineer"] = Query("customer"),
    back: str | None = Query(None),
    service: QueryService = Depends(get_service),
    diag_service: DiagnosticService = Depends(get_diagnostic_service),
):
    """단일 서버 보고서 — job 있으면 정적 스냅샷, 없으면 live read-only preview (단순화 양식, T13)."""
    self_back = quote(f"{request.url.path}?{request.url.query}", safe="")
    back_url = back if back and back.startswith("/") and not back.startswith("//") else f"/servers/{server_id}"

    if job:
        return await _render_single_snapshot(request, job, back_url, self_back, diag_service)

    summary = await service.get_single_server_report(
        str(server_id), view=view, time_range=time_range
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="server not found")
    hostname = summary.base.rows[0].hostname if summary.base.rows else str(server_id)
    attention = await service.get_attention_signals(limit_each=None)
    return templates.TemplateResponse(
        request=request,
        name="servers/single_report.html",
        context={
            "summary": summary,
            "view": view,
            "view_title": _REPORT_VIEW_TITLES[view],
            "back_url": back_url,
            "hostname": hostname,
            "narratives": {},
            "narrative_status": "none",
            "report_job_id": None,
            "attention_for_host": _attention_for_host(hostname, attention),
            "self_back": self_back,
            "time_range": time_range,
        },
    )


async def _render_single_snapshot(
    request: Request,
    job_id: str,
    back_url: str,
    self_back: str,
    diag_service: DiagnosticService,
):
    """발행된 단일 서버 보고서 정적 스냅샷 렌더 (EnvironmentReportSummary) + narrative/운영신호 (aux)."""
    rec = await diag_service.get_one(job_id)
    if rec is None or rec.result is None or rec.result.get("kind") != REPORT_KIND_ENV:
        raise HTTPException(status_code=404, detail="report snapshot not found")
    result = rec.result
    summary = env_report_from_dict(result["snapshot"])
    view = result.get("view", "engineer")
    hostname = summary.base.rows[0].hostname if summary.base.rows else "server"
    return templates.TemplateResponse(
        request=request,
        name="servers/single_report.html",
        context={
            "summary": summary,
            "view": view,
            "view_title": _REPORT_VIEW_TITLES.get(view, view),
            "back_url": back_url,
            "hostname": hostname,
            "narratives": result.get("narratives", {}),
            "narrative_status": result.get("narrative_status", "none"),
            "report_job_id": rec.id,
            "attention_for_host": result.get("aux", {}).get("attention_for_host", {}),
            "self_back": self_back,
            "time_range": rec.input_params.get("time_range", DIAGNOSTIC_DEFAULT_TIME_RANGE),
        },
    )
