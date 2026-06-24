"""server_mount_usage_5m continuous aggregate — 마운트 사용률 사전집계

Revision ID: d1f3b5a7c2e4
Revises: c8e2a4f6d1b3
Create Date: 2026-06-24 18:00:00.000000

마운트 사용률(gauge)·fill_rate 도 매 보고서 raw 스캔에서 cagg 사전집계. used_pct max/avg + first/last avail(fill_rate)
+ total max. report_mount_worst·report_aggregate mount_max·report_mount_usage 가 본 cagg 소비 — 가상 mount 필터
(_DATA_VOLUME_SQL_FILTER 스냅샷)는 cagg 단계 pre-applied 라 소비 쿼리에서 필터 불요. 5분 버킷, real-time aggregation.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1f3b5a7c2e4"
down_revision: str | Sequence[str] | None = "c8e2a4f6d1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE MATERIALIZED VIEW server_mount_usage_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT server_id, mount, time_bucket('5 minutes', collected_at) AS bucket,
            max((1 - avail_bytes::float / total_bytes) * 100) AS used_pct_max,
            avg((1 - avail_bytes::float / total_bytes) * 100) AS used_pct_avg,
            max(total_bytes)                                  AS total_bytes_max,
            first(avail_bytes, collected_at)                 AS avail_first,
            last(avail_bytes, collected_at)                  AS avail_last
        FROM server_mount_usage
        WHERE total_bytes > 0 AND avail_bytes IS NOT NULL
          AND (
            mount ~ '^[A-Za-z]:'
            OR ((major IS NULL OR major <> 0) AND mount <> '/boot' AND mount NOT LIKE '/boot/%')
          )
        GROUP BY server_id, mount, bucket
        WITH NO DATA
    """)
    op.execute("""
        SELECT add_continuous_aggregate_policy('server_mount_usage_5m',
            start_offset => INTERVAL '8 days', end_offset => INTERVAL '10 minutes',
            schedule_interval => INTERVAL '5 minutes')
    """)


def downgrade() -> None:
    op.execute("SELECT remove_continuous_aggregate_policy('server_mount_usage_5m', if_exists => true)")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS server_mount_usage_5m")
