"""Attention warning 도메인 concrete — metric gap (통신 끊김 운영신호) 전용."""

from sqlalchemy import text

from assessment_engine.db.dtos.outbound import MetricGapWarningRaw
from assessment_engine.db.repositories.query._base import _BaseQueryMixin


class SqlAttentionQueryRepository(_BaseQueryMixin):
    async def get_metric_gap_warnings(
        self,
        gap_minutes: int,
        recent_hours: int,
        limit: int | None,
    ) -> list[MetricGapWarningRaw]:
        sql = text("""
            WITH metric_max AS (
                SELECT server_id, MAX(collected_at) AS last_metric_at
                FROM server_metrics
                WHERE collected_at >= now() - (LEAST(:recent_h, 168) * interval '1 hour')
                GROUP BY server_id
            )
            SELECT s.public_id AS public_id,
                   s.hostname  AS hostname,
                   mm.last_metric_at AS last_metric_at
            FROM server_inventory s
            JOIN metric_max mm ON mm.server_id = s.id
            WHERE mm.last_metric_at < now() - (:gap_min * interval '1 minute')
              AND mm.last_metric_at > now() - (:recent_h * interval '1 hour')
            ORDER BY mm.last_metric_at ASC
            LIMIT :limit
        """)
        result = await self.session.execute(
            sql,
            {"gap_min": gap_minutes, "recent_h": recent_hours, "limit": limit},
        )
        return [
            MetricGapWarningRaw(
                public_id=str(r.public_id),
                hostname=r.hostname,
                last_metric_at=r.last_metric_at,
            )
            for r in result.all()
        ]
