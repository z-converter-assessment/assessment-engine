"""진단 narrative polling API (ADR 0004).

AI 진단 독립 발행은 engineer 보고서 발행 통합으로 폐기 — 본 라우터는 보고서 narrative polling 단건 조회만.
보고서 페이지 안 polling JS 가 발행된 job_id 의 narrative 진행/완료를 본 endpoint 로 확인.
표시 파생(badge·라벨)은 `diagnostic_mapper.to_view`로 단일 변환 (P2).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from assessment_engine.web.deps import get_diagnostic_service
from assessment_engine.web.services.diagnostic_service import DiagnosticService
from assessment_engine.web.services.mappers.diagnostic import to_view

diagnostics_router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@diagnostics_router.get("/{job_id}")
async def get_one(
    job_id: UUID,
    service: DiagnosticService = Depends(get_diagnostic_service),
):
    """단건 narrative polling — `result.narratives` + `result.narrative_status` + job status 반환."""
    rec = await service.get_one(str(job_id))
    if rec is None:
        raise HTTPException(status_code=404)
    return to_view(rec).to_dict()
