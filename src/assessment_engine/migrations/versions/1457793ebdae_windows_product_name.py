"""Windows ProductName 원문 컬럼 추가 (os_display 짧은 라벨 파싱 소스)

agent 가 CurrentVersion ProductName 을 발행(Windows only, Linux null) — os_version(DisplayVersion)이
LTSC/SAC 구분 없이 동일 문자열("1809")을 공유하는 한계를 product_name 의 연도 유무로 보강.

Revision ID: 1457793ebdae
Revises: d1c8b4a6e2f9
Create Date: 2026-07-15

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "1457793ebdae"
down_revision: str | Sequence[str] | None = "d1c8b4a6e2f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = ("server_inventory", "server_inventory_history")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("product_name", sa.String(length=128), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "product_name")
