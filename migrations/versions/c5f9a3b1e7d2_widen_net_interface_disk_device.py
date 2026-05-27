"""net_io interface / disk_io device 길이 상향 (Windows 인터페이스 이름 수용)

Revision ID: c5f9a3b1e7d2
Revises: b3e1d7f9a2c4
Create Date: 2026-05-26 15:00:00.000000

Windows 네트워크 인터페이스는 WFP/QoS 필터 드라이버 체인 이름이라 64자 초과 (NDIS 한계 256).
varchar(64) 제한에 걸려 metrics 메시지가 Pydantic 검증에서 전량 reject됐다.
- server_net_io.interface 64 -> 256
- server_disk_io.device 64 -> 128 (방어 — Windows 디스크 이름 여유)

varchar 길이 확장이라 데이터·UNIQUE 인덱스 무손실.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5f9a3b1e7d2"
down_revision: str | Sequence[str] | None = "b3e1d7f9a2c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "server_net_io",
        "interface",
        existing_type=sa.String(64),
        type_=sa.String(256),
        existing_nullable=False,
    )
    op.alter_column(
        "server_disk_io",
        "device",
        existing_type=sa.String(64),
        type_=sa.String(128),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "server_disk_io",
        "device",
        existing_type=sa.String(128),
        type_=sa.String(64),
        existing_nullable=False,
    )
    op.alter_column(
        "server_net_io",
        "interface",
        existing_type=sa.String(256),
        type_=sa.String(64),
        existing_nullable=False,
    )
