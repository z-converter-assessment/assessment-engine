"""AI 진단 router — 사용자 트리거 진단 발행 + polling 응답 (ADR 0004).

책임: HTTP I/O만. 비즈니스 로직(INSERT·publish·트랜잭션)은 DiagnosticService 위임 (F4).
표시 파생(badge·라벨·window 라벨 등)은 `diagnostic_mapper.to_view`로 단일 변환 (P2).
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from assessment_engine.db.repositories.base_diagnostic_repository import DiagnosticTimeRange
from assessment_engine.web.deps import get_diagnostic_service
from assessment_engine.web.services.diagnostic_mapper import to_view
from assessment_engine.web.services.diagnostic_service import (
    DiagnosticService,
    _BadRequest,
    _NotFound,
    _RaceMiss,
)

diagnostics_router = APIRouter(prefix="/api/v1/diagnostics", tags=["diagnostics"])


class DiagnosticRequest(BaseModel):
    scope: Literal["server", "environment"]
    server_ids: list[UUID] | None = Field(default=None, max_length=100)
    time_range: DiagnosticTimeRange = "14d"
    anchor_at: datetime | None = None  # None → 서버에서 분 단위 truncate한 now()

    @model_validator(mode="after")
    def _validate_scope(self) -> "DiagnosticRequest":
        if self.scope == "server" and not self.server_ids:
            raise ValueError("server_ids required when scope='server'")
        return self


class JobIdsResponse(BaseModel):
    job_ids: list[str]


@diagnostics_router.post("", response_model=JobIdsResponse)
async def submit(
    req: DiagnosticRequest,
    service: DiagnosticService = Depends(get_diagnostic_service),
):
    public_ids = [str(u) for u in req.server_ids] if req.server_ids else None
    try:
        job_ids = await service.submit(
            req.scope, public_ids, req.time_range, req.anchor_at,
        )
        return JobIdsResponse(job_ids=job_ids)
    except _BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))
    except _NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except _RaceMiss as e:
        raise HTTPException(status_code=409, detail=str(e))


@diagnostics_router.get("")
async def list_status(
    ids: str = Query(..., description="comma-separated job ids (max 100)"),
    service: DiagnosticService = Depends(get_diagnostic_service),
):
    """JSON 응답 — 각 job의 view dict(`diagnostic_mapper.to_view().to_dict()`) 배열."""
    job_ids = [s.strip() for s in ids.split(",") if s.strip()]
    if not job_ids:
        raise HTTPException(status_code=400, detail="ids required")
    if len(job_ids) > 100:
        raise HTTPException(status_code=400, detail="max 100 ids per request")
    # UUID 형식 검증 — 라우터 단일 경로 (#F3)
    for j in job_ids:
        try:
            UUID(j)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid job_id format")

    records = await service.get_many(job_ids)
    return [to_view(r).to_dict() for r in records]


@diagnostics_router.get("/{job_id}")
async def get_one(
    job_id: UUID,
    service: DiagnosticService = Depends(get_diagnostic_service),
):
    rec = await service.get_one(str(job_id))
    if rec is None:
        raise HTTPException(status_code=404)
    return to_view(rec).to_dict()
