from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class ServerInventory(Base):
    """등록 호스트 인벤토리.

    식별 단일 키 = `composite_id` (UNIQUE, #C1). `machine_id` 는 표시 전용,
    `hostname` 은 display field (UNIQUE X), `public_id` 는 URL 노출용 (ADR 0022 정정).
    """

    __tablename__ = "server_inventory"
    __table_args__ = (UniqueConstraint("composite_id", name="uq_server_inventory_composite_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        server_default=text("gen_random_uuid()"),
        unique=True,
        nullable=False,
    )
    # 호스트 식별 단일 키 (#C1) — agent 매칭·라우팅 (MQ queue·routing key 도 본 값).
    composite_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # raw machine-id (Linux /etc/machine-id, Windows MachineGuid). 표시 전용 — 식별 미사용.
    machine_id: Mapped[str | None] = mapped_column(String(64))
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_version: Mapped[str | None] = mapped_column(String(32))

    # OS family (linux / windows) — task.install OS 별 dispatch 단일 진실 (ADR 0020).
    os_family: Mapped[str | None] = mapped_column(String(16))
    os_id: Mapped[str | None] = mapped_column(String(64))
    os_version: Mapped[str | None] = mapped_column(String(64))
    os_codename: Mapped[str | None] = mapped_column(String(64))
    kernel_version: Mapped[str | None] = mapped_column(String(64))

    cpu_cores: Mapped[int | None] = mapped_column(Integer)
    cpu_model: Mapped[str | None] = mapped_column(String(255))
    mem_total_kb: Mapped[int | None] = mapped_column(BigInteger)
    swap_total_kb: Mapped[int | None] = mapped_column(BigInteger)

    boot_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ip_internal: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    ip_external: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    # NIC MAC 목록 (clone collision 감사용 raw 보존). 식별 미사용.
    mac_addresses: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    disks: Mapped[list[Any] | None] = mapped_column(JSONB)
    mounts: Mapped[list[Any] | None] = mapped_column(JSONB)
    services: Mapped[list[Any] | None] = mapped_column(JSONB)
    listen_ports: Mapped[list[Any] | None] = mapped_column(JSONB)

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
