"""server_metrics — PSI some total + disk idle/query time 저장 (agent 발행값 전부 보존)

Revision ID: c7f9a1b3d5e8
Revises: b5e8f1a3c7d9
Create Date: 2026-07-07 00:00:02.000000

agent 가 발행하나 지금까지 extra=ignore 로 drop 되던 raw 신호를 저장한다 — 당장 분류/표시에 안 써도
발행값은 전부 DB 에 보존(#B). 관측·검증·후속 활용 대비.
- psi_cpu/mem/io_some_total: PSI some total(us 누적, /proc/pressure/*, 4.20+). 분류 미사용(관측·검증).
- sat_disk_idle_time / sat_disk_query_time: IOCTL_DISK_PERFORMANCE %util·델타분모 참고(device 합산 누적).
- collection_interval_sec: agent 설정 수집 주기(초, Integer). 나머지는 nullable BigInteger.
cagg 미집계(단순 컬럼 추가).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7f9a1b3d5e8"
down_revision: str | Sequence[str] | None = "b5e8f1a3c7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BIGINT_COLS = (
    "psi_cpu_some_total",
    "psi_mem_some_total",
    "psi_io_some_total",
    "sat_disk_idle_time",
    "sat_disk_query_time",
)


def upgrade() -> None:
    for col in _BIGINT_COLS:
        op.add_column("server_metrics", sa.Column(col, sa.BigInteger(), nullable=True))
    op.add_column("server_metrics", sa.Column("collection_interval_sec", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("server_metrics", "collection_interval_sec")
    for col in reversed(_BIGINT_COLS):
        op.drop_column("server_metrics", col)
