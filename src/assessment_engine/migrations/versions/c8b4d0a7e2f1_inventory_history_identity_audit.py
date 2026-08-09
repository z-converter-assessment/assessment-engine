"""inventory history에 composite_id와 machine_id를 보존한다.

Revision ID: c8b4d0a7e2f1
Revises: 9a7b86e0d10e
Create Date: 2026-08-08
"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c8b4d0a7e2f1"
down_revision: str | Sequence[str] | None = "9a7b86e0d10e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("server_inventory_history", sa.Column("composite_id", sa.String(length=64), nullable=True))
    op.add_column("server_inventory_history", sa.Column("machine_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("server_inventory_history", "machine_id")
    op.drop_column("server_inventory_history", "composite_id")
