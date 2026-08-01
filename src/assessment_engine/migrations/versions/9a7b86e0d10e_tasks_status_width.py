"""tasks.status 폭을 인바운드 계약에 맞춘다

인바운드 스키마가 32자까지 받는데 컬럼이 16자라, 그 사이 길이의 status 는 검증을 통과한 뒤
UPDATE 에서 22001 로 실패해 DLQ 로 갔다. 새 status 값을 조용히 통과시킨다는 전방 호환 의도가
그 구간에서만 깨져 있었다.

Revision ID: 9a7b86e0d10e
Revises: 1f92642782bb
Create Date: 2026-08-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a7b86e0d10e"
down_revision: str | Sequence[str] | None = "1f92642782bb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # varchar 확장은 테이블 rewrite 없는 메타데이터 변경이고, status='pending' 부분 UNIQUE 도 재생성되지 않는다.
    op.alter_column("tasks", "status", existing_type=sa.String(16), type_=sa.String(32), existing_nullable=False)


def downgrade() -> None:
    # 16자를 넘는 값이 남아 있으면 여기서 실패하는 것이 맞다 — 조용히 자르지 않는다.
    op.alter_column("tasks", "status", existing_type=sa.String(32), type_=sa.String(16), existing_nullable=False)
