"""server_metrics_5m cagg 재생성 — procs_running(run-queue) + oom_kill 집계 (ADR 0052)

Revision ID: c7e9b1d3f5a2
Revises: b6d8f0a2c4e1
Create Date: 2026-07-06 00:00:02.000000

Linux CPU 포화를 load(IO 오염)에서 실행 큐로 교체하기 위해 procs_running(gauge)을 avg 집계하고,
메모리 under 보조 증거로 oom_kill(counter)을 counter_agg 한다. 그간 저장만 하고 미소비(dead)이던
두 컬럼이 이걸로 실제 쓰인다. cagg 컬럼 ALTER 불가라 뷰 drop 후 재생성(WITH NO DATA, real-time, #C4).
집계식·버킷·정책은 f4a6c8e0b2d1 정의 그대로 두고 procs_running_avg·oom_kill_ca 만 덧붙인다.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e9b1d3f5a2"
down_revision: str | Sequence[str] | None = "b6d8f0a2c4e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = """
    SELECT add_continuous_aggregate_policy('server_metrics_5m',
        start_offset => INTERVAL '8 days', end_offset => INTERVAL '10 minutes',
        schedule_interval => INTERVAL '5 minutes')
"""

_CPU_TOTAL = """counter_agg(collected_at, (COALESCE(cpu_user,0) + COALESCE(cpu_nice,0)
                + COALESCE(cpu_system,0) + COALESCE(cpu_idle,0) + COALESCE(cpu_iowait,0)
                + COALESCE(cpu_irq,0) + COALESCE(cpu_softirq,0) + COALESCE(cpu_steal,0))::double precision)"""

# run-queue·oom 집계 — extra=True(upgrade)면 추가, False(downgrade)면 f4a6c8e0b2d1 정의 복원.
_EXTRA_COLS = """avg(procs_running)                                     AS procs_running_avg,
            counter_agg(collected_at, oom_kill::double precision) AS oom_kill_ca,"""


def _create(*, extra: bool) -> None:
    extra_cols = _EXTRA_COLS if extra else ""
    op.execute(f"""
        CREATE MATERIALIZED VIEW server_metrics_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT
            server_id,
            time_bucket('5 minutes', collected_at) AS bucket,
            {_CPU_TOTAL} AS cpu_total_ca,
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
            avg(sat_disk_queue) AS disk_queue_avg,
            avg(sat_cpu_run_queue) AS cpu_run_queue_avg,
            counter_agg(collected_at, sat_mem_paging_rate::double precision) AS mem_paging_ca,
            counter_agg(collected_at, cpu_steal::double precision)        AS cpu_steal_ca,
            avg(procs_blocked)                                          AS procs_blocked_avg,
            counter_agg(collected_at, pswpout::double precision)        AS pswpout_ca,
            counter_agg(collected_at, mem_pages_input::double precision) AS mem_pages_input_ca,
            counter_agg(collected_at, tcp_retrans_segs::double precision) AS tcp_retrans_ca,
            {extra_cols}
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
    _create(extra=True)


def downgrade() -> None:
    _drop()
    _create(extra=False)
