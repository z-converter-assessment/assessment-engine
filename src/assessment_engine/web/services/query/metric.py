"""메트릭·차트 조회 mixin — 최신 대시보드·시계열 스냅샷·추이 차트·재부팅 마커."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from assessment_engine.cache.redis import safe_get, safe_set
from assessment_engine.db.dtos.outbound import RebootEvent, SaturationRaw
from assessment_engine.db.repositories.query.types import (
    TIME_RANGE_TD,
    AggFunc,
    BucketSize,
    EnvironmentMetricType,
    MetricType,
    TimeRange,
)
from assessment_engine.web.services.cache_serializer import (
    dashboard_from_json,
    dashboard_to_json,
)
from assessment_engine.web.services.mappers.metric import to_metric_series_item
from assessment_engine.web.services.mappers.metric_dashboard import (
    build_dashboard,
    build_saturation_signals,
)
from assessment_engine.web.services.query._base import _BaseQueryServiceMixin
from assessment_engine.web.settings import get_web_settings

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from assessment_engine.db.repositories.query import QueryRepository
    from assessment_engine.web.view_models.metric import MetricDashboard, MetricSeriesItem


async def latest_metric(
    repo: QueryRepository,
    redis: Redis,
    server_id: int,
    saturation: SaturationRaw | None = None,
) -> MetricDashboard | None:
    """서버 1대 최신 스냅샷 — Redis 캐시 우선, 없으면 조회 후 채운다.

    mixin 메서드가 아니라 자유 함수다. 환경 도메인이 서버마다 이걸 부르는데, mixin 메서드로 두면
    형제 호출이 되고 `self` 를 Protocol 로 좁혀야 한다 — 그 Protocol 은 런타임에 아무것도 강제하지
    않으면서 결합만 숨긴다.
    """
    cache_key = get_web_settings().redis_key_cache_metrics.format(server_id)
    cached = await safe_get(redis, cache_key)
    if cached:
        return dashboard_from_json(cached)

    raw = await repo.get_latest_dashboard(server_id)
    if not raw or not raw.metrics:
        return None
    result = build_dashboard(raw)
    # 호출자가 벌크 조회분을 넘기면 재조회를 생략한다 — 환경 화면에서 per-server 재조회로 N+1 이 나는 자리다.
    sat = saturation
    if sat is None:
        since = datetime.now(UTC) - timedelta(minutes=10)
        sat = (await repo.get_latest_saturation([server_id], since)).get(server_id) or SaturationRaw()
    cur = raw.metrics[0] if raw.metrics else None
    signals = build_saturation_signals(
        os_family=raw.os_family,
        kernel_version=raw.kernel_version,
        run_queue_total=cur.cpu_run_queue if cur else None,
        cores=cur.cpu_logical_count if cur else None,
        steal_pct=result.cpu.steal_pct if result.cpu else None,
        blocked=cur.cpu_blocked if cur else None,
        sat=sat,
    )
    result.cpu_saturation = signals["cpu"]
    result.mem_saturation = signals["mem"]
    result.disk_saturation = signals["disk"]
    result.net_saturation = signals["net"]
    # 에러 축(E)은 여기 안 싣는다 — 평가 윈도우 카드(get_period_assessment)가 맡고, per-server 조회는 N+1 이다.
    await safe_set(redis, cache_key, dashboard_to_json(result), ex=get_web_settings().redis_ttl_cache_metrics)
    return result


class MetricQueryMixin(_BaseQueryServiceMixin):
    async def get_latest_metric(
        self, server_id: int, saturation: SaturationRaw | None = None
    ) -> MetricDashboard | None:
        return await latest_metric(self.repo, self.redis, server_id, saturation)

    async def get_metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeriesItem]:
        dtos = await self.repo.get_metric_snapshots(server_id, cursor, limit)
        return [to_metric_series_item(dto) for dto in dtos]

    async def get_environment_metric_chart(
        self,
        metric_type: EnvironmentMetricType,
        time_range: TimeRange,
        bucket: BucketSize,
        end: datetime | None = None,
        server_ids: list[int] | None = None,
    ) -> list[MetricSeriesItem]:
        """환경 시계열 — server_ids=None 이면 전체 환경, 주어지면 선택 N대 한정.

        시점별 1값(그 시점 데이터가 있는 서버 sum/sum) -> 버킷 avg. 서버 상세와 같은 산식이라
        dimension 없는 지표(cpu/mem)는 1대 선택 시 상세 화면과 값이 일치한다.
        """
        end_dt = end or datetime.now(UTC)
        start = end_dt - TIME_RANGE_TD[time_range]
        dtos = await self.repo.get_metric_trend(
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
        collapse: bool = False,
    ) -> list[MetricSeriesItem]:
        # collapse=True 는 물리 디바이스 수와 무관하게 1선으로 합산한다 — 디바이스가 많으면 멀티라인이 안 읽힌다.
        dtos = await self.repo.get_metric_chart(
            server_id, metric_type, dimension, time_range, bucket, agg, end, collapse
        )
        return [to_metric_series_item(dto) for dto in dtos]

    async def get_reboot_events(
        self,
        server_id: int,
        time_range: TimeRange,
        end: datetime | None = None,
    ) -> list[RebootEvent]:
        """차트 vertical marker 용 — 지정 time_range 내 시스템 재부팅·에이전트 재시작 시점.

        파생 필드가 없고 datetime·Literal 이 그대로 직렬화돼 ViewModel 변환 없이 outbound DTO 를 넘긴다.
        """
        end_dt = end or datetime.now(UTC)
        start = end_dt - TIME_RANGE_TD[time_range]
        return await self.repo.get_reboot_events(server_id, start, end_dt)
