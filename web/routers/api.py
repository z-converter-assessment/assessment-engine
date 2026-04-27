from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from web.deps import get_service
from web.services.query_service import QueryService

api_router = APIRouter(prefix="/api/v1/servers", tags=["api"])


@api_router.get("/{server_id}/collection-status")
async def get_collection_status(
    server_id: int,
    service: QueryService = Depends(get_service),
):
    return await service.get_collection_status(server_id)


@api_router.get("/{server_id}/metrics/latest")
async def get_latest_metric(
    server_id: int,
    service: QueryService = Depends(get_service),
):
    result = await service.get_latest_metric(server_id)
    if not result:
        raise HTTPException(status_code=404)
    return result


@api_router.get("/{server_id}/metrics/snapshots")
async def get_metric_snapshots(
    server_id: int,
    cursor: datetime | None = Query(None),
    limit: int = Query(10, ge=1, le=100),
    service: QueryService = Depends(get_service),
):
    return await service.get_metric_snapshots(server_id, cursor, limit)


@api_router.get("/{server_id}/metrics/chart")
async def get_metric_chart(
    server_id: int,
    metric_type: str = Query(...),
    dimension: str | None = Query(None),
    time_range: str = Query("1h"),
    bucket: str = Query("5m"),
    agg: str = Query("avg"),
    service: QueryService = Depends(get_service),
):
    return await service.get_metric_chart(server_id, metric_type, dimension, time_range, bucket, agg)