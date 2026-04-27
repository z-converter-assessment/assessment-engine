import dataclasses
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from config import consumer_settings
from web.deps import get_redis_client, get_service
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
    metric_type: str = Query(...),
    dimension: str | None = Query(None),
    time_range: str = Query("1h"),
    bucket: str = Query("5m"),
    agg: str = Query("avg"),
    service: QueryService = Depends(get_service),
):
    return await service.get_metric_chart(server_id, metric_type, dimension, time_range, bucket, agg)


@api_router.get("/{server_id}/metrics/stream")
async def metrics_stream(
    server_id: int,
    redis: Redis = Depends(get_redis_client),
):
    """SSE: consumer가 metrics.events 채널에 publish하면 해당 server_id 이벤트를 전달."""
    async def event_stream():
        async with redis.pubsub() as pubsub:
            await pubsub.subscribe(consumer_settings.redis_channel_metrics)
            try:
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                    except (ValueError, TypeError):
                        continue
                    if payload.get("server_id") == server_id:
                        yield f"data: {message['data']}\n\n"
            except Exception:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )