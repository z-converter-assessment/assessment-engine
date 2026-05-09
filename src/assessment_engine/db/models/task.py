from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class Task(Base):
    """ZConverter 등 원격 작업 명령 + 실행 이력. 영구 보존 (source of truth).

    Redis(`task:pending:{machine_id}`)는 hot path 캐시. DB가 진실, Redis는 빠른 확인용.
    """

    __tablename__ = "tasks"

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
