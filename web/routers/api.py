import dataclasses
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from db.repositories.base_query_repository import AggFunc, BucketSize, MetricType, TimeRange
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
    return dataclasses.asdict(result)


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
    metric_type: MetricType = Query(...),
    dimension: str | None = Query(None),
    time_range: TimeRange = Query("1h"),
    bucket: BucketSize = Query("5m"),
    agg: AggFunc = Query("avg"),
    service: QueryService = Depends(get_service),
):
    return await service.get_metric_chart(server_id, metric_type, dimension, time_range, bucket, agg)


@api_router.get("/{server_id}/metrics/stream")
async def metrics_stream(
    server_id: int,
    service: QueryService = Depends(get_service),
):
    async def event_stream():
        async for data in service.stream_metrics_events(server_id):
            yield f"data: {data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )