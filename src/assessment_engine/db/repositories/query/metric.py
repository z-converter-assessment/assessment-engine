"""Metric chart 도메인 추상 인터페이스 — dashboard snapshot · 시계열 · 차트 dispatch · reboot marker."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from assessment_engine.db.dtos.outbound import (
        DashboardRaw,
        ErrorFleetRaw,
        FleetErrorRaw,
        MetricSeries,
        RebootEvent,
        SaturationRaw,
    )
    from assessment_engine.db.repositories.query.types import (
        AggFunc,
        BucketSize,
        EnvironmentMetricType,
        MetricType,
        TimeRange,
    )


class MetricQueryRepository(Protocol):
    async def get_latest_dashboard(self, server_id: int) -> DashboardRaw | None: ...
    async def get_latest_saturation(self, server_ids: list[int], since: datetime) -> dict[int, SaturationRaw]: ...
    async def get_latest_errors(self, server_id: int, since: datetime) -> ErrorFleetRaw: ...
    async def get_fleet_error_summary(self, server_ids: list[int], since: datetime) -> FleetErrorRaw: ...
    async def get_fleet_error_hosts(self, server_ids: list[int], since: datetime) -> set[int]: ...
    async def get_latest_link_speed(self, server_ids: list[int], since: datetime) -> dict[int, dict[str, int]]: ...
    async def get_metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeries]: ...
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
    ) -> list[MetricSeries]: ...

    # metric_type 은 두 Literal 의 합집합 — 서버 상세 차트(MetricType)와 환경 추이(EnvironmentMetricType)가
    # 같은 SQL 을 공유한다. 미처리 값은 본문 끝 AssertionError 로 떨어지므로 선언을 좁혀 두면 그 자리가
    # 호출 시점에 잡힌다 (과거 미선언 str 로 500 이 났던 경로).
    async def get_metric_trend(
        self,
        metric_type: MetricType | EnvironmentMetricType,
        start: datetime,
        end: datetime,
        bucket: BucketSize,
        server_ids: list[int] | None = None,
        agg: AggFunc = "avg",
        dimension: str | None = None,
        collapse: bool = True,
    ) -> list[MetricSeries]: ...
    async def get_reboot_events(
        self,
        server_id: int,
        start: datetime,
        end: datetime,
    ) -> list[RebootEvent]: ...
