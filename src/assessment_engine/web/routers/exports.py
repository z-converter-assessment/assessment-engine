"""Exports router — 자동화 도구 입력용 정제 산출물 다운로드.

스키마·정제 원칙·사용처: docs/architecture/inventory-export.md.
v3: envelope 강화(engine_id·schema_doc·period_window) + I/O p95/peak + recommended_size_class 객체화
   + services.listeners (proto·address) — listen_ports inventory 매칭.
"""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from assessment_engine.web.deps import get_service
from assessment_engine.web.services.query_service import QueryService

exports_router = APIRouter(prefix="/api/v1/exports", tags=["exports"])

# recommended_size_class 객체화 — 자동화 도구가 key→자기 도메인 instance type 매핑할 때 참고용 가이드.
_SIZE_CLASS_GUIDE: dict[str, str] = {
    "under_provisioned": "instance type 상향 (vCPU·RAM 증가)",
    "over_provisioned":  "instance type 축소 (vCPU·RAM 감소)",
    "idle":              "운영 종료 또는 통합 검토 — 사용 거의 없음",
    "shutdown":          "운영 종료 검토 — 사용 0에 근접",
    "optimal":           "변경 불필요 — 적정 사양",
    "insufficient_data": "데이터 부족 — 평가 기간 안 메트릭 부재",
}


class InventoryExportRequest(BaseModel):
    target_public_ids: list[str] = Field(min_length=1, max_length=1000)
    period_days: int = Field(default=7, ge=1, le=30)


@exports_router.post("/inventory")
async def export_inventory(
    req: InventoryExportRequest,
    service: QueryService = Depends(get_service),
):
    """선택 서버 N대의 정제 inventory v3 JSON 반환.

    응답을 클라이언트가 파일로 저장 (브라우저 download). 서버에서 파일 생성 안 함 — stateless.
    envelope에 평가 기간 시작·종료 + size_class 매핑 가이드 포함 (reproducibility).
    """
    sid_map = await service.resolve_server_ids(req.target_public_ids)
    missing = [pid for pid in req.target_public_ids if pid not in sid_map]
    if missing:
        raise HTTPException(status_code=404, detail=f"server not found: {','.join(missing[:5])}")
    server_ids = [sid_map[pid] for pid in req.target_public_ids]

    now = datetime.now(UTC)
    period_start = now - timedelta(days=req.period_days)
    entries = await service.get_inventory_export(server_ids, req.period_days)
    return {
        "inventory_export": {
            "schema_version": "3",
            "schema_doc": "docs/architecture/inventory-export.md",
            "engine_id": "zconverter-assessment-portal",
            "exported_at": now.isoformat(),
            "period_window": {
                "days": req.period_days,
                "start": period_start.isoformat(),
                "end": now.isoformat(),
            },
            "size_class_guide": _SIZE_CLASS_GUIDE,
            "servers": entries,
        }
    }
