from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base
from assessment_engine.json_types import JsonObject


class ServerInventory(Base):
    """등록 호스트 인벤토리 (wire inventory).

    식별 단일 키 = `agent_id` (UUID, UNIQUE, #C1) — 부팅 무관 불변. `composite_id`/`machine_id` 는
    감사·표시 전용, `hostname` 은 display field (UNIQUE X), `public_id` 는 URL 노출용.
    """

    __tablename__ = "server_inventory"
    __table_args__ = (
        UniqueConstraint("agent_id", name="uq_server_inventory_agent_id"),
        Index("ix_server_inventory_service_categories", "service_categories", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        server_default=text("gen_random_uuid()"),
        unique=True,
        nullable=False,
    )

    agent_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    composite_id: Mapped[str | None] = mapped_column(String(64))
    machine_id: Mapped[str | None] = mapped_column(String(64))  # Linux /etc/machine-id, Windows MachineGuid
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_version: Mapped[str | None] = mapped_column(String(32))

    # OS family (linux / windows) — task.install OS 별 dispatch 단일 진실.
    os_family: Mapped[str | None] = mapped_column(String(16))
    os_id: Mapped[str | None] = mapped_column(String(64))
    os_version: Mapped[str | None] = mapped_column(String(64))
    os_codename: Mapped[str | None] = mapped_column(String(64))
    kernel_version: Mapped[str | None] = mapped_column(String(64))

    arch: Mapped[str | None] = mapped_column(String(32))
    bits: Mapped[int | None] = mapped_column(Integer)
    boot_firmware: Mapped[str | None] = mapped_column(String(8))
    secure_boot: Mapped[bool | None] = mapped_column(Boolean)
    edition: Mapped[str | None] = mapped_column(String(64))  # Windows EditionID (Linux null)
    # CurrentVersion ProductName 원문(Windows only, Linux null) — os_display 짧은 라벨 파싱 소스.
    product_name: Mapped[str | None] = mapped_column(String(128))
    timezone: Mapped[str | None] = mapped_column(String(64))
    rtc_utc: Mapped[bool | None] = mapped_column(Boolean)

    cpu_cores: Mapped[int | None] = mapped_column(Integer)
    cpu_model: Mapped[str | None] = mapped_column(String(255))
    mem_total_bytes: Mapped[int | None] = mapped_column(BigInteger)  # 단위 By (swap 은 block_devices type=swap)

    boot_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    block_devices: Mapped[list[JsonObject] | None] = mapped_column(JSONB)

    net_interfaces: Mapped[list[JsonObject] | None] = mapped_column(JSONB)
    # LVM VG — [{name,size_bytes,free_bytes,data_percent,metadata_percent}] (Linux 전용).
    lvm_vgs: Mapped[list[JsonObject] | None] = mapped_column(JSONB)
    # boot 재현 서술자 — {kernel_cmdline,root_ref_type,grub_install_target} (Linux; Windows null).
    boot: Mapped[JsonObject | None] = mapped_column(JSONB)
    # 비블록 마운트 — [{source,target,fstype,options,fs_freq,fs_passno}] (tmpfs/nfs/cifs/bind 등, Linux).
    nonblock_mounts: Mapped[list[JsonObject] | None] = mapped_column(JSONB)
    ip_external: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    services: Mapped[list[JsonObject] | None] = mapped_column(JSONB)
    listen_ports: Mapped[list[JsonObject] | None] = mapped_column(JSONB)

    service_categories: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
