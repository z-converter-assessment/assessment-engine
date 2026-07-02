"""식별 단일 키를 composite_id 에서 agent_id 로 전환 (agent_id 불변 UUID)

Revision ID: e8b4d2f6a1c9
Revises: d7f9b1c3e5a2
Create Date: 2026-07-01 00:00:05.000000

agent 가 발행하는 불변 UUID(agent_id)를 식별 단일 키로 승격한다. composite_id(SHA-256 machine_id+MAC)는
부팅마다 바뀔 수 있어(OpenStack Windows VM NIC 재발급) 재연결 로직이 필요했으나, agent_id 는 첫 실행 시
1회 생성·영구저장되어 부팅 무관 불변 — 재연결 자체가 불요(_relink_rebooted_host 제거).

- server_inventory: agent_id(UUID) 추가 + UNIQUE, composite_id UNIQUE 해제·nullable 강등(감사·표시용).
- tasks: target_composite_id -> target_agent_id(UUID) — 발행 대상·라우팅 기록.

breaking cutover(전 에이전트 교체 + DB 초기화 전제)라 데이터 백필 없음 — 빈 테이블에 agent_id NOT NULL 바로 부여.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e8b4d2f6a1c9"
down_revision: str | Sequence[str] | None = "d7f9b1c3e5a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_inventory: agent_id 식별 승격, composite_id 감사용 강등
    op.add_column("server_inventory", sa.Column("agent_id", postgresql.UUID(as_uuid=False), nullable=False))
    op.create_unique_constraint("uq_server_inventory_agent_id", "server_inventory", ["agent_id"])
    op.drop_constraint("uq_server_inventory_composite_id", "server_inventory", type_="unique")
    op.alter_column("server_inventory", "composite_id", existing_type=sa.String(64), nullable=True)

    # tasks: 발행 대상 식별자 agent_id 전환
    op.drop_index("ix_tasks_target_composite_id", table_name="tasks")
    op.drop_column("tasks", "target_composite_id")
    op.add_column("tasks", sa.Column("target_agent_id", postgresql.UUID(as_uuid=False), nullable=False))
    op.create_index("ix_tasks_target_agent_id", "tasks", ["target_agent_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_target_agent_id", table_name="tasks")
    op.drop_column("tasks", "target_agent_id")
    op.add_column("tasks", sa.Column("target_composite_id", sa.String(length=64), nullable=False))
    op.create_index("ix_tasks_target_composite_id", "tasks", ["target_composite_id"])

    op.alter_column("server_inventory", "composite_id", existing_type=sa.String(64), nullable=False)
    op.create_unique_constraint("uq_server_inventory_composite_id", "server_inventory", ["composite_id"])
    op.drop_constraint("uq_server_inventory_agent_id", "server_inventory", type_="unique")
    op.drop_column("server_inventory", "agent_id")
