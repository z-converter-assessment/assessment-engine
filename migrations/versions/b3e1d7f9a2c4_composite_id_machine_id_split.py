"""composite_id 단일 식별 + machine_id 표시용 분리 (agent v4 계약)

Revision ID: b3e1d7f9a2c4
Revises: f8b2c4d6e1a3
Create Date: 2026-05-26 14:00:00.000000

agent v4 가 발행하는 식별 체계 변경 반영:
- server_inventory.host_id -> composite_id rename (SHA-256 composite hash, 단일 UNIQUE 식별).
- server_inventory.machine_id 신규 (raw machine-id 표시 전용, 식별·라우팅 미사용, nullable).
- tasks.target_host_id -> target_composite_id rename (audit 컬럼 + index).

server_inventory_history 는 식별 컬럼 미러 제외(server_id 로 충분)라 변경 없음.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3e1d7f9a2c4"
down_revision: str | Sequence[str] | None = "f8b2c4d6e1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_inventory: host_id -> composite_id rename + machine_id 표시용 추가
    op.drop_constraint("uq_server_inventory_host_id", "server_inventory", type_="unique")
    op.alter_column("server_inventory", "host_id", new_column_name="composite_id")
    op.create_unique_constraint("uq_server_inventory_composite_id", "server_inventory", ["composite_id"])
    op.add_column("server_inventory", sa.Column("machine_id", sa.String(length=64), nullable=True))

    # tasks: target_host_id -> target_composite_id rename (index 포함)
    op.drop_index("ix_tasks_target_host_id", table_name="tasks")
    op.alter_column("tasks", "target_host_id", new_column_name="target_composite_id")
    op.create_index("ix_tasks_target_composite_id", "tasks", ["target_composite_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_target_composite_id", table_name="tasks")
    op.alter_column("tasks", "target_composite_id", new_column_name="target_host_id")
    op.create_index("ix_tasks_target_host_id", "tasks", ["target_host_id"])

    op.drop_column("server_inventory", "machine_id")
    op.drop_constraint("uq_server_inventory_composite_id", "server_inventory", type_="unique")
    op.alter_column("server_inventory", "composite_id", new_column_name="host_id")
    op.create_unique_constraint("uq_server_inventory_host_id", "server_inventory", ["host_id"])
