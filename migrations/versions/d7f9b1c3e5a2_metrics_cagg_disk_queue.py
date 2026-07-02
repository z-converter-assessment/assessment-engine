"""server_metrics_5m cagg 에 disk_queue_avg 추가 — Windows 디스크 saturation 집계

Revision ID: d7f9b1c3e5a2
Revises: c5e7a9b1d3f4
Create Date: 2026-07-01 00:00:04.000000

right-sizing 디스크 I/O saturation 축의 Windows 신호(saturation.disk_queue)를 baseline 이 소비하려면
server_metrics_5m cagg 가 per-bucket disk_queue 를 집계해야 한다. avg(sat_disk_queue) AS disk_queue_avg 추가.
(Linux 는 sat_disk_queue NULL -> disk_queue_avg NULL, iowait 축 사용 유지.)

기존 집계식 불변, disk_queue_avg 만 추가. cagg drop 후 재생성(WITH NO DATA, real-time). DB 초기화 전제라 백필 없음.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7f9b1c3e5a2"
down_revision: str | Sequence[str] | None = "c5e7a9b1d3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = """
    SELECT add_continuous_aggregate_policy('server_metrics_5m',
        start_offset => INTERVAL '8 days', end_offset => INTERVAL '10 minutes',
        schedule_interval => INTERVAL '5 minutes')
"""


def _create(with_disk_queue: bool) -> None:
    disk_queue_col = "avg(sat_disk_queue) AS disk_queue_avg," if with_disk_queue else ""
    op.execute(f"""
        CREATE MATERIALIZED VIEW server_metrics_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT
            server_id,
            time_bucket('5 minutes', collected_at) AS bucket,
            counter_agg(collected_at, (cpu_user + cpu_nice + cpu_system + cpu_idle
                + cpu_iowait + cpu_irq + cpu_softirq + cpu_steal)::double precision) AS cpu_total_ca,
            counter_agg(collected_at, cpu_idle::double precision)   AS cpu_idle_ca,
            counter_agg(collected_at, cpu_user::double precision)   AS cpu_user_ca,
            counter_agg(collected_at, cpu_system::double precision) AS cpu_system_ca,
            counter_agg(collected_at, cpu_iowait::double precision) AS cpu_iowait_ca,
            avg(CASE WHEN mem_total_kb > 0 AND mem_available_kb IS NOT NULL
                     THEN (1 - mem_available_kb::float / mem_total_kb) * 100 END) AS mem_pct_avg,
            max(CASE WHEN mem_total_kb > 0 AND mem_available_kb IS NOT NULL
                     THEN (1 - mem_available_kb::float / mem_total_kb) * 100 END) AS mem_pct_max,
            count(CASE WHEN mem_total_kb > 0 AND mem_available_kb IS NOT NULL THEN 1 END) AS mem_sample,
            max(load_15m) AS load_15m_max,
            max(CASE WHEN swap_total_kb > 0 AND swap_free_kb IS NOT NULL
                          AND swap_free_kb < swap_total_kb THEN 1 ELSE 0 END) AS swap_in_use,
            {disk_queue_col}
            count(*) AS sample_count
        FROM server_metrics
        GROUP BY server_id, bucket
        WITH NO DATA
    """)
    op.execute(_POLICY)


def _drop() -> None:
    op.execute("SELECT remove_continuous_aggregate_policy('server_metrics_5m', if_exists => true)")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS server_metrics_5m")


def upgrade() -> None:
    _drop()
    _create(with_disk_queue=True)


def downgrade() -> None:
    _drop()
    _create(with_disk_queue=False)
