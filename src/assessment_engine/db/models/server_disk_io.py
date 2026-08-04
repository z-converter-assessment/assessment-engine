from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class ServerDiskIo(Base):
    """디바이스별 디스크 IO 시계열 (wire system.disk).

    device_id = 안정 id 문자열("<scheme>:<value>", 이름 아님 — 재부팅/재발급 무관). counter_agg 로
    reset-safe 집계 (#C5). io_time/operation_time 은 s(Float) counter — %util·await 산출 원자료.
    envelope 메타(boot_time)는 미보유 — 동일 (server_id, collected_at) 의 server_metrics 행 참조.
    """

    __tablename__ = "server_disk_io"
    __table_args__ = (UniqueConstraint("server_id", "device_id", "collected_at", name="uq_server_disk_io_sid_dev_ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)  # 안정키 (dm/partuuid/wwid/serial/..)
    device_name: Mapped[str | None] = mapped_column(String(128))  # 표시명 (sda·nvme0n1·PhysicalDrive0)

    io_read_bytes: Mapped[int | None] = mapped_column(BigInteger)  # disk.io direction=read (By counter)
    io_write_bytes: Mapped[int | None] = mapped_column(BigInteger)
    ops_read: Mapped[int | None] = mapped_column(BigInteger)  # disk.operations direction=read (counter)
    ops_write: Mapped[int | None] = mapped_column(BigInteger)
    io_time_s: Mapped[float | None] = mapped_column(Float)  # disk.io_time (%util 산출, s counter)
    op_read_time_s: Mapped[float | None] = mapped_column(Float)  # disk.operation_time read (await 산출, s counter)
    op_write_time_s: Mapped[float | None] = mapped_column(Float)
    pending_ops: Mapped[float | None] = mapped_column(Float)  # disk.pending_operations (큐 깊이 gauge)
