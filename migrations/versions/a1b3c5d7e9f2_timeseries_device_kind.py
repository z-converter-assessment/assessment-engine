"""server_disk_io/net_io/mount_usage.kind 추가 — agent device 분류 태그 저장

Revision ID: a1b3c5d7e9f2
Revises: f2a4c6e8b1d3
Create Date: 2026-07-01 00:00:01.000000

agent 가 disk_io/net_io/mounts 각 항목에 `kind` 를 발행한다 (공용 분류기):
- disk: physical/partition/lvm/raid/virtual
- net:  physical/loopback/bridge/veth/bond_master/bond_member/vlan/tunnel/virtual (Windows coarse: physical/loopback/tunnel/virtual)
- mount: data/boot/image (가상 fs 는 agent pre-drop)

kind 저장만 추가 (필터 전환·cagg 재정의는 후속 리비전). 물리/데이터 판정을 정규식/major 대신
kind 단일 신호로 이관하기 위한 토대. nullable — placeholder metrics 행 호환.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b3c5d7e9f2"
down_revision: str | Sequence[str] | None = "f2a4c6e8b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("server_disk_io", "server_net_io", "server_mount_usage")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("kind", sa.String(length=32), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "kind")
