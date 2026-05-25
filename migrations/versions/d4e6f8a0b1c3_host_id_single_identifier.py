"""host_id 단일 식별자 (ADR 0022)

Revision ID: d4e6f8a0b1c3
Revises: c3a5e7f9b1d2
Create Date: 2026-05-23 21:00:00.000000

server_inventory / server_inventory_history / tasks 의 machine_id → host_id rename
+ server_inventory UNIQUE 단일 (host_id).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e6f8a0b1c3"
down_revision: str | Sequence[str] | None = "c3a5e7f9b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_inventory
    op.drop_constraint("uq_server_inventory_machine_hostname", "server_inventory", type_="unique")
    op.alter_column("server_inventory", "machine_id", new_column_name="host_id")
    op.create_unique_constraint("uq_server_inventory_host_id", "server_inventory", ["host_id"])

    # tasks
    op.drop_index("ix_tasks_target_machine_id", table_name="tasks")
    op.alter_column("tasks", "target_machine_id", new_column_name="target_host_id")
    op.create_index("ix_tasks_target_host_id", "tasks", ["target_host_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_target_host_id", table_name="tasks")
    op.alter_column("tasks", "target_host_id", new_column_name="target_machine_id")
    op.create_index("ix_tasks_target_machine_id", "tasks", ["target_machine_id"])

    op.drop_constraint("uq_server_inventory_host_id", "server_inventory", type_="unique")
    op.alter_column("server_inventory", "host_id", new_column_name="machine_id")
    op.create_unique_constraint(
        "uq_server_inventory_machine_hostname",
        "server_inventory",
        ["machine_id", "hostname"],
    )
