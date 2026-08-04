"""server_pressure.id 를 bigint 로

시계열 8테이블 중 server_pressure 만 int4 SERIAL 이었다. 시퀀스가 int4 상한을 넘기면 nextval 이
SQLSTATE 2200H 로 실패하는데, 이 오류는 재시도 대상 분류에 없어 즉시 DLQ 로 간다. record_metrics 는
1 메시지 = 1 트랜잭션이라 PSI 행만이 아니라 그 호스트의 메트릭 적재 전체가 함께 롤백된다.

Revision ID: 1f92642782bb
Revises: f6f365fd114c
Create Date: 2026-08-01

"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "1f92642782bb"
down_revision: str | Sequence[str] | None = "f6f365fd114c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("server_pressure", "id", existing_type=sa.INTEGER(), type_=sa.BigInteger(), existing_nullable=False)
    # 컬럼 타입만 바꾸면 SERIAL 이 만든 시퀀스는 AS integer 로 남아 상한이 그대로다. autogenerate 는
    # 시퀀스 타입을 비교하지 않아 이 갭을 drift 로 잡지 못한다.
    op.execute("ALTER SEQUENCE server_pressure_id_seq AS bigint")


def downgrade() -> None:
    # last_value 가 int4 상한을 넘었으면 여기서 실패하는 것이 맞다.
    op.execute("ALTER SEQUENCE server_pressure_id_seq AS integer")
    op.alter_column("server_pressure", "id", existing_type=sa.BigInteger(), type_=sa.INTEGER(), existing_nullable=False)
