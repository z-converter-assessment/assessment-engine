"""b3 server_metrics_5m mem byte gauges for env_util cagg

env_utilization·report_memory_breakdown 의 raw server_metrics 스캔을 cagg 로 이관하기 위해
server_metrics_5m 재생성 — mem byte gauge(capacity-weighting 용) + cached/buffered pct 추가.
측정(70대 x 14일): env_util mem per-ts p95 raw 476ms -> cagg 98ms (약 5배).

cagg 는 ALTER 로 컬럼 추가 불가라 DROP + CREATE 재생성. 기존 컬럼 전부 보존 + mem 4 컬럼만 추가.
#C4: 정의+policy 는 마이그레이션 트랜잭션 내, 초기 materialize(refresh)는 트랜잭션 밖(autocommit_block) 1회.

Revision ID: f6f365fd114c
Revises: 1457793ebdae
Create Date: 2026-07-21

"""

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f6f365fd114c"
down_revision: str | Sequence[str] | None = "1457793ebdae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Windows COALESCE — a2f4c6e8d0b1 과 동일 (cpu total NULL 방지).
_CPU_TOTAL_S = (
    "COALESCE(cpu_user_s,0) + COALESCE(cpu_nice_s,0) + COALESCE(cpu_system_s,0) + COALESCE(cpu_idle_s,0) "
    "+ COALESCE(cpu_iowait_s,0) + COALESCE(cpu_irq_s,0) + COALESCE(cpu_softirq_s,0) + COALESCE(cpu_steal_s,0)"
)

# 기존 컬럼 (a2f4c6e8d0b1 정의와 동일 — 재생성이라 전부 보존).
_COMMON_COLS = f"""
        server_id,
        time_bucket('5 minutes', collected_at) AS bucket,
        counter_agg(collected_at, ({_CPU_TOTAL_S}))                     AS cpu_total_ca,
        counter_agg(collected_at, cpu_idle_s)                          AS cpu_idle_ca,
        counter_agg(collected_at, cpu_user_s)                          AS cpu_user_ca,
        counter_agg(collected_at, cpu_system_s)                        AS cpu_system_ca,
        counter_agg(collected_at, cpu_iowait_s)                        AS cpu_iowait_ca,
        counter_agg(collected_at, cpu_steal_s)                         AS cpu_steal_ca,
        counter_agg(collected_at, paging_major::double precision)      AS paging_major_ca,
        counter_agg(collected_at, paging_out::double precision)        AS paging_out_ca,
        counter_agg(collected_at, paging_in::double precision)         AS paging_in_ca,
        counter_agg(collected_at, mem_oom_kill::double precision)      AS oom_kill_ca,
        counter_agg(collected_at, net_tcp_retransmits::double precision) AS tcp_retrans_ca,
        counter_agg(collected_at, cpu_mce::double precision)           AS cpu_mce_ca,
        avg(CASE WHEN mem_limit_bytes > 0 AND mem_available_bytes IS NOT NULL
                 THEN (1 - mem_available_bytes::float / mem_limit_bytes) * 100 END) AS mem_pct_avg,
        max(CASE WHEN mem_limit_bytes > 0 AND mem_available_bytes IS NOT NULL
                 THEN (1 - mem_available_bytes::float / mem_limit_bytes) * 100 END) AS mem_pct_max,
        count(CASE WHEN mem_limit_bytes > 0 AND mem_available_bytes IS NOT NULL THEN 1 END) AS mem_sample,
        avg(CASE WHEN mem_commit_limit_bytes > 0 AND mem_commit_usage_bytes IS NOT NULL
                 THEN mem_commit_usage_bytes::float / mem_commit_limit_bytes * 100 END) AS commit_pct_avg,
        max(CASE WHEN mem_commit_limit_bytes > 0 AND mem_commit_usage_bytes IS NOT NULL
                 THEN mem_commit_usage_bytes::float / mem_commit_limit_bytes * 100 END) AS commit_pct_max,
        avg(cpu_run_queue) AS run_queue_avg,
        max(cpu_run_queue) AS run_queue_max,
        avg(cpu_blocked)   AS blocked_avg,
        max(cpu_blocked)   AS blocked_max,
        avg(CASE WHEN net_conntrack_limit > 0 AND net_conntrack_usage IS NOT NULL
                 THEN net_conntrack_usage::float / net_conntrack_limit END) AS conntrack_ratio_avg,
        max(CASE WHEN net_conntrack_limit > 0 AND net_conntrack_usage IS NOT NULL
                 THEN net_conntrack_usage::float / net_conntrack_limit END) AS conntrack_ratio_max,
        max(mem_hardware_corrupted_bytes) AS hw_corrupted_max"""

# B3 추가 — env_util capacity-weighting(sum(bytes)) + memory_breakdown cached/buffered%.
# available/limit = byte avg(서버 합산으로 capacity-weight), cached/buffered = pct avg(mem_pct_avg 규약과 동형).
_B3_MEM_COLS = """,
        avg(CASE WHEN mem_limit_bytes > 0 AND mem_available_bytes IS NOT NULL
                 THEN mem_available_bytes END) AS mem_available_avg,
        avg(CASE WHEN mem_limit_bytes > 0 THEN mem_limit_bytes END) AS mem_limit_avg,
        avg(CASE WHEN mem_limit_bytes > 0 AND mem_cached_bytes IS NOT NULL
                 THEN mem_cached_bytes::float / mem_limit_bytes * 100 END) AS mem_cached_pct_avg,
        avg(CASE WHEN mem_limit_bytes > 0 AND mem_buffered_bytes IS NOT NULL
                 THEN mem_buffered_bytes::float / mem_limit_bytes * 100 END) AS mem_buffered_pct_avg"""

_POLICY = """
    SELECT add_continuous_aggregate_policy('server_metrics_5m',
        start_offset => INTERVAL '16 days',
        end_offset => INTERVAL '10 minutes',
        schedule_interval => INTERVAL '5 minutes')
"""


def _create_sql(with_b3: bool) -> str:
    extra = _B3_MEM_COLS if with_b3 else ""
    return f"""
        CREATE MATERIALIZED VIEW server_metrics_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT{_COMMON_COLS}{extra},
            count(*) AS sample_count
        FROM server_metrics
        GROUP BY server_id, bucket
        WITH NO DATA
    """


def _recreate(with_b3: bool) -> None:
    op.execute("DROP MATERIALIZED VIEW server_metrics_5m CASCADE")
    op.execute(_create_sql(with_b3))
    # #C4: refresh 는 트랜잭션 밖 1회 — autocommit_block 이 현 txn 커밋 후 autocommit 으로 실행.
    # 재생성이라 cagg 비어 있음 -> 기존 raw 데이터 backfill(정책 첫 실행 전 즉시 가용).
    # policy 는 refresh 후 추가 — policy 백그라운드 job 과 수동 refresh 동시 실행(락 충돌) 회피.
    with op.get_context().autocommit_block():
        op.execute("CALL refresh_continuous_aggregate('server_metrics_5m', NULL, NULL)")
    op.execute(_POLICY)


def upgrade() -> None:
    _recreate(with_b3=True)


def downgrade() -> None:
    _recreate(with_b3=False)
