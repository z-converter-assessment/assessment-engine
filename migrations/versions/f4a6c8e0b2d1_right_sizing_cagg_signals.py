"""ADR 0052 신 신호 cagg 집계 — 4개 continuous aggregate 재생성

Revision ID: f4a6c8e0b2d1
Revises: e6b8d0f2a4c7
Create Date: 2026-07-06 00:00:00.000000

자원 적정성 재설계(ADR 0052)가 report_aggregate 에서 소비할 신 raw 신호를 각 cagg 집계 컬럼으로 추가한다.
cagg 컬럼은 ALTER 불가라 4개 뷰를 drop 후 재생성(WITH NO DATA, real-time aggregation 이라 refresh 없이도
정확 — #C4/ADR 0043). 집계식·버킷·정책은 기존 정의를 그대로 두고 신 컬럼만 덧붙인다(additive).

- server_metrics_5m: cpu_steal_ca(steal% p95), procs_blocked_avg(D-state p95 입력, gauge라 avg),
  pswpout_ca·mem_pages_input_ca(mem_swap_paging bool_or — Linux swap page-out / Windows hard page-in,
  서로 배타적 null), tcp_retrans_ca(재전송률 분자). 전부 카운터 counter_agg, procs_blocked 만 gauge avg.
- server_disk_io_5m: treading_ca·twriting_ca(await = time delta / io delta, 물리 device 한정). Linux 실측
  (Windows disk_io 시간필드 미발행 -> null -> await null -> 포화 미관측).
- server_net_io_5m: rxd_ca·txd_ca(드롭률 분자, 분모는 기존 rxp_ca/txp_ca).
- server_mount_usage_5m: inodes_free_first·inodes_free_last(inode runway = avail 바이트와 동일 fill_rate).
  Windows 는 statvfs 부재로 inodes_free null -> first/last null -> inode runway 없음.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4a6c8e0b2d1"
down_revision: str | Sequence[str] | None = "e6b8d0f2a4c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─── 정책 (기존 정의와 동일 — 뷰별 8일 창 5분 스케줄) ───
def _policy(view: str) -> str:
    return f"""
        SELECT add_continuous_aggregate_policy('{view}',
            start_offset => INTERVAL '8 days', end_offset => INTERVAL '10 minutes',
            schedule_interval => INTERVAL '5 minutes')
    """


def _drop(view: str) -> None:
    op.execute(f"SELECT remove_continuous_aggregate_policy('{view}', if_exists => true)")
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view}")


# ─── server_metrics_5m — cpu total COALESCE 합(#C2) + saturation(S1/S2) 기존 정의 유지, 신 신호 컬럼 추가 ───
_CPU_TOTAL = """counter_agg(collected_at, (COALESCE(cpu_user,0) + COALESCE(cpu_nice,0)
                + COALESCE(cpu_system,0) + COALESCE(cpu_idle,0) + COALESCE(cpu_iowait,0)
                + COALESCE(cpu_irq,0) + COALESCE(cpu_softirq,0) + COALESCE(cpu_steal,0))::double precision)"""

# ADR 0052 신 신호 집계 컬럼 — rs=True(upgrade)면 추가, rs=False(downgrade)면 옛 정의 복원.
_METRICS_RS_COLS = """counter_agg(collected_at, cpu_steal::double precision)        AS cpu_steal_ca,
            avg(procs_blocked)                                          AS procs_blocked_avg,
            counter_agg(collected_at, pswpout::double precision)        AS pswpout_ca,
            counter_agg(collected_at, mem_pages_input::double precision) AS mem_pages_input_ca,
            counter_agg(collected_at, tcp_retrans_segs::double precision) AS tcp_retrans_ca,"""


def _create_metrics(*, rs: bool) -> None:
    rs_cols = _METRICS_RS_COLS if rs else ""
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
            {rs_cols}
            count(*) AS sample_count
        FROM server_metrics
        GROUP BY server_id, bucket
        WITH NO DATA
    """)
    op.execute(_policy("server_metrics_5m"))


# ─── server_disk_io_5m — 물리 device counter_agg, await 원자료(time_reading/writing) 추가 ───
def _create_disk_io(*, rs: bool) -> None:
    rs_cols = (
        """,
            counter_agg(collected_at, time_reading_ms::double precision) AS treading_ca,
            counter_agg(collected_at, time_writing_ms::double precision) AS twriting_ca"""
        if rs
        else ""
    )
    op.execute(f"""
        CREATE MATERIALIZED VIEW server_disk_io_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT server_id, device, time_bucket('5 minutes', collected_at) AS bucket,
            counter_agg(collected_at, reads_completed::double precision)  AS reads_ca,
            counter_agg(collected_at, writes_completed::double precision) AS writes_ca,
            counter_agg(collected_at, sectors_read::double precision)     AS sread_ca,
            counter_agg(collected_at, sectors_written::double precision)  AS swritten_ca{rs_cols}
        FROM server_disk_io
        WHERE kind = 'physical'
        GROUP BY server_id, device, bucket
        WITH NO DATA
    """)
    op.execute(_policy("server_disk_io_5m"))


# ─── server_net_io_5m — 물리+bond_master interface counter_agg, 드롭 원자료 추가 ───
def _create_net_io(*, rs: bool) -> None:
    rs_cols = (
        """,
            counter_agg(collected_at, rx_drops::double precision) AS rxd_ca,
            counter_agg(collected_at, tx_drops::double precision) AS txd_ca"""
        if rs
        else ""
    )
    op.execute(f"""
        CREATE MATERIALIZED VIEW server_net_io_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT server_id, interface, time_bucket('5 minutes', collected_at) AS bucket,
            counter_agg(collected_at, rx_bytes::double precision)   AS rx_ca,
            counter_agg(collected_at, tx_bytes::double precision)   AS tx_ca,
            counter_agg(collected_at, rx_packets::double precision) AS rxp_ca,
            counter_agg(collected_at, tx_packets::double precision) AS txp_ca{rs_cols}
        FROM server_net_io
        WHERE kind IN ('physical', 'bond_master')
        GROUP BY server_id, interface, bucket
        WITH NO DATA
    """)
    op.execute(_policy("server_net_io_5m"))


# ─── server_mount_usage_5m — 데이터 볼륨 사용률 + inode first/last(runway) 추가 ───
def _create_mount(*, rs: bool) -> None:
    rs_cols = (
        """,
            first(inodes_free, collected_at) AS inodes_free_first,
            last(inodes_free, collected_at)  AS inodes_free_last"""
        if rs
        else ""
    )
    op.execute(f"""
        CREATE MATERIALIZED VIEW server_mount_usage_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT server_id, mount, time_bucket('5 minutes', collected_at) AS bucket,
            max((1 - avail_bytes::float / total_bytes) * 100) AS used_pct_max,
            avg((1 - avail_bytes::float / total_bytes) * 100) AS used_pct_avg,
            max(total_bytes)                                  AS total_bytes_max,
            first(avail_bytes, collected_at)                 AS avail_first,
            last(avail_bytes, collected_at)                  AS avail_last{rs_cols}
        FROM server_mount_usage
        WHERE total_bytes > 0 AND avail_bytes IS NOT NULL AND kind = 'data'
        GROUP BY server_id, mount, bucket
        WITH NO DATA
    """)
    op.execute(_policy("server_mount_usage_5m"))


_VIEWS = ("server_metrics_5m", "server_disk_io_5m", "server_net_io_5m", "server_mount_usage_5m")


def upgrade() -> None:
    for v in _VIEWS:
        _drop(v)
    _create_metrics(rs=True)
    _create_disk_io(rs=True)
    _create_net_io(rs=True)
    _create_mount(rs=True)


def downgrade() -> None:
    for v in _VIEWS:
        _drop(v)
    _create_metrics(rs=False)
    _create_disk_io(rs=False)
    _create_net_io(rs=False)
    _create_mount(rs=False)
