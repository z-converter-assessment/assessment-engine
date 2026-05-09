from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class Task(Base):
    """ZConverter 등 원격 작업 명령 + 실행 이력. 영구 보존 (source of truth).

    Redis(`task:pending:{machine_id}`)는 hot path 캐시. DB가 진실, Redis는 빠른 확인용.

    pending 상태 부분 UNIQUE — 같은 (target_server_id, task_type) 조합으로 동시 다중 pending
    INSERT 차단. 운영자 더블클릭 방어. UniqueViolation은 service가 catch해서 409 반환.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        Index(
            "uq_tasks_pending_per_server_type",
            "target_server_id", "task_type",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), nullable=False, server_default=func.gen_random_uuid(), unique=True
    )
    target_server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)
    target_machine_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    task_type: Mapped[str] = mapped_column(String(64), nullable=False)  # "zconverter_install" 등
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)        # task_type별 파라미터
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # pending → in_progress → success / failed

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_message: Mapped[str | None] = mapped_column(Text)
