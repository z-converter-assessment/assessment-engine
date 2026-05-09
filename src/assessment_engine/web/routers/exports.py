"""Exports router — 자동화 도구 입력용 정제 산출물 다운로드.

현재: 정제 Inventory JSON (OpenStack/Terraform/SDK 등 후속 자동화에 그대로 투입 가능).
양식·필드 정의: docs/assessment-deliverables.md.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from assessment_engine.web.deps import get_service
from assessment_engine.web.services.query_service import QueryService

exports_router = APIRouter(prefix="/api/v1/exports", tags=["exports"])


class InventoryExportRequest(BaseModel):
    target_public_ids: list[str] = Field(min_length=1, max_length=1000)


@exports_router.post("/inventory")
async def export_inventory(
    req: InventoryExportRequest,
    service: QueryService = Depends(get_service),
):
    """선택 서버 N대의 정제 inventory를 표준 JSON으로 반환.

    응답을 클라이언트가 파일로 저장 (브라우저 download). 서버에서 파일 생성 안 함 — stateless.
    """
    sid_map = await service.resolve_server_ids(req.target_public_ids)
    missing = [pid for pid in req.target_public_ids if pid not in sid_map]
    if missing:
        raise HTTPException(status_code=404, detail=f"server not found: {','.join(missing[:5])}")
    server_ids = [sid_map[pid] for pid in req.target_public_ids]

    entries = await service.get_inventory_export(server_ids)
    return {
        "inventory_export": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "zconverter-assessment-portal",
            "schema_version": "1",
            "servers": entries,
        }
    }
