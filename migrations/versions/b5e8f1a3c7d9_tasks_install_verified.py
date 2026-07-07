"""tasks.install_verified — 실제 설치 신호(데몬 기동+ZDM 등록) 판정 1순위 컬럼

Revision ID: b5e8f1a3c7d9
Revises: a3c5e7f9b2d4
Create Date: 2026-07-07 00:00:01.000000

install 성패를 installer exit_code 로만 판정하던 로직의 false positive(exit 0 인데 데몬 미기동)를 없애기
위해, agent worker 가 installer 종료 후 실제 설치 상태(데몬 기동 + ZDM 등록)를 점검해 발행하는
install_verified 를 저장한다. 판정 우선순위는 task_policy.effective_task_result 단일 진실.
nullable — 구버전 agent 미발행 시 null(레거시 exit_code + allowlist 폴백, 하위 호환).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5e8f1a3c7d9"
down_revision: str | Sequence[str] | None = "a3c5e7f9b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("install_verified", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "install_verified")
