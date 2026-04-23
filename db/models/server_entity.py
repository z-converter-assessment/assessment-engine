from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from db.models.metric_snapshot import MetricSnapshot

from sqlalchemy import Integer, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from db.models.base import Base


class ServerEntity(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hostname: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    snapshots: Mapped[List[MetricSnapshot]] = relationship("MetricSnapshot", back_populates="server", cascade="all, delete-orphan")