"""server_inventory_history.os_family 컬럼 추가

Revision ID: c3a5e7f9b1d2
Revises: c2a4e6f8b0d1
Create Date: 2026-05-23 00:01:00.000000

server_inventory.os_family 도입 (c2a4e6f8b0d1) 의 history mirror. inventory upsert 시
변경 detect → history INSERT 분기에서 os_family 도 같이 기록.

기존 row 는 'linux' backfill. nullable 유지 (server_inventory 와 일관).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3a5e7f9b1d2"
down_revision: str | Sequence[str] | None = "c2a4e6f8b0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "server_inventory_history",
        sa.Column("os_family", sa.String(length=16), nullable=True),
    )
    op.execute("UPDATE server_inventory_history SET os_family = 'linux' WHERE os_family IS NULL")


def downgrade() -> None:
    op.drop_column("server_inventory_history", "os_family")
