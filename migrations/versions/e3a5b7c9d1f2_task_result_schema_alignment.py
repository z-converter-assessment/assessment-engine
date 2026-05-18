"""task.result schema 정합화 — 6 신규 컬럼 추가 / result_message 제거 / status enum 정정

Revision ID: e3a5b7c9d1f2
Revises: d2f4a6b8c0e1
Create Date: 2026-05-14 12:00:00.000000

agent 결과 보고 메시지 구조 정합화.
- tasks.failure_reason / exit_code / duration_ms / stdout_tail / stderr_tail 컬럼 추가
- tasks.result_message 컬럼 제거 (failure_reason + stderr_tail 조합으로 대체)
- tasks.status 값 'failed' -> 'failure' 일괄 UPDATE (Pydantic Literal 정합)
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e3a5b7c9d1f2'
down_revision: str | Sequence[str] | None = 'd2f4a6b8c0e1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('failure_reason', sa.String(length=32), nullable=True))
    op.add_column('tasks', sa.Column('exit_code', sa.SmallInteger(), nullable=True))
    op.add_column('tasks', sa.Column('duration_ms', sa.BigInteger(), nullable=True))
    op.add_column('tasks', sa.Column('stdout_tail', sa.Text(), nullable=True))
    op.add_column('tasks', sa.Column('stderr_tail', sa.Text(), nullable=True))

    # 기존 row의 status='failed' -> 'failure' 일괄 교체. Pydantic Literal과 정합.
    op.execute("UPDATE tasks SET status = 'failure' WHERE status = 'failed'")

    op.drop_column('tasks', 'result_message')


def downgrade() -> None:
    op.add_column('tasks', sa.Column('result_message', sa.Text(), nullable=True))

    op.execute("UPDATE tasks SET status = 'failed' WHERE status = 'failure'")

    op.drop_column('tasks', 'stderr_tail')
    op.drop_column('tasks', 'stdout_tail')
    op.drop_column('tasks', 'duration_ms')
    op.drop_column('tasks', 'exit_code')
    op.drop_column('tasks', 'failure_reason')
