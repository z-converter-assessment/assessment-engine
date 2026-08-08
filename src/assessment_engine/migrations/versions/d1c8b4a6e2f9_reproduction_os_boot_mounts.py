"""reproduction 재현 서술자 컬럼 추가 (os arch/boot_firmware 등 + boot 노드 + nonblock_mounts)

agent 가 발행하나 엔진이 미배선이던 재현 필드를 server_inventory 에 저장 — assessment API reproduction 완전화.

Revision ID: d1c8b4a6e2f9
Revises: a2f4c6e8d0b1
Create Date: 2026-07-10

"""

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d1c8b4a6e2f9"
down_revision: str | Sequence[str] | None = "a2f4c6e8d0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = ("server_inventory", "server_inventory_history")


def _columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("arch", sa.String(length=32), nullable=True),
        sa.Column("bits", sa.Integer(), nullable=True),
        sa.Column("boot_firmware", sa.String(length=8), nullable=True),
        sa.Column("secure_boot", sa.Boolean(), nullable=True),
        sa.Column("edition", sa.String(length=64), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("rtc_utc", sa.Boolean(), nullable=True),
        sa.Column("boot", JSONB(), nullable=True),
        sa.Column("nonblock_mounts", JSONB(), nullable=True),
    ]


def upgrade() -> None:
    for table in _TABLES:
        for col in _columns():
            op.add_column(table, col)


def downgrade() -> None:
    for table in _TABLES:
        for col in reversed(_columns()):
            op.drop_column(table, col.name)
