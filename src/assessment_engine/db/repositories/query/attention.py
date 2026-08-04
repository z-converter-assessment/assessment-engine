"""Attention warning 도메인 추상 인터페이스 — metric gap (통신 끊김 운영신호) 전용."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import MetricGapWarningRaw


class AttentionQueryRepository(Protocol):
    async def metric_gap_warnings(
        self,
        gap_minutes: int,
        recent_hours: int,
        limit: int | None,
    ) -> list[MetricGapWarningRaw]:
        """metric 발행 갭이 gap_minutes 초과 + 최근 recent_hours 안 발행 있던 서버 — '한때 살아있다 끊김'.

        limit=None 이면 무제한(Postgres LIMIT NULL) — 운영신호 카드 전수 출력.
        """
        ...
