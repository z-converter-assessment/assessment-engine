"""JSON API 라우터.

의존성 주입 정석: `internal_id`는 `resolve_internal_id` Depends로. 라우터 내 _resolve 함수 없음.
검증 단일 경로 (F3): metric_type/time_range/bucket/agg/device_category 모두 Literal로 라우터에서.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from assessment_engine.db.repositories.query.types import EnvironmentMetricType
from assessment_engine.web.deps import get_service, resolve_internal_id
from assessment_engine.web.services.query_service import (
    AggFunc,
    BucketSize,
    DeviceCategory,
    MetricType,
    QueryService,
    TimeRange,
)

api_router = APIRouter(prefix="/api/servers", tags=["api"])


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
        internal_id,
        metric_type,
        dimension,
        time_range,
        bucket,
        agg,
        end,
        device_category,
    )


@api_router.get("/environment/metrics-chart")
async def get_environment_metrics_chart(
    metric_type: EnvironmentMetricType = Query(...),
    time_range: TimeRange = Query("15m"),
    bucket: BucketSize = Query("1m"),
    ids: str | None = Query(None, description="public_ids(comma) — 선택 N대 한정. 미지정 시 전체 환경."),
    service: QueryService = Depends(get_service),
):
    """환경 시계열 — 환경 성능 추이 live + 대시보드 추이. ids 면 선택 N대 한정, 없으면 전체 환경."""
    server_ids = None
    if ids:
        public_ids = [pid.strip() for pid in ids.split(",") if pid.strip()]
        sid_map = await service.resolve_server_ids(public_ids)
        server_ids = list(sid_map.values())
    return await service.get_environment_metric_chart(metric_type, time_range, bucket, server_ids=server_ids)


@api_router.get("/{server_id}/events/reboot")
async def get_reboot_events(
    time_range: TimeRange = Query("24h"),
    end: datetime | None = Query(None),
    internal_id: int = Depends(resolve_internal_id),
    service: QueryService = Depends(get_service),
):
    """차트 vertical marker용 — 지정 time_range 내 시스템 재부팅·에이전트 재시작 시점.

    응답: `[{collected_at, boot_time, agent_started_at, kind}]`
    kind: "reboot" (시스템 재부팅 또는 첫 등록) | "restart" (에이전트만 재시작)
    """
    return await service.get_reboot_events(internal_id, time_range, end)
