"""server_mount_usage 에 major/minor 추가 (data-volume 판단 신호)

Revision ID: a9f3c7e1d5b2
Revises: e7a2c9d4f1b6
Create Date: 2026-06-05 08:00:00.000000

agent 가 mount 메트릭에 발행하던 major/minor 를 시계열 테이블에 저장한다.
major==0 = 블록 디바이스 없는 가상 fs — 데이터 볼륨 판단(device_filters.is_data_volume) 단일 신호로 활용.
nullable — 마이그레이션 전 기존 행은 NULL(agent 재발행 시 채워짐), SQL 필터는 path fallback 로 안전.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9f3c7e1d5b2"
down_revision: str | Sequence[str] | None = "e7a2c9d4f1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("server_mount_usage", sa.Column("major", sa.Integer(), nullable=True))
    op.add_column("server_mount_usage", sa.Column("minor", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("server_mount_usage", "minor")
    op.drop_column("server_mount_usage", "major")
