from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class ServerMountUsage(Base):
    __tablename__ = "server_mount_usage"
    __table_args__ = (
        UniqueConstraint("server_id", "mount", "collected_at", name="uq_server_mount_usage_sid_mnt_ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)
    mount: Mapped[str] = mapped_column(String(255), nullable=False)

    total_bytes: Mapped[int | None] = mapped_column(BigInteger)
    free_bytes: Mapped[int | None] = mapped_column(BigInteger)
    avail_bytes: Mapped[int | None] = mapped_column(BigInteger)