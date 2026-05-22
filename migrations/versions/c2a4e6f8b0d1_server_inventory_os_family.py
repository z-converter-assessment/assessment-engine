"""server_inventory.os_family 컬럼 추가

Revision ID: c2a4e6f8b0d1
Revises: a1b2c3d4e5f6
Create Date: 2026-05-23 00:00:00.000000

agent 가 publish 하는 OS family (linux / windows) 명시 저장. task.install 발행 시
OS 별 dispatch (install.type / download.url / install.script) 의 단일 진실 (ADR 0020).

기존 row 는 'linux' backfill — 본 시점 Linux 호스트만 등록. nullable 유지 (Linux agent
minor bump 시점 호환). agent 측 배포 완료 후 별도 revision 에서 not-null tighten.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2a4e6f8b0d1"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "server_inventory",
        sa.Column("os_family", sa.String(length=16), nullable=True),
    )
    op.execute("UPDATE server_inventory SET os_family = 'linux' WHERE os_family IS NULL")


def downgrade() -> None:
    op.drop_column("server_inventory", "os_family")
