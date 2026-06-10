"""Exports router — 자동화 도구 입력용 정제 산출물 다운로드.

스키마·정제 원칙·사용처: docs/architecture/web/export-schema.md.
v4: 사용처축 배치 — server 항목을 identity/os/spec(VM 생성)/usage(right-sizing 측정)/assessment(평가 결과)/
   services(보안그룹)로 재정렬. 자동화 도구가 사용처 블록을 통째로 소비.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from assessment_engine.web.deps import get_service
from assessment_engine.web.services.query_service import QueryService

exports_router = APIRouter(prefix="/api/exports", tags=["exports"])

# recommended_size_class 객체화 — 자동화 도구가 key→자기 도메인 instance type 매핑할 때 참고용 가이드.
_SIZE_CLASS_GUIDE: dict[str, str] = {
    "under_provisioned": "instance type 상향 (vCPU·RAM 증가)",
    "over_provisioned": "instance type 축소 (vCPU·RAM 감소)",
    "idle": "운영 종료 또는 통합 검토 — 사용 거의 없음",
    "shutdown": "운영 종료 검토 — 사용 0에 근접",
    "optimal": "변경 불필요 — 적정 사양",
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

    AI 진단 narrative 포함 X — 자동화 도구 (Terraform · Ansible 등) 안 자연어 parse 불가,
    구조화 데이터 (`recommended_size_class` 필드 + 분류·임계 수치) 만 활용 catalog 정공.
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
            "schema_version": "4",
            "schema_doc": "docs/architecture/web/export-schema.md",
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
