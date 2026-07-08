"""ADR 0052 자원 적정성 재설계 — 신 USE 신호 컬럼 추가

Revision ID: e6b8d0f2a4c7
Revises: d4f6a8c0b2e5
Create Date: 2026-07-05 00:00:00.000000

per-resource USE 모델(ADR 0052)이 소비할 raw 신호를 시계열 4테이블에 추가한다. 전부 nullable —
agent optional 발행(없으면 null), 기존 행 rewrite 없음(즉시). hypertable nullable add 는 즉시.
- server_metrics: host-wide 스칼라 (procs·swap·oom·Windows paging·TCP·conntrack).
- server_disk_io: await 원자료(time_reading/writing_ms) + %util·avgqu 참고(io_ticks/weighted).
- server_net_io: rx/tx drops(품질).
- server_mount_usage: inode(inodes_total/free).
cagg 집계·counter_agg 는 Phase C 별도(본 마이그레이션은 raw 컬럼만).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6b8d0f2a4c7"
down_revision: str | Sequence[str] | None = "d4f6a8c0b2e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, type) — nullable raw 신호. BigInteger=누적 카운터, Integer=gauge/count.
_ADDS: list[tuple[str, str, sa.types.TypeEngine]] = [
    # server_metrics host-wide
    ("server_metrics", "procs_running", sa.Integer()),
    ("server_metrics", "procs_blocked", sa.Integer()),
    ("server_metrics", "schedstat_run_wait_ns", sa.BigInteger()),
    ("server_metrics", "pswpin", sa.BigInteger()),
    ("server_metrics", "pswpout", sa.BigInteger()),
    ("server_metrics", "oom_kill", sa.BigInteger()),
    ("server_metrics", "mem_pages_input", sa.BigInteger()),
    ("server_metrics", "tcp_retrans_segs", sa.BigInteger()),
    ("server_metrics", "tcp_tw", sa.Integer()),
    ("server_metrics", "conntrack_count", sa.Integer()),
    ("server_metrics", "conntrack_max", sa.Integer()),
    # server_disk_io await 원자료 + 참고
    ("server_disk_io", "time_reading_ms", sa.BigInteger()),
    ("server_disk_io", "time_writing_ms", sa.BigInteger()),
    ("server_disk_io", "io_ticks_ms", sa.BigInteger()),
    ("server_disk_io", "weighted_io_ms", sa.BigInteger()),
    # server_net_io drops
    ("server_net_io", "rx_drops", sa.BigInteger()),
    ("server_net_io", "tx_drops", sa.BigInteger()),
    # server_mount_usage inode
    ("server_mount_usage", "inodes_total", sa.BigInteger()),
    ("server_mount_usage", "inodes_free", sa.BigInteger()),
]


def upgrade() -> None:
    for table, column, coltype in _ADDS:
        op.add_column(table, sa.Column(column, coltype, nullable=True))


def downgrade() -> None:
    for table, column, _coltype in reversed(_ADDS):
        op.drop_column(table, column)
