"""server_metrics_5m cagg — cpu_total COALESCE 정규화 + cpu_run_queue/mem_paging saturation 집계

Revision ID: f1a3c5e7b9d2
Revises: e8b4d2f6a1c9
Create Date: 2026-07-02 00:00:00.000000

세 가지를 한 번의 cagg 재생성으로 반영:
- C2: cpu_total_ca 를 성분 COALESCE 합으로. Windows 는 nice/iowait/irq/softirq/steal 이 null 이라
  기존 (cpu_user + ... + cpu_steal) 이 SQL X+NULL=NULL -> cpu_total_ca null -> CPU 파생 전량 붕괴였다.
  COALESCE(성분,0) 합 = Windows total(user+system+idle) 정확. Linux 는 전 성분 실측이라 결과 동일.
- S1: avg(sat_cpu_run_queue) AS cpu_run_queue_avg. Windows CPU saturation(Processor Queue Length) 집계.
  Linux 는 sat_cpu_run_queue null -> cpu_run_queue_avg null (load 축 사용 유지).
- S2: counter_agg(sat_mem_paging_rate) AS mem_paging_ca. Windows Memory Pages/sec 는 누적 counter라
  delta()/time_delta() 로 rate 환산 (cpu jiffies·disk/net bytes 와 동급). Linux 는 null -> mem_paging_ca null.

cagg drop 후 재생성(WITH NO DATA, real-time aggregation). DB 초기화 전제라 백필 없음. (#C4)
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a3c5e7b9d2"
down_revision: str | Sequence[str] | None = "e8b4d2f6a1c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = """
    SELECT add_continuous_aggregate_policy('server_metrics_5m',
        start_offset => INTERVAL '8 days', end_offset => INTERVAL '10 minutes',
        schedule_interval => INTERVAL '5 minutes')
"""

# cpu_total_ca 성분 합 — with_coalesce=True 면 COALESCE(성분,0)(#C2), False 면 raw 합(옛 정의 복원).
_CPU_TOTAL_COALESCE = """counter_agg(collected_at, (COALESCE(cpu_user,0) + COALESCE(cpu_nice,0)
                + COALESCE(cpu_system,0) + COALESCE(cpu_idle,0) + COALESCE(cpu_iowait,0)
                + COALESCE(cpu_irq,0) + COALESCE(cpu_softirq,0) + COALESCE(cpu_steal,0))::double precision)"""
_CPU_TOTAL_RAW = """counter_agg(collected_at, (cpu_user + cpu_nice + cpu_system + cpu_idle
                + cpu_iowait + cpu_irq + cpu_softirq + cpu_steal)::double precision)"""

# saturation 집계 컬럼 — with_saturation=True(신규)면 cpu_run_queue_avg(S1)·mem_paging_ca(S2) 추가.
_SATURATION_COLS = """avg(sat_cpu_run_queue) AS cpu_run_queue_avg,
            counter_agg(collected_at, sat_mem_paging_rate::double precision) AS mem_paging_ca,"""


def _create(*, with_coalesce: bool, with_saturation: bool) -> None:
    cpu_total = _CPU_TOTAL_COALESCE if with_coalesce else _CPU_TOTAL_RAW
    saturation = _SATURATION_COLS if with_saturation else ""
    op.execute(f"""
        CREATE MATERIALIZED VIEW server_metrics_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT
            server_id,
            time_bucket('5 minutes', collected_at) AS bucket,
            {cpu_total} AS cpu_total_ca,
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
            {saturation}
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
    _create(with_coalesce=True, with_saturation=True)


def downgrade() -> None:
    _drop()
    _create(with_coalesce=False, with_saturation=False)
