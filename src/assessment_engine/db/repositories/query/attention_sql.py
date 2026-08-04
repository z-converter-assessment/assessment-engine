"""Attention warning 도메인 concrete — metric gap (통신 끊김 운영신호) 전용."""

from sqlalchemy import text

from assessment_engine.db.dtos.outbound import MetricGapWarningRaw
from assessment_engine.db.repositories.query._base import _BaseQueryMixin


class SqlAttentionQueryRepository(_BaseQueryMixin):
    async def metric_gap_warnings(
        self,
        gap_minutes: int,
        recent_hours: int,
        limit: int | None,
    ) -> list[MetricGapWarningRaw]:
        """metric 발행 갭 — '한때 살아있다 끊김' 패턴.

        - last_metric_at < now() - gap_minutes (현재 끊김)
        - last_metric_at > now() - recent_hours (한때는 살아있음 — 완전 dead 서버 제외)
        - partition pruning: recent_hours를 동적 binding + LEAST 168h(7d) cap.
          호출자 실수로 큰 값 넘겨도 자동 cap — 7d 이상은 metric_gap 의미 없음 (다른 신호 영역).
        - limit=None 이면 `LIMIT NULL`(Postgres 무제한) — 운영신호 카드 전수 출력.
        """
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
