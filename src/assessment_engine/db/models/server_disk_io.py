from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class ServerDiskIo(Base):
    __tablename__ = "server_disk_io"
    __table_args__ = (
        UniqueConstraint("server_id", "device", "collected_at", name="uq_server_disk_io_sid_dev_ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)
    device: Mapped[str] = mapped_column(String(64), nullable=False)

    reads_completed: Mapped[int | None] = mapped_column(BigInteger)
    writes_completed: Mapped[int | None] = mapped_column(BigInteger)
    sectors_read: Mapped[int | None] = mapped_column(BigInteger)
    sectors_written: Mapped[int | None] = mapped_column(BigInteger)

    # counter reset 정밀 식별용 (server_metrics와 동일 정책).
    boot_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))