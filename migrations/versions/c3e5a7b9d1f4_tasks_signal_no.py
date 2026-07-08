"""tasks.signal_no — task.result 시그널 사망 번호 저장

Revision ID: c3e5a7b9d1f4
Revises: b1d3f5a7c9e2
Create Date: 2026-07-04 00:00:00.000000

task.result 의 signal_no(int|null) 를 저장할 컬럼. exit_code 와 상호배타 (정상종료=exit_code / 시그널종료=signal_no).
nullable 컬럼 추가라 기존 행 rewrite 없음(즉시). SmallInteger — 시그널 번호 1~64.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e5a7b9d1f4"
down_revision: str | Sequence[str] | None = "b1d3f5a7c9e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("signal_no", sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "signal_no")
