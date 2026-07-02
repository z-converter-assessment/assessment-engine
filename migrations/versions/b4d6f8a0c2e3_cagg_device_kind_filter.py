"""cagg 물리/데이터 필터를 device kind 로 재정의 (정규식·major -> kind)

Revision ID: b4d6f8a0c2e3
Revises: a1b3c5d7e9f2
Create Date: 2026-07-01 00:00:02.000000

server_disk_io_5m·server_net_io_5m·server_mount_usage_5m 의 물리/데이터 필터를 device `kind` 태그로 교체한다.
- disk_io_5m: device 정규식(loop/dm/partition 제외) -> kind = 'physical'
- net_io_5m:  interface 정규식(lo/veth/bridge/bond/vlan 제외) -> kind = 'physical'
- mount_usage_5m: major/드라이브/부트 path -> kind = 'data'

집계식(counter_agg·max/avg/first/last)·버킷·정책은 불변, WHERE 만 교체. cagg 는 drop 후 재생성(WITH NO DATA,
real-time aggregation 이라 refresh 없이도 정확). breaking cutover(DB 초기화) 전제라 데이터 백필 없음.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d6f8a0c2e3"
down_revision: str | Sequence[str] | None = "a1b3c5d7e9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIEWS = ("server_disk_io_5m", "server_net_io_5m", "server_mount_usage_5m")


def _drop_caggs() -> None:
    for view in _VIEWS:
        op.execute(f"SELECT remove_continuous_aggregate_policy('{view}', if_exists => true)")
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view}")


def _add_policies() -> None:
    for view in _VIEWS:
        op.execute(f"""
            SELECT add_continuous_aggregate_policy('{view}',
                start_offset => INTERVAL '8 days', end_offset => INTERVAL '10 minutes',
                schedule_interval => INTERVAL '5 minutes')
        """)


def upgrade() -> None:
    _drop_caggs()
    # 물리 디스크만 — kind = 'physical' (partition/lvm/raid/virtual 제외).
    op.execute("""
        CREATE MATERIALIZED VIEW server_disk_io_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT server_id, device, time_bucket('5 minutes', collected_at) AS bucket,
            counter_agg(collected_at, reads_completed::double precision)  AS reads_ca,
            counter_agg(collected_at, writes_completed::double precision) AS writes_ca,
            counter_agg(collected_at, sectors_read::double precision)     AS sread_ca,
            counter_agg(collected_at, sectors_written::double precision)  AS swritten_ca
        FROM server_disk_io
        WHERE kind = 'physical'
        GROUP BY server_id, device, bucket
        WITH NO DATA
    """)
    # 물리 interface만 — kind = 'physical' (loopback/bridge/veth/bond/vlan/tunnel/virtual 제외).
    op.execute("""
        CREATE MATERIALIZED VIEW server_net_io_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT server_id, interface, time_bucket('5 minutes', collected_at) AS bucket,
            counter_agg(collected_at, rx_bytes::double precision)   AS rx_ca,
            counter_agg(collected_at, tx_bytes::double precision)   AS tx_ca,
            counter_agg(collected_at, rx_packets::double precision) AS rxp_ca,
            counter_agg(collected_at, tx_packets::double precision) AS txp_ca
        FROM server_net_io
        WHERE kind = 'physical'
        GROUP BY server_id, interface, bucket
        WITH NO DATA
    """)
    # 데이터 볼륨만 — kind = 'data' (boot/image/가상 fs 제외).
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
        WHERE total_bytes > 0 AND avail_bytes IS NOT NULL AND kind = 'data'
        GROUP BY server_id, mount, bucket
        WITH NO DATA
    """)
    _add_policies()


def downgrade() -> None:
    _drop_caggs()
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
    _add_policies()
