import asyncio
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.celery_app import celery_app
from app.contracts.task_names import PROCESS_METRIC

router = APIRouter(prefix="/ingest", tags=["ingestion"])


class DiskPayload(BaseModel):
    name: str
    size: Optional[str] = None


class FreePayload(BaseModel):
    mem_total_mb: Optional[int] = None


class ServerPayload(BaseModel):
    hostname: str
    nproc: Optional[str] = None
    free: Optional[FreePayload] = None
    lsblk_raw: Optional[list[DiskPayload]] = None
    ip_raw: Optional[dict] = None


@router.post("/", status_code=202)
async def ingest(payload: ServerPayload):
    kwargs = {
        "hostname": payload.hostname,
        "nproc": payload.nproc,
        "free": payload.free.model_dump() if payload.free else None,
        "lsblk_raw": [d.model_dump() for d in payload.lsblk_raw] if payload.lsblk_raw else [],
        "ip_raw": payload.ip_raw or {},
    }
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: celery_app.send_task(PROCESS_METRIC, kwargs=kwargs))
    return {"status": "accepted"}
