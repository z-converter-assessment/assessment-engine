"""server_inventory.service_categories — 서비스 카테고리 ingest 사전계산

Revision ID: a7c3e5f1b9d4
Revises: f9d3a7c2b5e1
Create Date: 2026-06-24 16:00:00.000000

service_classifier.compute_service_categories 로 inventory upsert 시 산출한 카테고리 키 집합(text[])을 저장.
이름·comm·포트 어느 신호로 식별되든 동일 — 모든 read 경로(목록·상세·리포트·필터)가 본 컬럼을 소비해
화면 간 서비스 뱃지 집합 일치. category 필터용 GIN 인덱스(배열 포함 연산 @>/&&).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3e5f1b9d4"
down_revision: str | Sequence[str] | None = "f9d3a7c2b5e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("server_inventory", sa.Column("service_categories", sa.ARRAY(sa.Text()), nullable=True))
    op.create_index(
        "ix_server_inventory_service_categories",
        "server_inventory",
        ["service_categories"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_server_inventory_service_categories", table_name="server_inventory")
    op.drop_column("server_inventory", "service_categories")
