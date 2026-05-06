from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from web.deps import get_service
from web.services.query_service import AggFunc, BucketSize, MetricType, QueryService, TimeRange

api_router = APIRouter(prefix="/api/v1/servers", tags=["api"])


async def _resolve(public_id: str, service: QueryService) -> int:
    server_id = await service.resolve_server_id(public_id)
    if server_id is None:
        raise HTTPException(status_code=404)
    return server_id


@api_router.get("/{server_id}/collection-status")
async def get_collection_status(
    server_id: str,
    service: QueryService = Depends(get_service),
):
    internal_id = await _resolve(server_id, service)
    return await service.get_collection_status(internal_id)


@api_router.get("/{server_id}/metrics/latest")
async def get_latest_metric(
    server_id: str,
    service: QueryService = Depends(get_service),
):
    internal_id = await _resolve(server_id, service)
    result = await service.get_latest_metric(internal_id)
    if not result:
        raise HTTPException(status_code=404)
    return result


@api_router.get("/{server_id}/metrics/snapshots")
async def get_metric_snapshots(
    server_id: str,
    cursor: datetime | None = Query(None),
    limit: int = Query(10, ge=1, le=100),
    service: QueryService = Depends(get_service),
):
    internal_id = await _resolve(server_id, service)
    return await service.get_metric_snapshots(internal_id, cursor, limit)


@api_router.get("/{server_id}/metrics/chart")
async def get_metric_chart(
    server_id: str,
    metric_type: MetricType = Query(...),
    dimension: str | None = Query(None),
    time_range: TimeRange = Query("1h"),
    bucket: BucketSize = Query("5m"),
    agg: AggFunc = Query("avg"),
    end: datetime | None = Query(None),
    device_category: str | None = Query(None),
    service: QueryService = Depends(get_service),
):
    internal_id = await _resolve(server_id, service)
    return await service.get_metric_chart(internal_id, metric_type, dimension, time_range, bucket, agg, end, device_category)


@api_router.get("/{server_id}/metrics/stream")
async def metrics_stream(
    server_id: str,
    service: QueryService = Depends(get_service),
):
    internal_id = await _resolve(server_id, service)

    async def event_stream():
        async for data in service.stream_metrics_events(internal_id):
            yield f"data: {data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )