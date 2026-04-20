from __future__ import annotations
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import String, Integer, BigInteger, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hostname: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    metrics: Mapped[List[ServerMetric]] = relationship("ServerMetric", back_populates="server", cascade="all, delete-orphan")


class ServerMetric(Base):
    __tablename__ = "server_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("servers.id"), nullable=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    nproc: Mapped[Optional[int]] = mapped_column(Integer)
    mem_total_mb: Mapped[Optional[int]] = mapped_column(BigInteger)
    disks: Mapped[Optional[list]] = mapped_column(JSONB)
    ip_internal: Mapped[Optional[list]] = mapped_column(JSONB)
    ip_external: Mapped[Optional[list]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    server: Mapped[Server] = relationship("Server", back_populates="metrics")