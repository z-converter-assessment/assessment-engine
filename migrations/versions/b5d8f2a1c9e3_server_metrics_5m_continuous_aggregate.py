"""server_metrics_5m continuous aggregate — CPU(counter_agg)·mem·load·swap 5분 사전집계

Revision ID: b5d8f2a1c9e3
Revises: a7c3e5f1b9d4
Create Date: 2026-06-24 17:00:00.000000

right-sizing·보고서 집계가 매 요청 7일치 raw 를 LAG window 로 스캔하던 것을 TimescaleDB continuous
aggregate 로 사전집계한다. CPU jiffies(카운터)는 counter_agg 로 reset(재부팅·wraparound)을 값-감소 기준
일률 처리 — 기존 boot_time gate 의 disk/net 누락 불일치를 근본 제거(정석 통일). percentile 은 cagg 버킷에
쿼리 시 percentile_cont(정확) — tdigest 근사 불요. real-time aggregation(materialized_only=false)으로
미materialize 최근 구간은 실시간 집계 -> staleness 0. 5분 버킷 = 클라우드 right-sizing 표준 granularity.

초기 materialize(refresh_continuous_aggregate)는 트랜잭션 밖 전용이라 본 마이그레이션 밖에서 1회 실행
(real-time aggregation 이라 refresh 전에도 값은 정확, refresh 는 성능용).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5d8f2a1c9e3"
down_revision: str | Sequence[str] | None = "a7c3e5f1b9d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CPU total = 8 jiffies 합 (report_aggregate _CPU_TOTAL_EXPR 와 동일). mem%/swap = 시점값(카운터 아님).
    op.execute("""
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
            count(*) AS sample_count
        FROM server_metrics
        GROUP BY server_id, bucket
        WITH NO DATA
    """)
    # 5분마다 refresh. start_offset=8d(7일 윈도우 + 버퍼), end_offset=10m(미완 버킷은 real-time agg 위임).
    op.execute("""
        SELECT add_continuous_aggregate_policy('server_metrics_5m',
            start_offset => INTERVAL '8 days',
            end_offset => INTERVAL '10 minutes',
            schedule_interval => INTERVAL '5 minutes')
    """)


def downgrade() -> None:
    op.execute("SELECT remove_continuous_aggregate_policy('server_metrics_5m', if_exists => true)")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS server_metrics_5m")
