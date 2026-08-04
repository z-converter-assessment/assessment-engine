from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class ServerNetIo(Base):
    """인터페이스별 네트워크 IO 시계열 (wire system.network).

    iface_id = 안정키 = MAC("mac:.."). rx/tx bytes·packets·errors·dropped 는 counter (counter_agg
    reset-safe). link_speed_bps 는 bit/s gauge — 10Gbps(1e10) > int32 라 BigInteger 필수. virtio 는 null.
    envelope 메타(boot_time)는 미보유 — server_metrics 행 참조.
    """

    __tablename__ = "server_net_io"
    __table_args__ = (UniqueConstraint("server_id", "iface_id", "collected_at", name="uq_server_net_io_sid_iface_ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)
    iface_id: Mapped[str] = mapped_column(String(64), nullable=False)  # 안정키 (mac:..)
    # Windows 인터페이스는 WFP/QoS 필터 드라이버 체인 이름이라 김 (NDIS 한계 256).
    iface_name: Mapped[str | None] = mapped_column(String(256))

    rx_bytes: Mapped[int | None] = mapped_column(BigInteger)
    tx_bytes: Mapped[int | None] = mapped_column(BigInteger)
    rx_packets: Mapped[int | None] = mapped_column(BigInteger)
    tx_packets: Mapped[int | None] = mapped_column(BigInteger)
    rx_errors: Mapped[int | None] = mapped_column(BigInteger)
    tx_errors: Mapped[int | None] = mapped_column(BigInteger)
    rx_dropped: Mapped[int | None] = mapped_column(BigInteger)  # 링 버퍼 오버런·경로 손실 품질 신호
    tx_dropped: Mapped[int | None] = mapped_column(BigInteger)
    link_speed_bps: Mapped[int | None] = mapped_column(BigInteger)  # network.link.speed (bit/s gauge)
