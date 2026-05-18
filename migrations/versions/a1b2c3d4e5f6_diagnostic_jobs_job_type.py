"""diagnostic_jobs.job_type 컬럼 + active UNIQUE 에 job_type 포함

Revision ID: a1b2c3d4e5f6
Revises: f5c1e2d3a4b8
Create Date: 2026-05-18 15:30:00.000000

보고서 (고객/엔지니어) 를 diagnostic_jobs 에 통합 — 이력 보존 + AI 진단 목록과 통합 표시.
job_type Literal: 'ai_diagnostic' (기본), 'customer_report', 'engineer_report'.

active partial UNIQUE 에 job_type 추가 — 같은 (scope, input_hash) 대상에 다른 양식 보고서를
동시 발행 허용 (예: 같은 서버 N대에 고객 보고서 + 엔지니어 보고서 병렬).

기존 row 는 server_default='ai_diagnostic' 으로 자동 backfill (NOT NULL 안전).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: str | Sequence[str] | None = 'f5c1e2d3a4b8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'diagnostic_jobs',
        sa.Column(
            'job_type',
            sa.String(length=32),
            nullable=False,
            server_default='ai_diagnostic',
        ),
    )
    op.drop_index('uq_diagnostic_jobs_active_per_input', table_name='diagnostic_jobs')
    op.create_index(
        'uq_diagnostic_jobs_active_per_input',
        'diagnostic_jobs',
        ['scope', 'input_hash', 'job_type'],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index('uq_diagnostic_jobs_active_per_input', table_name='diagnostic_jobs')
    op.create_index(
        'uq_diagnostic_jobs_active_per_input',
        'diagnostic_jobs',
        ['scope', 'input_hash'],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )
    op.drop_column('diagnostic_jobs', 'job_type')
