"""Attention warning 도메인 추상 인터페이스 — metric gap (통신 끊김 운영신호) 전용."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import MetricGapWarningRaw


class AttentionQueryRepository(Protocol):
    async def get_metric_gap_warnings(
        self,
        gap_minutes: int,
        recent_hours: int,
        limit: int | None,
    ) -> list[MetricGapWarningRaw]: ...
