from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class ServerInventory(Base):
    """등록 호스트 인벤토리.

    Unique 식별 = `host_id` 단일 (ADR 0022 — composite hash 단일 식별자).
    hostname 은 display field (운영자 변경 가능, UNIQUE 제약 X).
    """

    __tablename__ = "server_inventory"
    __table_args__ = (UniqueConstraint("host_id", name="uq_server_inventory_host_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        server_default=text("gen_random_uuid()"),
        unique=True,
        nullable=False,
    )
    host_id: Mapped[str] = mapped_column(String(64), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_version: Mapped[str | None] = mapped_column(String(32))

    # os_family — agent 가 publish 하는 OS family (linux / windows). task.install 발행 시
    # OS 별 dispatch (install.type / download.url / install.script) 의 단일 진실.
    # Linux agent minor bump 호환 단계라 nullable. agent 배포 완료 후 별도 revision 에서 not-null tighten.
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

    disks: Mapped[list[Any] | None] = mapped_column(JSONB)
    mounts: Mapped[list[Any] | None] = mapped_column(JSONB)
    services: Mapped[list[Any] | None] = mapped_column(JSONB)
    listen_ports: Mapped[list[Any] | None] = mapped_column(JSONB)

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
