"""server_mount_usage.major/minor 제거 — 에이전트 미발행 vestigial 컬럼

Revision ID: d4f6a8c0b2e5
Revises: c3e5a7b9d1f4
Create Date: 2026-07-04 00:00:00.000000

metrics mount(mount_usage) 는 사용량 전담 — 에이전트가 major/minor 를 발행하지 않는다(inventory mount 만 보유).
해당 컬럼은 항상 null 이고 read 경로 미사용(mount-disk 조인은 inventory mount 기준, cagg 는 major/minor 미참조)이라
제거. DROP COLUMN 은 메타데이터 연산(rewrite 없음). cagg server_mount_usage_5m 는 major/minor 를 참조하지 않아 무영향.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4f6a8c0b2e5"
down_revision: str | Sequence[str] | None = "c3e5a7b9d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("server_mount_usage", "major")
    op.drop_column("server_mount_usage", "minor")


def downgrade() -> None:
    op.add_column("server_mount_usage", sa.Column("minor", sa.Integer(), nullable=True))
    op.add_column("server_mount_usage", sa.Column("major", sa.Integer(), nullable=True))
