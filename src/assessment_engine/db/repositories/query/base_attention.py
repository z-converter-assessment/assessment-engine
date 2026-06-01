"""Attention warning 도메인 추상 인터페이스 — metric gap (통신 끊김 운영신호) 전용."""

from abc import ABC, abstractmethod

from assessment_engine.db.dtos.outbound import MetricGapWarningRaw


class BaseAttentionQueryRepository(ABC):
    @abstractmethod
    async def metric_gap_warnings(
        self,
        gap_minutes: int,
        recent_hours: int,
        limit: int,
    ) -> list[MetricGapWarningRaw]:
        """metric 발행 갭이 gap_minutes 초과 + 최근 recent_hours 안 발행 있던 서버 — '한때 살아있다 끊김'."""
        ...
