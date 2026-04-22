from datetime import datetime

from sqlalchemy import Integer, BigInteger, ForeignKey, DateTime
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from db.models.base import Base
from db.models.server_entity import ServerEntity


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("servers.id"), nullable=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    nproc: Mapped[int] = mapped_column(Integer, nullable=False)
    mem_total_mb: Mapped[int] = mapped_column(BigInteger, nullable=False)
    disks: Mapped[list] = mapped_column(postgresql.JSONB, nullable=False)
    ip_internal: Mapped[list] = mapped_column(postgresql.JSONB, nullable=False)
    ip_external: Mapped[list] = mapped_column(postgresql.JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    server: Mapped[ServerEntity] = relationship("ServerEntity", back_populates="snapshots")
