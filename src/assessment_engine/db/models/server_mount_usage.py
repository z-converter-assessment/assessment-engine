from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class ServerMountUsage(Base):
    __tablename__ = "server_mount_usage"
    __table_args__ = (UniqueConstraint("server_id", "mount", "collected_at", name="uq_server_mount_usage_sid_mnt_ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)
    mount: Mapped[str] = mapped_column(String(255), nullable=False)

    total_bytes: Mapped[int | None] = mapped_column(BigInteger)
    free_bytes: Mapped[int | None] = mapped_column(BigInteger)
    avail_bytes: Mapped[int | None] = mapped_column(BigInteger)

    # data-volume 판단 신호 — major==0 = 블록 디바이스 없는 가상 fs.
    major: Mapped[int | None] = mapped_column(Integer)
    minor: Mapped[int | None] = mapped_column(Integer)

    # 시계열 4개 테이블 메타 일관성 (#C1·#B). 본 테이블은 시점값이라 reset 판정 미사용, 메타 균일 위해 보존.
    boot_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
