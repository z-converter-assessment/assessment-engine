"""v2 continuous aggregates

wire v2 스키마 위 5분 버킷 continuous aggregate 5종 (T3). 단위 canonical(seconds/bytes) counter_agg +
게이지 avg/max. 물리/가상·bond 필터는 cagg 에 박지 않고 query 시 inventory 조인으로 선별 — 필터 규약 변경
시 cagg 재생성 불요(#C4 고통 해소). p95 는 query 시 버킷 위에서 percentile.

확정 진단모델(Gate0) 신호 반영: cpu 시간·steal·run_queue·blocked·mce, paging_major/out/in, oom_kill,
conntrack ratio, tcp 재전송, net drops/errors, hw_corrupted, commit(Windows). PSI 는 Deferred(cagg 미생성).

Revision ID: a2f4c6e8d0b1
Revises: 53df4c2132fd
Create Date: 2026-07-09

"""

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a2f4c6e8d0b1"
down_revision: str | Sequence[str] | None = "53df4c2132fd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 5종 cagg — refresh 정책 공통(start_offset = WINDOW_DAYS(14) + 버퍼, end_offset 10m real-time 위임).
_CAGGS = ("server_metrics_5m", "server_filesystem_5m", "server_disk_io_5m", "server_net_io_5m", "server_cpu_core_5m")

# Windows 는 iowait/nice/irq/softirq/steal jiffie 개념 부재라 해당 컬럼 NULL — NULL+x=NULL 로 total 전체가
# NULL 이 되면 cpu_total_ca NULL -> util 산출 불가(Windows CPU 판정 블라인드). COALESCE 로 성분별 0 처리해
# Windows total = user+system+idle 로 성립하게. (Linux 는 전 성분 실측이라 무영향.)
_CPU_TOTAL_S = (
    "COALESCE(cpu_user_s,0) + COALESCE(cpu_nice_s,0) + COALESCE(cpu_system_s,0) + COALESCE(cpu_idle_s,0) "
    "+ COALESCE(cpu_iowait_s,0) + COALESCE(cpu_irq_s,0) + COALESCE(cpu_softirq_s,0) + COALESCE(cpu_steal_s,0)"
)


def upgrade() -> None:
    # counter_agg(reset-safe delta) — timescaledb_toolkit. CASCADE 로 의존 객체 동시.
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit CASCADE")

    # --- server_metrics_5m (host 집계) ---
    op.execute(f"""
        CREATE MATERIALIZED VIEW server_metrics_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT
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
            max(mem_hardware_corrupted_bytes) AS hw_corrupted_max,
            count(*) AS sample_count
        FROM server_metrics
        GROUP BY server_id, bucket
        WITH NO DATA
    """)

    # --- server_filesystem_5m (마운트별 용량·inode) ---
    op.execute("""
        CREATE MATERIALIZED VIEW server_filesystem_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT
            server_id, mountpoint,
            time_bucket('5 minutes', collected_at) AS bucket,
            max(CASE WHEN (used_bytes + free_bytes) > 0
                     THEN used_bytes::float / (used_bytes + free_bytes) * 100 END) AS used_pct_max,
            avg(CASE WHEN (used_bytes + free_bytes) > 0
                     THEN used_bytes::float / (used_bytes + free_bytes) * 100 END) AS used_pct_avg,
            max(used_bytes + free_bytes)     AS total_bytes_max,
            first(free_bytes, collected_at)  AS free_first,
            last(free_bytes, collected_at)   AS free_last,
            max(CASE WHEN (inodes_used + inodes_free) > 0
                     THEN inodes_used::float / (inodes_used + inodes_free) * 100 END) AS inode_pct_max,
            first(inodes_free, collected_at) AS inode_free_first,
            last(inodes_free, collected_at)  AS inode_free_last,
            last(fstype, collected_at)       AS fstype_any,
            count(*) AS sample_count
        FROM server_filesystem
        GROUP BY server_id, mountpoint, bucket
        WITH NO DATA
    """)

    # --- server_disk_io_5m (디바이스별 IO) ---
    op.execute("""
        CREATE MATERIALIZED VIEW server_disk_io_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT
            server_id, device_id,
            time_bucket('5 minutes', collected_at) AS bucket,
            counter_agg(collected_at, io_read_bytes::double precision)  AS io_read_ca,
            counter_agg(collected_at, io_write_bytes::double precision) AS io_write_ca,
            counter_agg(collected_at, ops_read::double precision)       AS ops_read_ca,
            counter_agg(collected_at, ops_write::double precision)      AS ops_write_ca,
            counter_agg(collected_at, op_read_time_s)                   AS op_rtime_ca,
            counter_agg(collected_at, op_write_time_s)                  AS op_wtime_ca,
            counter_agg(collected_at, io_time_s)                        AS io_time_ca,
            avg(pending_ops) AS pending_avg,
            max(pending_ops) AS pending_max,
            count(*) AS sample_count
        FROM server_disk_io
        GROUP BY server_id, device_id, bucket
        WITH NO DATA
    """)

    # --- server_net_io_5m (인터페이스별) ---
    op.execute("""
        CREATE MATERIALIZED VIEW server_net_io_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT
            server_id, iface_id,
            time_bucket('5 minutes', collected_at) AS bucket,
            counter_agg(collected_at, rx_bytes::double precision)   AS rx_ca,
            counter_agg(collected_at, tx_bytes::double precision)   AS tx_ca,
            counter_agg(collected_at, rx_packets::double precision) AS rxp_ca,
            counter_agg(collected_at, tx_packets::double precision) AS txp_ca,
            counter_agg(collected_at, rx_dropped::double precision) AS rxd_ca,
            counter_agg(collected_at, tx_dropped::double precision) AS txd_ca,
            counter_agg(collected_at, rx_errors::double precision)  AS rxe_ca,
            counter_agg(collected_at, tx_errors::double precision)  AS txe_ca,
            max(link_speed_bps) AS link_max,
            count(*) AS sample_count
        FROM server_net_io
        GROUP BY server_id, iface_id, bucket
        WITH NO DATA
    """)

    # --- server_cpu_core_5m (per-core 이용률) ---
    op.execute(f"""
        CREATE MATERIALIZED VIEW server_cpu_core_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT
            server_id, core_id,
            time_bucket('5 minutes', collected_at) AS bucket,
            counter_agg(collected_at, ({_CPU_TOTAL_S})) AS cpu_total_ca,
            counter_agg(collected_at, cpu_idle_s)       AS cpu_idle_ca,
            count(*) AS sample_count
        FROM server_cpu_core
        GROUP BY server_id, core_id, bucket
        WITH NO DATA
    """)

    # refresh 정책 (5종 공통). start_offset 16d = WINDOW_DAYS(14) + 버퍼, end_offset 10m real-time 위임.
    for cagg in _CAGGS:
        op.execute(f"""
            SELECT add_continuous_aggregate_policy('{cagg}',
                start_offset => INTERVAL '16 days',
                end_offset => INTERVAL '10 minutes',
                schedule_interval => INTERVAL '5 minutes')
        """)
    # fresh cutover DB 는 초기 데이터 0 -> refresh_continuous_aggregate 불요(정책이 도착분 materialize).


def downgrade() -> None:
    for cagg in reversed(_CAGGS):
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {cagg} CASCADE")
