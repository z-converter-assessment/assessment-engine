"""diagnostic_jobs.job_type server_default 를 customer_report 로 정정

Revision ID: f9d3a7c2b5e1
Revises: e2f4a6c8b0d3
Create Date: 2026-06-14 12:30:00.000000

diagnostic_jobs 는 보고서 발행 job 만 enqueue (job_type 은 'customer_report'/'engineer_report'
둘 중 하나). emit_report 가 항상 job_type 을 명시 INSERT 하므로 server_default 는 실제로
발동하지 않으나, 컬럼 기본값이 더 이상 생성되지 않는 값으로 남아 있어 정합을 위해 정정한다.
기존 row 값은 변경하지 않는다 (이력 보존).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f9d3a7c2b5e1"
down_revision: str | Sequence[str] | None = "e2f4a6c8b0d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "diagnostic_jobs",
        "job_type",
        server_default="customer_report",
    )


def downgrade() -> None:
    op.alter_column(
        "diagnostic_jobs",
        "job_type",
        server_default="ai_diagnostic",
    )
