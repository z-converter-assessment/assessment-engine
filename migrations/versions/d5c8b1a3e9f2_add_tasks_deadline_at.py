"""tasks 에 deadline_at 추가 (install 응답 마감 — agent 무응답 timeout)

Revision ID: d5c8b1a3e9f2
Revises: a9f3c7e1d5b2
Create Date: 2026-06-09 00:00:00.000000

install task 발행 시점에 응답 마감(now + install_timeout_sec + margin)을 확정 저장한다.
경과한 pending 은 표시 계층에서 "응답 시간 초과"로 노출되고, 다음 발행 시점에 failure(failure_reason='timeout')
로 전이되어 pending 부분 UNIQUE 충돌 없이 재발행을 허용한다. install 외 task_type 은 NULL.
nullable — 마이그레이션 전 기존 pending row 는 NULL(마감 없음, 표시·expire 대상 제외).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5c8b1a3e9f2"
down_revision: str | Sequence[str] | None = "a9f3c7e1d5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "deadline_at")
