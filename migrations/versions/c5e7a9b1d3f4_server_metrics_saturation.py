"""server_metrics.saturation 컬럼 추가 — USE Method saturation raw 신호 저장

Revision ID: c5e7a9b1d3f4
Revises: b4d6f8a0c2e3
Create Date: 2026-07-01 00:00:03.000000

agent 가 metrics.saturation{disk_queue, cpu_run_queue, mem_paging_rate} 를 발행한다 (raw, os-aware 임계는 엔진).
현재 Windows 는 disk_queue 만 채우고 나머지는 null (perflib 검증 전까지 보류). 저장만 추가 — recommendation
소비(disk 축 채우기)는 임계 설계와 함께 후속. server_metrics_5m cagg 는 cpu/mem 집계라 무관(변경 없음).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5e7a9b1d3f4"
down_revision: str | Sequence[str] | None = "b4d6f8a0c2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("sat_disk_queue", "sat_cpu_run_queue", "sat_mem_paging_rate")


def upgrade() -> None:
    for col in _COLUMNS:
        op.add_column("server_metrics", sa.Column(col, sa.Float(), nullable=True))


def downgrade() -> None:
    for col in _COLUMNS:
        op.drop_column("server_metrics", col)
