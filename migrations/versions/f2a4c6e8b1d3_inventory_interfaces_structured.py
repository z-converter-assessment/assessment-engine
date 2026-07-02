"""server_inventory(+history).interfaces 구조화 — ip_internal(CIDR 문자열) 대체

Revision ID: f2a4c6e8b1d3
Revises: d1f3b5a7c2e4
Create Date: 2026-07-01 00:00:00.000000

agent 가 내부 IP 를 CIDR 문자열 배열(ip_internal)이 아니라 구조화 interfaces[]
([{name, address, prefix, family, kind}], IPv6 포함)로 발행한다 (#E2). 엔진은 iface `kind` 로
가상망(docker/veth/bridge/tunnel/loopback)을 직접 판별 -> 토폴로지 휴리스틱 제거.

breaking cutover (전 agent 교체 + DB 초기화 전제) — 데이터 백필 없음. ip_internal 드롭 + interfaces JSONB 신설.
server_inventory 와 append-only 이력 server_inventory_history 양쪽 적용.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f2a4c6e8b1d3"
down_revision: str | Sequence[str] | None = "d1f3b5a7c2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("server_inventory", "server_inventory_history")


def upgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "ip_internal")
        op.add_column(table, sa.Column("interfaces", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "interfaces")
        op.add_column(table, sa.Column("ip_internal", postgresql.ARRAY(sa.Text()), nullable=True))
