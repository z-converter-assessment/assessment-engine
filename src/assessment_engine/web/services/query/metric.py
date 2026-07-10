"""메트릭·차트 조회 mixin — 최신 대시보드·시계열 스냅샷·추이 차트·재부팅 마커."""

from datetime import UTC, datetime, timedelta

from assessment_engine.cache.redis import safe_get, safe_set
from assessment_engine.db.dtos.outbound import RebootEvent, SaturationRaw
from assessment_engine.db.repositories.query.types import (
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
from assessment_engine.web.services.mappers.metric import to_metric_series_item
from assessment_engine.web.services.metrics_calculator import build_dashboard
from assessment_engine.web.services.query._base import _BaseQueryServiceMixin
from assessment_engine.web.settings import web_settings
from assessment_engine.web.view_models.metric import MetricDashboard, MetricSeriesItem


class MetricQueryMixin(_BaseQueryServiceMixin):
    async def get_latest_metric(
        self, server_id: int, saturation: SaturationRaw | None = None
    ) -> MetricDashboard | None:
        cache_key = web_settings.redis_key_cache_metrics.format(server_id)
        cached = await safe_get(self.redis, cache_key)
        if cached:
            return dashboard_from_json(cached)

        raw = await self.repo.latest_dashboard(server_id)
        if not raw or not raw.metrics:
            return None
        result = build_dashboard(raw)
        # 포화 신호(디스크 await/큐·메모리 페이징) — 호출자가 벌크 조회분(saturation)을 넘기면 재조회 생략
        # (B4: _assemble_realtime 이 sat_map 을 이미 벌크 조회 -> per-server 재조회 N+1 제거). 미전달 시 단일 조회.
        sat = saturation
        if sat is None:
            since = datetime.now(UTC) - timedelta(minutes=10)
            sat = (await self.repo.latest_saturation([server_id], since)).get(server_id) or SaturationRaw()
        result.disk_await_ms = sat.await_ms
        result.disk_queue = sat.pending_ops  # 디스크 큐 깊이(await 폴백)
        # 하드폴트 rate — Linux refault / Windows Pages Input 통일 (paging_major_rate). 메모리 압박 표시 축.
        result.mem_pages_input_rate = sat.paging_major_rate
        result.net_retrans_pct = sat.retrans_pct
        await safe_set(self.redis, cache_key, dashboard_to_json(result), ex=web_settings.redis_ttl_cache_metrics)
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
        서버 상세(collapse=False, [1대])와 동일 산식 — dimension 없는 지표(cpu/mem)는 선택 1대=상세 일치.
        """
        end_dt = end or datetime.now(UTC)
        start = end_dt - TIME_RANGE_TD[time_range]
        dtos = await self.repo.metric_trend(
            metric_type, start, end_dt, bucket, server_ids=server_ids, agg="avg", collapse=True
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
    ) -> list[MetricSeriesItem]:
        # 검증은 라우터의 Query(MetricType) Literal Pydantic 단계에서 이미 처리됨.
        # device/mount 선별은 repo SQL 단계 (fs.usage_percent 데이터볼륨 필터). 표시 계층 재필터 없음.
        dtos = await self.repo.metric_chart(server_id, metric_type, dimension, time_range, bucket, agg, end)
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
