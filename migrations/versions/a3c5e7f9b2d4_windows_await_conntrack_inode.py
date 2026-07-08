"""ADR 0052 Phase 1 — Windows disk await 카운터 + conntrack ratio + inode used% (cagg 재생성)

Revision ID: a3c5e7f9b2d4
Revises: c7e9b1d3f5a2
Create Date: 2026-07-07 00:00:00.000000

3 신호를 baseline(report_aggregate)이 소비하도록 저장/집계 추가:
- Windows disk await: saturation.disk_queue[] IOCTL_DISK_PERFORMANCE ReadTime/WriteTime(100ns 누적)·
  ReadCount/WriteCount 를 device 합산해 server_metrics 4 컬럼 저장. server_metrics_5m 이 counter_agg 로
  집계(delta(time)/delta(count) = await, Linux time_reading/writing 등가). 큐 깊이 대체(같은 IOCTL, 커버리지 동일).
- conntrack: 저장 중이던 conntrack_count/max 를 ratio(count/max)로 집계(연결테이블 고갈 임박, 품질 신호).
- inode: server_mount_usage inodes_total/inodes_free 로 inode used% 집계(바이트 85% 정적 가드 대칭).

cagg 컬럼 ALTER 불가라 뷰 drop 후 재생성(WITH NO DATA, real-time #C4). 집계식·버킷·정책은
c7e9b1d3f5a2(server_metrics_5m)·f4a6c8e0b2d1(server_mount_usage_5m) 정의 그대로 두고 신 컬럼만 덧붙인다.
downgrade 는 두 뷰를 직전 정의로 복원 + 4 컬럼 제거.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c5e7f9b2d4"
down_revision: str | Sequence[str] | None = "c7e9b1d3f5a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Windows disk await 원자료 (device 합산, 100ns/count 누적). BigInteger nullable — Linux/구세대 viostor 는 NULL.
_AWAIT_COLS = (
    "sat_disk_read_time",
    "sat_disk_write_time",
    "sat_disk_read_count",
    "sat_disk_write_count",
)

_CPU_TOTAL = """counter_agg(collected_at, (COALESCE(cpu_user,0) + COALESCE(cpu_nice,0)
                + COALESCE(cpu_system,0) + COALESCE(cpu_idle,0) + COALESCE(cpu_iowait,0)
                + COALESCE(cpu_irq,0) + COALESCE(cpu_softirq,0) + COALESCE(cpu_steal,0))::double precision)"""

# Windows disk await counter_agg + conntrack ratio — extra=True(upgrade)면 추가, False(downgrade)면 c7e9 정의 복원.
# counter_agg 는 NULL 샘플을 건너뛰므로 (read_time+write_time) 이 NULL(Linux·구세대 viostor)이면 그 축 미관측 흡수.
_METRICS_EXTRA = """counter_agg(collected_at, (sat_disk_read_time + sat_disk_write_time)::double precision)   AS sat_disk_time_ca,
            counter_agg(collected_at, (sat_disk_read_count + sat_disk_write_count)::double precision)  AS sat_disk_count_ca,
            max(CASE WHEN conntrack_max > 0 AND conntrack_count IS NOT NULL
                     THEN conntrack_count::float / conntrack_max END)                                  AS conntrack_ratio_max,"""

# server_mount_usage_5m inode used% — extra=True 면 추가, False 면 f4a6 정의 복원.
_MOUNT_EXTRA = """,
            max(CASE WHEN inodes_total > 0 AND inodes_free IS NOT NULL
                     THEN (1 - inodes_free::float / inodes_total) * 100 END) AS inode_used_pct_max"""


def _policy(view: str) -> str:
    return f"""
        SELECT add_continuous_aggregate_policy('{view}',
            start_offset => INTERVAL '8 days', end_offset => INTERVAL '10 minutes',
            schedule_interval => INTERVAL '5 minutes')
    """


def _drop(view: str) -> None:
    op.execute(f"SELECT remove_continuous_aggregate_policy('{view}', if_exists => true)")
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view}")


def _create_metrics(*, extra: bool) -> None:
    extra_cols = _METRICS_EXTRA if extra else ""
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
            avg(procs_running)                                     AS procs_running_avg,
            counter_agg(collected_at, oom_kill::double precision) AS oom_kill_ca,
            {extra_cols}
            count(*) AS sample_count
        FROM server_metrics
        GROUP BY server_id, bucket
        WITH NO DATA
    """)
    op.execute(_policy("server_metrics_5m"))


def _create_mount(*, extra: bool) -> None:
    extra_col = _MOUNT_EXTRA if extra else ""
    op.execute(f"""
        CREATE MATERIALIZED VIEW server_mount_usage_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT server_id, mount, time_bucket('5 minutes', collected_at) AS bucket,
            max((1 - avail_bytes::float / total_bytes) * 100) AS used_pct_max,
            avg((1 - avail_bytes::float / total_bytes) * 100) AS used_pct_avg,
            max(total_bytes)                                  AS total_bytes_max,
            first(avail_bytes, collected_at)                 AS avail_first,
            last(avail_bytes, collected_at)                  AS avail_last,
            first(inodes_free, collected_at)                 AS inodes_free_first,
            last(inodes_free, collected_at)                  AS inodes_free_last{extra_col}
        FROM server_mount_usage
        WHERE total_bytes > 0 AND avail_bytes IS NOT NULL AND kind = 'data'
        GROUP BY server_id, mount, bucket
        WITH NO DATA
    """)
    op.execute(_policy("server_mount_usage_5m"))


def upgrade() -> None:
    for col in _AWAIT_COLS:
        op.add_column("server_metrics", sa.Column(col, sa.BigInteger(), nullable=True))
    _drop("server_metrics_5m")
    _create_metrics(extra=True)
    _drop("server_mount_usage_5m")
    _create_mount(extra=True)


def downgrade() -> None:
    _drop("server_mount_usage_5m")
    _create_mount(extra=False)
    _drop("server_metrics_5m")
    _create_metrics(extra=False)
    for col in reversed(_AWAIT_COLS):
        op.drop_column("server_metrics", col)
