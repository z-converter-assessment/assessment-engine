"""net_io cagg 집계 대상에 bond_master 추가 (본딩 호스트 net 누락 방지)

Revision ID: b1d3f5a7c9e2
Revises: f1a3c5e7b9d2
Create Date: 2026-07-04 00:00:00.000000

server_net_io_5m 의 WHERE 를 kind='physical' -> kind IN ('physical','bond_master') 로 넓힌다.
본딩 호스트에서 실제 트래픽을 나르는 물리 NIC 은 bond_member, 집계 NIC 은 bond_master 로 태깅되는데, 둘 다
'physical' 이 아니라 kind='physical' 만 세면 그 호스트 net 이 통째로 0 으로 빠진다(누락). bond_master 는
/proc/net/dev 의 bondN 카운터로 슬레이브 합산 트래픽을 나르므로 집계 단위로 포함하고, bond_member 는 계속 제외
(bond_master 가 이미 합산분 -> 더하면 이중 집계). 런타임 필터 상수(_PHYS_IFACE_SQL_FILTER)·is_virtual_interface 와
동일 정의(physical + bond_master).

집계식(counter_agg)·버킷·정책 불변, WHERE 만 교체. cagg 는 drop 후 재생성(WITH NO DATA, real-time aggregation
이라 refresh 없이도 정확). 과거 버킷 materialize 가 필요하면 배포 후 1회 refresh_continuous_aggregate 를 트랜잭션
밖에서 실행(마이그레이션 외, ADR 0043/#C4). disk_io/mount cagg 는 불변(bond 무관).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1d3f5a7c9e2"
down_revision: str | Sequence[str] | None = "f1a3c5e7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIEW = "server_net_io_5m"


def _drop() -> None:
    op.execute(f"SELECT remove_continuous_aggregate_policy('{_VIEW}', if_exists => true)")
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {_VIEW}")


def _add_policy() -> None:
    op.execute(f"""
        SELECT add_continuous_aggregate_policy('{_VIEW}',
            start_offset => INTERVAL '8 days', end_offset => INTERVAL '10 minutes',
            schedule_interval => INTERVAL '5 minutes')
    """)


def _create(kind_filter: str) -> None:
    op.execute(f"""
        CREATE MATERIALIZED VIEW {_VIEW}
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT server_id, interface, time_bucket('5 minutes', collected_at) AS bucket,
            counter_agg(collected_at, rx_bytes::double precision)   AS rx_ca,
            counter_agg(collected_at, tx_bytes::double precision)   AS tx_ca,
            counter_agg(collected_at, rx_packets::double precision) AS rxp_ca,
            counter_agg(collected_at, tx_packets::double precision) AS txp_ca
        FROM server_net_io
        WHERE {kind_filter}
        GROUP BY server_id, interface, bucket
        WITH NO DATA
    """)


def upgrade() -> None:
    _drop()
    # 물리 + 본딩 집계 단위 — bond_member/loopback/bridge/veth/vlan/tunnel/virtual 제외.
    _create("kind IN ('physical', 'bond_master')")
    _add_policy()


def downgrade() -> None:
    _drop()
    _create("kind = 'physical'")
    _add_policy()
