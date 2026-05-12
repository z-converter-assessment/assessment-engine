"""drop diagnostic_jobs cache index

Revision ID: d2f4a6b8c0e1
Revises: c1d2e3f4a5b6
Create Date: 2026-05-12 11:00:00.000000

ADR 0004 정정 — 1시간 input_hash 캐시 규칙 제거 (anchor 분 단위 변화로 실효성 없음).
관련 partial index ix_diagnostic_jobs_cache_lookup도 drop.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'd2f4a6b8c0e1'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('ix_diagnostic_jobs_cache_lookup', table_name='diagnostic_jobs')


def downgrade() -> None:
    op.create_index(
        'ix_diagnostic_jobs_cache_lookup',
        'diagnostic_jobs',
        ['input_hash', 'finished_at'],
        postgresql_where=sa.text("status = 'succeeded'"),
    )
