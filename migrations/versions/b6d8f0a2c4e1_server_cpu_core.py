"""server_cpu_core — per-core CPU jiffies (단일스레드 병목 감지, ADR 0052)

Revision ID: b6d8f0a2c4e1
Revises: f4a6c8e0b2d1
Create Date: 2026-07-06 00:00:01.000000

에이전트가 발행하나 그간 미저장(extra=ignore drop)이던 cpu_per_core[] 를 정규화 테이블로 저장한다.
server_disk_io(디바이스별)와 동일 패턴 — 코어별 행 + 코어별 counter_agg cagg 로 reset 일률 처리(ADR 0043).
per-core 이용률 p95 의 서버별 최댓값이 `cpu_percore_p95_max` 로 단일스레드 보호(RS_CPU_PERCORE_HOLD)에 쓰인다.
core_id = /proc/stat cpu0..N 위치 인덱스. Windows 는 per-core 미발행이라 행 없음. boot_time/agent_started_at 미보유
(counter_agg reset-safe + envelope 메타는 server_metrics 에 이미 있어 코어 수만큼 중복 저장 회피).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6d8f0a2c4e1"
down_revision: str | Sequence[str] | None = "f4a6c8e0b2d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = """
    SELECT add_continuous_aggregate_policy('server_cpu_core_5m',
        start_offset => INTERVAL '8 days', end_offset => INTERVAL '10 minutes',
        schedule_interval => INTERVAL '5 minutes')
"""

# cpu_total = 성분 COALESCE 합 (server_metrics_5m #C2 와 동일 — per-core 는 Linux 실측이라 전 성분 존재하나 일관 유지)
_CPU_TOTAL = """counter_agg(collected_at, (COALESCE(cpu_user,0) + COALESCE(cpu_nice,0)
                + COALESCE(cpu_system,0) + COALESCE(cpu_idle,0) + COALESCE(cpu_iowait,0)
                + COALESCE(cpu_irq,0) + COALESCE(cpu_softirq,0) + COALESCE(cpu_steal,0))::double precision)"""


def upgrade() -> None:
    op.create_table(
        "server_cpu_core",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("core_id", sa.Integer(), nullable=False),
        sa.Column("cpu_user", sa.BigInteger(), nullable=True),
        sa.Column("cpu_nice", sa.BigInteger(), nullable=True),
        sa.Column("cpu_system", sa.BigInteger(), nullable=True),
        sa.Column("cpu_idle", sa.BigInteger(), nullable=True),
        sa.Column("cpu_iowait", sa.BigInteger(), nullable=True),
        sa.Column("cpu_irq", sa.BigInteger(), nullable=True),
        sa.Column("cpu_softirq", sa.BigInteger(), nullable=True),
        sa.Column("cpu_steal", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["server_id"], ["server_inventory.id"]),
        sa.PrimaryKeyConstraint("id", "collected_at"),
        sa.UniqueConstraint("server_id", "core_id", "collected_at", name="uq_server_cpu_core_sid_core_ts"),
    )
    op.execute("SELECT create_hypertable('server_cpu_core', 'collected_at', if_not_exists => true)")
    op.execute(f"""
        CREATE MATERIALIZED VIEW server_cpu_core_5m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT server_id, core_id, time_bucket('5 minutes', collected_at) AS bucket,
            {_CPU_TOTAL} AS cpu_total_ca,
            counter_agg(collected_at, cpu_idle::double precision) AS cpu_idle_ca
        FROM server_cpu_core
        GROUP BY server_id, core_id, bucket
        WITH NO DATA
    """)
    op.execute(_POLICY)


def downgrade() -> None:
    op.execute("SELECT remove_continuous_aggregate_policy('server_cpu_core_5m', if_exists => true)")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS server_cpu_core_5m")
    op.drop_table("server_cpu_core")
