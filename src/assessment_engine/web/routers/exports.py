"""Exports router — assessment 계약을 다운로드 파일로.

GET /api/assessment 와 완전 동일한 데이터/envelope 를 다운로드 파일로 전달한다(내용 동일, 전달만 파일).
운영자가 파일로 저장해 오프라인 Terraform/Ansible 등에 투입하는 용도. 필터도 GET 과 동일
(hostname/ip/public_id/pair/window_days/end). 파일 최상위 객체는 GET 응답(계약 4.1)과 구조가 동일하다(계약 8절).
"""

import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from assessment_engine.web.deps import get_service
from assessment_engine.web.services.mappers.assessment_api import build_assessment_envelope
from assessment_engine.web.services.query_service import QueryService

exports_router = APIRouter(prefix="/api/exports", tags=["exports"])

# 계약 3절 — 14 미만은 관측 부족으로 과소 사이징. 연장(14 초과)만 안전 방향(GET /api/assessment 와 동일).
_WINDOW_FLOOR_DAYS = 14


class AssessmentExportRequest(BaseModel):
    """export 필터 — GET /api/assessment 쿼리와 동일 필드(쉼표 문자열 대신 배열). 미지정 시 전체 서버."""

    hostname: list[str] = Field(default_factory=list[str])
    ip: list[str] = Field(default_factory=list[str])
    public_id: list[str] = Field(default_factory=list[str])
    pair: list[str] = Field(default_factory=list[str])  # "hostname~discriminator" 토큰
    window_days: int = Field(default=_WINDOW_FLOOR_DAYS, ge=_WINDOW_FLOOR_DAYS, le=90)
    end: datetime | None = None


def _split_pairs(tokens: list[str]) -> list[tuple[str, str]]:
    """순서쌍 파싱 — "hostname~discriminator". discriminator=IP 또는 public_id. 형식 불량 무시."""
    out: list[tuple[str, str]] = []
    for token in tokens:
        if "~" in token:
            host, _, disc = token.partition("~")
            host, disc = host.strip(), disc.strip()
            if host and disc:
                out.append((host, disc))
    return out


@exports_router.post("/inventory")
async def export_inventory(
    req: AssessmentExportRequest,
    service: QueryService = Depends(get_service),
):
    """assessment 계약을 다운로드 JSON 파일로 반환 — GET /api/assessment 와 데이터 동일, 전달만 파일.

    파일 최상위 객체는 GET 응답(계약 4.1)과 구조 동일(같은 contract_version/필드). 운영자가 저장해 오프라인
    자동화(Terraform/Ansible)에 투입한다. 필터는 GET 과 동일(hostname/ip/public_id/pair/window_days/end),
    미지정 시 전체 서버. 사이징/재현 산식은 GET 과 동일 단일 진실(get_assessment).
    """
    anchor = req.end or datetime.now(UTC)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    hostnames = [h.strip() for h in req.hostname if h.strip()]
    ips = [i.strip() for i in req.ip if i.strip()]
    public_ids = [p.strip() for p in req.public_id if p.strip()]
    pairs = _split_pairs(req.pair)
    result = await service.get_assessment(
        window_days=req.window_days,
        end=anchor,
        hostnames=hostnames,
        ips=ips,
        public_ids=public_ids,
        pairs=pairs,
    )
    now = datetime.now(UTC)
    envelope = build_assessment_envelope(
        result,
        generated_at=now.isoformat(),
        window_days=req.window_days,
        window_start=(anchor - timedelta(days=req.window_days)).isoformat(),
        window_end=anchor.isoformat(),
        filters={
            "hostname": hostnames,
            "ip": ips,
            "public_id": public_ids,
            "pair": [f"{h}~{d}" for h, d in pairs],
        },
    )
    filename = f"assessment-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    return Response(
        content=json.dumps(envelope, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
