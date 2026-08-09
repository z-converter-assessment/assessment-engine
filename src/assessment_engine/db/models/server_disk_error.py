from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class ServerDiskError(Base):
    """디스크·스토리지 오류 시계열 (E축, wire system.disk errors — 가변 차원).

    error_kind = 하위계층 종류(mdraid|btrfs|ext4|eventlog|ioerr), error_class = 구체 오류
    (degraded|corruption|member_errors|..), member = 다중 멤버 식별(RAID 디스크 등, 없으면 '').
    count 는 counter(정상 시 0). NK = (server_id, device_id, error_kind, error_class, member, collected_at)
    — member NOT NULL('') 로 NULL 미포함 멱등키 (Postgres UNIQUE 는 NULL 을 distinct 처리하므로).
    envelope 메타 미보유 — server_metrics 행 참조.
    """

    __tablename__ = "server_disk_error"
    __table_args__ = (
        UniqueConstraint(
            "server_id",
            "device_id",
            "error_kind",
            "error_class",
            "member",
            "collected_at",
            name="uq_server_disk_error_nk",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    error_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    error_class: Mapped[str] = mapped_column(String(64), nullable=False)
    member: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"))

    count: Mapped[int | None] = mapped_column(BigInteger)
