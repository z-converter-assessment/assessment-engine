from fastapi import APIRouter

from app.dto.inbound import ServerMetricInput
from app.workers.tasks import process_metric

router = APIRouter(prefix="/ingest", tags=["ingestion"])


@router.post("/", status_code=202)
async def ingest(payload: ServerMetricInput):
    await process_metric.kiq(payload)
    return {"status": "accepted"}