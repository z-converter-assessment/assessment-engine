from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class ServerNetIo(Base):
    __tablename__ = "server_net_io"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)
    interface: Mapped[str] = mapped_column(String(64), nullable=False)

    rx_bytes: Mapped[int | None] = mapped_column(BigInteger)
    tx_bytes: Mapped[int | None] = mapped_column(BigInteger)
    rx_packets: Mapped[int | None] = mapped_column(BigInteger)
    tx_packets: Mapped[int | None] = mapped_column(BigInteger)
    rx_errors: Mapped[int | None] = mapped_column(Integer)
    tx_errors: Mapped[int | None] = mapped_column(Integer)