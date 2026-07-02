"""메트릭·차트 조회 mixin — 최신 대시보드·시계열 스냅샷·추이 차트·재부팅 마커."""

from datetime import UTC, datetime
from typing import Literal

from assessment_engine.cache.redis import safe_get, safe_set
from assessment_engine.db.dtos.outbound import MetricSeries, RebootEvent
from assessment_engine.db.repositories.query.types import (
    _BUCKET_INFO,
    TIME_RANGE_TD,
    AggFunc,
    BucketSize,
    MetricType,
    TimeRange,
)
from assessment_engine.web.services.cache_serializer import (
    dashboard_from_json,
    dashboard_to_json,
)
from assessment_engine.web.services.device_filters import (
    is_data_volume,
    is_lvm_disk,
    is_partition,
    is_physical_disk,
    is_virtual_interface,
)
from assessment_engine.web.services.mappers.metric import to_metric_series_item
from assessment_engine.web.services.metrics_calculator import build_dashboard
from assessment_engine.web.services.query._base import _BaseQueryServiceMixin
from assessment_engine.web.settings import web_settings
from assessment_engine.web.view_models.metric import MetricDashboard, MetricSeriesItem

_DISK_METRIC_TYPES = frozenset({"disk.read_iops", "disk.write_iops", "disk.read_kbps", "disk.write_kbps"})
_NET_METRIC_TYPES = frozenset(
    {"net.rx_bytes_per_sec", "net.tx_bytes_per_sec", "net.rx_packets_per_sec", "net.tx_packets_per_sec"}
)

# disk metric_type에서만 의미를 갖는 service 레벨 분기. 라우터에서 Literal로 검증 (F3 단일 경로).
DeviceCategory = Literal["phys", "logical"]


def _filter_disk_category(dtos: list[MetricSeries], category: DeviceCategory) -> list[MetricSeries]:
    if category == "phys":
        return [d for d in dtos if is_physical_disk(d.kind)]
    # category == "logical"
    lvm = [d for d in dtos if is_lvm_disk(d.kind)]
    return lvm if lvm else [d for d in dtos if is_partition(d.kind)]


class MetricQueryMixin(_BaseQueryServiceMixin):
    async def get_latest_metric(self, server_id: int) -> MetricDashboard | None:
        cache_key = web_settings.redis_key_cache_metrics.format(server_id)
        cached = await safe_get(self.redis, cache_key)
        if cached:
            return dashboard_from_json(cached)

        raw = await self.repo.latest_dashboard(server_id)
        if not raw or not raw.metrics:
            return None
        result = build_dashboard(raw)
        await safe_set(self.redis, cache_key, dashboard_to_json(result), ex=60)
        return result

    async def get_metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeriesItem]:
        dtos = await self.repo.metric_snapshots(server_id, cursor, limit)
        return [to_metric_series_item(dto) for dto in dtos]

    async def get_environment_metric_chart(
        self,
        metric_type: str,
        time_range: TimeRange,
        bucket: BucketSize,
        end: datetime | None = None,
        server_ids: list[int] | None = None,
    ) -> list[MetricSeriesItem]:
        """환경 시계열 — 대시보드 추이 차트 live. server_ids=None 이면 전체 환경, 주어지면 선택 N대 한정.

        통일 metric_trend(collapse=True) 위임 — 시점별 1값(그 시점 데이터 있는 서버 sum/sum) -> 버킷 avg.
        서버 상세(collapse=False, [1대])와 동일 산식 — dimension 없는 지표(cpu/mem/swap/load)는 선택 1대=상세 일치.
        """
        end_dt = end or datetime.now(UTC)
        bi, bucket_td = _BUCKET_INFO[bucket]
        start = end_dt - TIME_RANGE_TD[time_range]
        dtos = await self.repo.metric_trend(
            metric_type, start, end_dt, bi, bucket_td, server_ids=server_ids, agg="avg", collapse=True
        )
        return [to_metric_series_item(dto) for dto in dtos]

    async def get_metric_chart(
        self,
        server_id: int,
        metric_type: MetricType,
        dimension: str | None,
        time_range: TimeRange,
        bucket: BucketSize,
        agg: AggFunc,
        end: datetime | None = None,
        device_category: DeviceCategory | None = None,
    ) -> list[MetricSeriesItem]:
        # 검증은 라우터의 Query(MetricType) Literal Pydantic 단계에서 이미 처리됨.
        dtos = await self.repo.metric_chart(server_id, metric_type, dimension, time_range, bucket, agg, end)
        if metric_type == "fs.usage_percent":
            # 데이터 볼륨만 남김 (/ · C: 등) — 가상/부트(/boot · /sys · /proc) · major0 제외.
            # net 의 is_virtual_interface 필터와 동일 의도(유효 차원만 표시). Windows 는 C: 가 유일 데이터 볼륨.
            dtos = [d for d in dtos if is_data_volume(d.kind)]
        if metric_type in _NET_METRIC_TYPES:
            dtos = [d for d in dtos if not is_virtual_interface(d.kind)]
        if device_category is not None and metric_type in _DISK_METRIC_TYPES:
            dtos = _filter_disk_category(dtos, device_category)
        return [to_metric_series_item(dto) for dto in dtos]

    async def get_reboot_events(
        self,
        server_id: int,
        time_range: TimeRange,
        end: datetime | None = None,
    ) -> list[RebootEvent]:
        """차트 vertical marker용 — 지정 time_range 내 시스템 재부팅·에이전트 재시작 시점.

        outbound DTO 그대로 반환 (raw 그대로 — P1). 별도 ViewModel 변환 없음 — 파생 필드
        없고 datetime / Literal kind 그대로 JSON 직렬화 가능 (cache_serializer._json_default).
        """
        end_dt = end or datetime.now(UTC)
        start = end_dt - TIME_RANGE_TD[time_range]
        return await self.repo.reboot_events(server_id, start, end_dt)
