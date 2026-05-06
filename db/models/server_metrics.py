from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class ServerMetrics(Base):
    __tablename__ = "server_metrics"
    __table_args__ = (
        UniqueConstraint("server_id", "collected_at", name="uq_server_metrics_sid_ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)

    cpu_user: Mapped[int | None] = mapped_column(BigInteger)
    cpu_nice: Mapped[int | None] = mapped_column(BigInteger)
    cpu_system: Mapped[int | None] = mapped_column(BigInteger)
    cpu_idle: Mapped[int | None] = mapped_column(BigInteger)
    cpu_iowait: Mapped[int | None] = mapped_column(BigInteger)
    cpu_irq: Mapped[int | None] = mapped_column(BigInteger)
    cpu_softirq: Mapped[int | None] = mapped_column(BigInteger)
    cpu_steal: Mapped[int | None] = mapped_column(BigInteger)

    mem_total_kb: Mapped[int | None] = mapped_column(BigInteger)
    mem_free_kb: Mapped[int | None] = mapped_column(BigInteger)
    mem_available_kb: Mapped[int | None] = mapped_column(BigInteger)
    mem_buffers_kb: Mapped[int | None] = mapped_column(BigInteger)
    mem_cached_kb: Mapped[int | None] = mapped_column(BigInteger)
    swap_total_kb: Mapped[int | None] = mapped_column(BigInteger)
    swap_free_kb: Mapped[int | None] = mapped_column(BigInteger)

    load_1m: Mapped[float | None] = mapped_column(Float)
    load_5m: Mapped[float | None] = mapped_column(Float)
    load_15m: Mapped[float | None] = mapped_column(Float)