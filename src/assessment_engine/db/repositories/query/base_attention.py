"""Attention warning 도메인 추상 인터페이스 — disk usage · metric gap."""

from abc import ABC, abstractmethod

from assessment_engine.db.dtos.outbound import DiskUsageWarningRaw, MetricGapWarningRaw


class BaseAttentionQueryRepository(ABC):
    @abstractmethod
    async def disk_usage_warnings(
        self,
        threshold_pct: float,
        limit: int,
    ) -> list[DiskUsageWarningRaw]:
        """전체 mount 중 사용률 임계 초과만 단일 SQL. mount당 latest 1건 → ORDER BY DESC LIMIT N."""
        ...

    @abstractmethod
    async def metric_gap_warnings(
        self,
        gap_minutes: int,
        recent_hours: int,
        limit: int,
    ) -> list[MetricGapWarningRaw]:
        """metric 발행 갭이 gap_minutes 초과 + 최근 recent_hours 안 발행 있던 서버 — '한때 살아있다 끊김'."""
        ...
