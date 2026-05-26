"""server_inventory.mac_addresses 추가 (clone collision 감사용 NIC MAC 목록)

Revision ID: e7a2c9d4f1b6
Revises: c5f9a3b1e7d2
Create Date: 2026-05-27 00:00:00.000000

agent(Linux+Windows) 가 payload-schema v3.3 부터 inventory.mac_addresses[] 를 발행한다 (lowercase·정렬·dedup
MAC 목록). 다중 NIC 라 단일 식별 불가 — composite_id 가 sha256 으로 MAC 을 이미 흡수하므로 식별·라우팅엔
미사용. raw 목록은 clone collision (이미지 클론으로 machine_id 중복) 감사·추적용으로 보존한다.

ip_internal/ip_external 과 동일하게 ARRAY(Text) — str 목록이라 JSONB 불필요. nullable (옛 agent 호환).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e7a2c9d4f1b6"
down_revision: str | Sequence[str] | None = "c5f9a3b1e7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "server_inventory",
        sa.Column("mac_addresses", postgresql.ARRAY(sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("server_inventory", "mac_addresses")
