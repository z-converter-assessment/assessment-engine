from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base
from assessment_engine.json_types import JsonObject


class ServerInventoryHistory(Base):
    """server_inventory append-only 변경 이력.

    upsert_server에서 직전 행과 다를 때만 INSERT (앱 레벨 trigger). server_inventory 컬럼을
    미러링 + 자기 식별자(id, server_id, collected_at)·멱등성 UNIQUE 추가.
    """

    __tablename__ = "server_inventory_history"
    __table_args__ = (UniqueConstraint("server_id", "collected_at", name="uq_server_inv_history_sid_ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)

    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_version: Mapped[str | None] = mapped_column(String(32))

    os_family: Mapped[str | None] = mapped_column(String(16))
    os_id: Mapped[str | None] = mapped_column(String(64))
    os_version: Mapped[str | None] = mapped_column(String(64))
    os_codename: Mapped[str | None] = mapped_column(String(64))
    kernel_version: Mapped[str | None] = mapped_column(String(64))
    arch: Mapped[str | None] = mapped_column(String(32))
    bits: Mapped[int | None] = mapped_column(Integer)
    boot_firmware: Mapped[str | None] = mapped_column(String(8))
    secure_boot: Mapped[bool | None] = mapped_column(Boolean)
    edition: Mapped[str | None] = mapped_column(String(64))
    product_name: Mapped[str | None] = mapped_column(String(128))
    timezone: Mapped[str | None] = mapped_column(String(64))
    rtc_utc: Mapped[bool | None] = mapped_column(Boolean)

    cpu_cores: Mapped[int | None] = mapped_column(Integer)
    cpu_model: Mapped[str | None] = mapped_column(String(255))
    mem_total_bytes: Mapped[int | None] = mapped_column(BigInteger)

    boot_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    block_devices: Mapped[list[JsonObject] | None] = mapped_column(JSONB)
    net_interfaces: Mapped[list[JsonObject] | None] = mapped_column(JSONB)
    lvm_vgs: Mapped[list[JsonObject] | None] = mapped_column(JSONB)
    boot: Mapped[JsonObject | None] = mapped_column(JSONB)
    nonblock_mounts: Mapped[list[JsonObject] | None] = mapped_column(JSONB)
    ip_external: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    services: Mapped[list[JsonObject] | None] = mapped_column(JSONB)
    listen_ports: Mapped[list[JsonObject] | None] = mapped_column(JSONB)
