from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Float, ForeignKey, Integer
from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from db.models.base import Base
from db.models.server_inventory import ServerInventory


class ServerMetrics(Base):
    __tablename__ = "server_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("servers.id"), nullable=False, index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    # CPU — /proc/stat 보장, not null
    cpu_user_pct: Mapped[float] = mapped_column(Float, nullable=False)
    cpu_system_pct: Mapped[float] = mapped_column(Float, nullable=False)
    cpu_iowait_pct: Mapped[float] = mapped_column(Float, nullable=False)

    # Memory
    mem_used_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    mem_available_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)  # CentOS 7.0~7.1 fallback 실패
    swap_used_mb: Mapped[int] = mapped_column(Integer, nullable=False)

    # Disk I/O — 기동 직후 첫 전송은 delta 없음 → nullable
    disk_read_iops: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_write_iops: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_read_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    disk_write_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Disk usage — 빈 배열도 유효
    disk_usage: Mapped[list] = mapped_column(JSONB, nullable=False)

    # Network — 기동 직후 첫 전송은 delta 없음 → nullable
    net_rx_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    net_tx_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Load — /proc/loadavg 보장, not null
    load_1m: Mapped[float] = mapped_column(Float, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    server: Mapped[ServerInventory] = relationship("ServerInventory", back_populates="snapshots")