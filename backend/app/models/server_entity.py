from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING, List
from uuid import UUID, uuid4

# IDE 우회용: 런타임에 항상 false
if TYPE_CHECKING:
    from app.models.metric_snapshot import MetricSnapshot

from sqlalchemy import String, DateTime
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base


class ServerEntity(Base):
    __tablename__ = "servers"

    id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True, default=uuid4)
    hostname: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    snapshots: Mapped[List[MetricSnapshot]] = relationship("MetricSnapshot", back_populates="server", cascade="all, delete-orphan")
