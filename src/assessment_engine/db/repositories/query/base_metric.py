"""Metric chart 도메인 추상 인터페이스 — dashboard snapshot · 시계열 · 차트 dispatch · reboot marker."""

from abc import ABC, abstractmethod
from datetime import datetime

from assessment_engine.db.dtos.outbound import DashboardRaw, MetricSeries, RebootEvent
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
    ) -> list[MetricSeries]: ...

    @abstractmethod
    async def reboot_events(
        self,
        server_id: int,
        start: datetime,
        end: datetime,
    ) -> list[RebootEvent]: ...
