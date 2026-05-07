"""JSON API 라우터.

의존성 주입 정석: `internal_id`는 `resolve_internal_id` Depends로. 라우터 내 _resolve 함수 없음.
검증 단일 경로 (F3): metric_type/time_range/bucket/agg/device_category 모두 Literal로 라우터에서.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from assessment_engine.web.deps import get_service, resolve_internal_id
from assessment_engine.web.services.query_service import (
    AggFunc,
    BucketSize,
    DeviceCategory,
    MetricType,
    QueryService,
    TimeRange,
)

api_router = APIRouter(prefix="/api/v1/servers", tags=["api"])


@api_router.get("/{server_id}/collection-status")
async def get_collection_status(
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    return await service.get_collection_status(internal_id)


@api_router.get("/{server_id}/metrics/latest")
async def get_latest_metric(
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    result = await service.get_latest_metric(internal_id)
    if not result:
        raise HTTPException(status_code=404)
    return result


@api_router.get("/{server_id}/metrics/snapshots")
async def get_metric_snapshots(
    cursor: datetime | None = Query(None),
    limit: int = Query(10, ge=1, le=100),
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    return await service.get_metric_snapshots(internal_id, cursor, limit)


@api_router.get("/{server_id}/metrics/chart")
async def get_metric_chart(
    metric_type: MetricType = Query(...),
    dimension: str | None = Query(None),
    time_range: TimeRange = Query("1h"),
    bucket: BucketSize = Query("5m"),
    agg: AggFunc = Query("avg"),
    end: datetime | None = Query(None),
    device_category: DeviceCategory | None = Query(None),
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    return await service.get_metric_chart(
        internal_id, metric_type, dimension, time_range, bucket, agg, end, device_category,
    )


@api_router.get("/{server_id}/metrics/stream")
async def metrics_stream(
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    async def event_stream():
        async for data in service.stream_metrics_events(internal_id):
            yield f"data: {data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )