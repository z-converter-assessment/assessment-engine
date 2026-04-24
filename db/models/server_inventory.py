from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from db.models.server_metrics import ServerMetrics

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from db.models.base import Base


class ServerInventory(Base):
    __tablename__ = "server_inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 불변 식별자 — inventory message 기준 upsert key
    machine_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # 가변 — 최신 inventory 값으로 갱신
    hostname: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # OS — /etc/os-release 없는 구형 시스템 대비 nullable
    os_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # uname(2) 보장 — not null
    kernel_version: Mapped[str] = mapped_column(String(64), nullable=False)

    # CPU
    cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False)
    cpu_model: Mapped[str | None] = mapped_column(String(255), nullable=True)  # x86_64 파싱 실패 대비

    # Memory
    mem_total_mb: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Network — ip_external: None=수집 실패, []=external IP 없음
    ip_internal: Mapped[list] = mapped_column(JSONB, nullable=False)
    ip_external: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Disk — 빈 배열도 유효
    disks: Mapped[list] = mapped_column(JSONB, nullable=False)
    disk_usage: Mapped[list] = mapped_column(JSONB, nullable=False)

    agent_version: Mapped[str] = mapped_column(String(32), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    snapshots: Mapped[List[ServerMetrics]] = relationship(
        "ServerMetrics", back_populates="server", cascade="all, delete-orphan"
    )