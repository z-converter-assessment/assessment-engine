"""server_disk_io_5m·server_net_io_5m continuous aggregate — counter_agg 사전집계

Revision ID: c8e2a4f6d1b3
Revises: b5d8f2a1c9e3
Create Date: 2026-06-24 17:30:00.000000

disk/net I/O baseline 도 매 보고서 7일치 raw 를 per-device/interface LAG 로 스캔하던 것을 counter_agg cagg 로
사전집계. 기존 LAG 산식은 boot_time reset gate 가 없어(단순 delta>=0) 재부팅 점프 delta 가 baseline·peak 에
섞일 수 있었다(weirdness) — counter_agg 가 값-감소 reset 을 일률 처리해 근본 제거(CPU 와 동일 정석).

가상 device/interface 는 cagg 단계에서 제외(물리만 집계) — types._PHYS_DISK_SQL_FILTER·_VIRTUAL_IFACE_SQL_FILTER
스냅샷. 필터 규약 변경 시 본 cagg 재생성 마이그레이션 동반.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e2a4f6d1b3"
down_revision: str | Sequence[str] | None = "b5d8f2a1c9e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 물리 디스크만 (loop/ram/dm/md/partition 제외 — _PHYS_DISK_SQL_FILTER 스냅샷).
    op.execute("""
        CREATE MATERIALIZED VIEW server_disk_io_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT server_id, device, time_bucket('5 minutes', collected_at) AS bucket,
            counter_agg(collected_at, reads_completed::double precision)  AS reads_ca,
            counter_agg(collected_at, writes_completed::double precision) AS writes_ca,
            counter_agg(collected_at, sectors_read::double precision)     AS sread_ca,
            counter_agg(collected_at, sectors_written::double precision)  AS swritten_ca
        FROM server_disk_io
        WHERE device !~ '^(loop[0-9]*|ram[0-9]*|zram[0-9]*|fd[0-9]*|sr[0-9]*|nbd[0-9]*)$'
          AND device !~ '^(dm-[0-9]+|md[0-9]+)$'
          AND device !~ '^(sd[a-z]+[0-9]+|vd[a-z]+[0-9]+|hd[a-z]+[0-9]+)$'
          AND device !~ '^(xvd[a-z]+[0-9]+|nvme[0-9]+n[0-9]+p[0-9]+|mmcblk[0-9]+p[0-9]+)$'
        GROUP BY server_id, device, bucket
        WITH NO DATA
    """)
    op.execute("""
        SELECT add_continuous_aggregate_policy('server_disk_io_5m',
            start_offset => INTERVAL '8 days', end_offset => INTERVAL '10 minutes',
            schedule_interval => INTERVAL '5 minutes')
    """)

    # 물리 interface만 (lo/veth/bridge/bond/vlan 제외 — _VIRTUAL_IFACE_SQL_FILTER 스냅샷). master/member 이중 집계 회피.
    op.execute(r"""
        CREATE MATERIALIZED VIEW server_net_io_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT server_id, interface, time_bucket('5 minutes', collected_at) AS bucket,
            counter_agg(collected_at, rx_bytes::double precision)   AS rx_ca,
            counter_agg(collected_at, tx_bytes::double precision)   AS tx_ca,
            counter_agg(collected_at, rx_packets::double precision) AS rxp_ca,
            counter_agg(collected_at, tx_packets::double precision) AS txp_ca
        FROM server_net_io
        WHERE interface !~ '^(lo|veth.*|sit[0-9]*|tunl[0-9]*|ip6tnl[0-9]*|gre[0-9]*)$'
          AND interface !~ '^(gretap[0-9]*|erspan[0-9]*|dummy[0-9]*|ifb[0-9]*|nlmon[0-9]*)$'
          AND interface !~ '^(bond[0-9]+|team[0-9]+|br[0-9]+|br-.+|docker[0-9]+|virbr[0-9]+(-nic)?)$'
          AND interface !~ '.+\.[0-9]+$'
          AND interface !~ '.+-[0-9]{4}$'
        GROUP BY server_id, interface, bucket
        WITH NO DATA
    """)
    op.execute("""
        SELECT add_continuous_aggregate_policy('server_net_io_5m',
            start_offset => INTERVAL '8 days', end_offset => INTERVAL '10 minutes',
            schedule_interval => INTERVAL '5 minutes')
    """)


def downgrade() -> None:
    op.execute("SELECT remove_continuous_aggregate_policy('server_net_io_5m', if_exists => true)")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS server_net_io_5m")
    op.execute("SELECT remove_continuous_aggregate_policy('server_disk_io_5m', if_exists => true)")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS server_disk_io_5m")
