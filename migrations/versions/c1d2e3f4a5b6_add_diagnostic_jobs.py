"""add diagnostic_jobs

Revision ID: c1d2e3f4a5b6
Revises: b8f8ffd882f7
Create Date: 2026-05-12 10:00:00.000000

ADR 0004 — AI 진단 워커 기반 테이블. 일반 테이블(hypertable 아님).
partial UNIQUE + partial INDEX 2종 보강 — 자세한 사유는 모델 docstring.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "b8f8ffd882f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "diagnostic_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("input_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("progress_stage", sa.String(length=32), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # active partial UNIQUE — (scope, input_hash) 동시 진행 1건 차단 (더블클릭·중복 enqueue 방어).
    op.create_index(
        "uq_diagnostic_jobs_active_per_input",
        "diagnostic_jobs",
        ["scope", "input_hash"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    # cache partial INDEX — 1시간 캐시 조회 (input_hash + finished_at DESC).
    op.create_index(
        "ix_diagnostic_jobs_cache_lookup",
        "diagnostic_jobs",
        ["input_hash", "finished_at"],
        postgresql_where=sa.text("status = 'succeeded'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_diagnostic_jobs_cache_lookup", table_name="diagnostic_jobs")
    op.drop_index("uq_diagnostic_jobs_active_per_input", table_name="diagnostic_jobs")
    op.drop_table("diagnostic_jobs")
