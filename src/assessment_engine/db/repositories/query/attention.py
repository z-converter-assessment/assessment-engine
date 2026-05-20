"""Attention warning 도메인 concrete — disk usage · metric gap."""

from sqlalchemy import text

from assessment_engine.db.dtos.outbound import DiskUsageWarningRaw, MetricGapWarningRaw
from assessment_engine.db.repositories.query._base import _BaseQueryMixin
from assessment_engine.db.repositories.query.base_attention import BaseAttentionQueryRepository
from assessment_engine.db.repositories.query.types import _VIRTUAL_MOUNT_SQL_FILTER


class AttentionQueryRepository(_BaseQueryMixin, BaseAttentionQueryRepository):
    async def disk_usage_warnings(
        self,
        threshold_pct: float,
        limit: int,
    ) -> list[DiskUsageWarningRaw]:
        """전체 mount 중 latest 사용률 임계 초과만 단일 SQL.

        - mount당 최신 1행 (PARTITION BY server_id, mount ORDER BY collected_at DESC)
        - 7d partition pruning — attention은 "현재 시점 단기 신호" 의도.
          7d 이상 안 갱신된 mount는 stale (metric_gap이 별도 담당)
        - 가상 mount 제외:
          (a) `total_bytes > 0 AND avail_bytes IS NOT NULL` — 가상 fs는 보통 둘 중 하나 NULL/0
          (b) mount path NOT LIKE 가상 prefix — `device_filters._VIRTUAL_MOUNT_PREFIXES`와 일관.
              ServerMountUsage에 fstype 컬럼이 없어 SQL 단에선 path 기반만 가능. fstype 기반 정밀
              필터는 storage detail mapper 측에서 수행 (defense in depth).
        """
        sql = text(f"""
            WITH mount_latest AS (
                SELECT server_id, mount, total_bytes, avail_bytes, collected_at,
                    ROW_NUMBER() OVER (PARTITION BY server_id, mount ORDER BY collected_at DESC) AS rn
                FROM server_mount_usage
                WHERE collected_at >= now() - interval '7 days'
                  AND {_VIRTUAL_MOUNT_SQL_FILTER}
            )
            SELECT s.public_id AS public_id,
                   s.hostname  AS hostname,
                   m.mount     AS mount,
                   m.total_bytes AS total_bytes,
                   m.avail_bytes AS avail_bytes,
                   m.collected_at AS last_metric_at
            FROM mount_latest m
            JOIN server_inventory s ON s.id = m.server_id
            WHERE m.rn = 1
              AND m.total_bytes > 0
              AND m.avail_bytes IS NOT NULL
              AND (1 - m.avail_bytes::float / m.total_bytes) >= :threshold
            ORDER BY (1 - m.avail_bytes::float / m.total_bytes) DESC
            LIMIT :limit
        """)
        result = await self.session.execute(
            sql,
            {"threshold": threshold_pct / 100.0, "limit": limit},
        )
        return [
            DiskUsageWarningRaw(
                public_id=str(r.public_id),
                hostname=r.hostname,
                mount=r.mount,
                total_bytes=r.total_bytes,
                avail_bytes=r.avail_bytes,
                last_metric_at=r.last_metric_at,
            )
            for r in result.all()
        ]

    async def metric_gap_warnings(
        self,
        gap_minutes: int,
        recent_hours: int,
        limit: int,
    ) -> list[MetricGapWarningRaw]:
        """metric 발행 갭 — '한때 살아있다 끊김' 패턴.

        - last_metric_at < now() - gap_minutes (현재 끊김)
        - last_metric_at > now() - recent_hours (한때는 살아있음 — 완전 dead 서버 제외)
        - partition pruning: recent_hours를 동적 binding + LEAST 168h(7d) cap.
          호출자 실수로 큰 값 넘겨도 자동 cap — 7d 이상은 metric_gap 의미 없음 (다른 신호 영역).
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
