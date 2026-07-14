"""Metric chart 도메인 추상 인터페이스 — dashboard snapshot · 시계열 · 차트 dispatch · reboot marker."""

from abc import ABC, abstractmethod
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
    MetricType,
    TimeRange,
)


class BaseMetricQueryRepository(ABC):
    @abstractmethod
    async def latest_dashboard(self, server_id: int) -> DashboardRaw | None: ...

    @abstractmethod
    async def latest_saturation(self, server_ids: list[int], since: datetime) -> dict[int, SaturationRaw]: ...

    @abstractmethod
    async def latest_errors(self, server_id: int, since: datetime) -> ErrorFleetRaw: ...

    @abstractmethod
    async def fleet_error_summary(self, server_ids: list[int], since: datetime) -> FleetErrorRaw: ...

    @abstractmethod
    async def fleet_error_hosts(self, server_ids: list[int], since: datetime) -> set[int]: ...

    @abstractmethod
    async def latest_link_speed(self, server_ids: list[int], since: datetime) -> dict[int, dict[str, int]]: ...

    @abstractmethod
    async def metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeries]: ...

    @abstractmethod
    async def metric_chart(
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

    @abstractmethod
    async def metric_trend(
        self,
        metric_type: str,
        start: datetime,
        end: datetime,
        bucket: BucketSize,
        server_ids: list[int] | None = None,
        agg: str = "avg",
        dimension: str | None = None,
        collapse: bool = True,
    ) -> list[MetricSeries]: ...

    @abstractmethod
    async def reboot_events(
        self,
        server_id: int,
        start: datetime,
        end: datetime,
    ) -> list[RebootEvent]: ...
