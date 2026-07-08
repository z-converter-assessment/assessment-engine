from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class ServerNetIo(Base):
    __tablename__ = "server_net_io"
    __table_args__ = (UniqueConstraint("server_id", "interface", "collected_at", name="uq_server_net_io_sid_iface_ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)
    # Windows 인터페이스는 WFP/QoS 필터 드라이버 체인 이름이라 김 (NDIS 한계 256).
    interface: Mapped[str] = mapped_column(String(256), nullable=False)

    rx_bytes: Mapped[int | None] = mapped_column(BigInteger)
    tx_bytes: Mapped[int | None] = mapped_column(BigInteger)
    rx_packets: Mapped[int | None] = mapped_column(BigInteger)
    tx_packets: Mapped[int | None] = mapped_column(BigInteger)
    rx_errors: Mapped[int | None] = mapped_column(Integer)
    tx_errors: Mapped[int | None] = mapped_column(Integer)
    # ADR 0052 — 드롭 (링 버퍼 오버런·경로 손실 품질 신호, virtio 게스트도 유의미). 누적.
    rx_drops: Mapped[int | None] = mapped_column(BigInteger)
    tx_drops: Mapped[int | None] = mapped_column(BigInteger)
    # kind — physical/loopback/bridge/veth/bond_*/vlan/tunnel/virtual (Windows coarse). 물리 집계 필터 신호.
    kind: Mapped[str | None] = mapped_column(String(32))

    # counter reset 정밀 식별용 (server_metrics와 동일 정책).
    boot_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
