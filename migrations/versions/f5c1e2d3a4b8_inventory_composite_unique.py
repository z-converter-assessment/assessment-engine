"""server_inventory unique 키를 (machine_id, hostname) 복합으로 변경

Revision ID: f5c1e2d3a4b8
Revises: e3a5b7c9d1f2
Create Date: 2026-05-18 14:50:00.000000

machine_id 단독 UNIQUE 는 VM 템플릿 복제·이미지 clone·container host /etc/machine-id 마운트 등으로
실제 운영 환경에서 중복 가능 → hostname 과 함께 복합 unique 로 보장.

Backward compatibility:
- upgrade: 기존 단일 UNIQUE 가 더 strict 였으니 자동 호환 (단일 unique 통과한 데이터는 복합 unique 도 통과).
- downgrade: 같은 machine_id 다른 hostname row 가 있으면 단일 UNIQUE 복원 실패. 운영자가
  사전에 중복 row 정리 의무 (docs/operations/alembic.md "Backward compatibility" 절).
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'f5c1e2d3a4b8'
down_revision: str | Sequence[str] | None = 'e3a5b7c9d1f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint('server_inventory_machine_id_key', 'server_inventory', type_='unique')
    op.create_unique_constraint(
        'uq_server_inventory_machine_hostname',
        'server_inventory',
        ['machine_id', 'hostname'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_server_inventory_machine_hostname', 'server_inventory', type_='unique')
    op.create_unique_constraint(
        'server_inventory_machine_id_key',
        'server_inventory',
        ['machine_id'],
    )
