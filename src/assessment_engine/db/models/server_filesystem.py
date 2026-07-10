from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class ServerFilesystem(Base):
    """마운트별 파일시스템 사용량 시계열 (wire filesystem.usage / filesystem.inodes.usage).

    gauge(시점값) — used/free By, inode used/free 개수. counter 아님(delta 미적용). 용량 runway 는
    used 추세 회귀로 산출. device_id 병기(안정키, 표시·조인용). NK = (server_id, mountpoint, collected_at).
    envelope 메타 미보유 — server_metrics 행 참조.
    """

    __tablename__ = "server_filesystem"
    __table_args__ = (
        UniqueConstraint("server_id", "mountpoint", "collected_at", name="uq_server_filesystem_sid_mnt_ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)
    mountpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(128))  # 안정키 (block_devices 조인)
    fstype: Mapped[str | None] = mapped_column(String(32))

    used_bytes: Mapped[int | None] = mapped_column(BigInteger)
    free_bytes: Mapped[int | None] = mapped_column(BigInteger)
    # inode — 작은 파일 폭증 시 바이트 남아도 inode 먼저 소진 -> ENOSPC. Windows 는 미발행(null).
    inodes_used: Mapped[int | None] = mapped_column(BigInteger)
    inodes_free: Mapped[int | None] = mapped_column(BigInteger)
