from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class ServerMountUsage(Base):
    __tablename__ = "server_mount_usage"
    __table_args__ = (UniqueConstraint("server_id", "mount", "collected_at", name="uq_server_mount_usage_sid_mnt_ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)
    mount: Mapped[str] = mapped_column(String(255), nullable=False)

    total_bytes: Mapped[int | None] = mapped_column(BigInteger)
    free_bytes: Mapped[int | None] = mapped_column(BigInteger)
    avail_bytes: Mapped[int | None] = mapped_column(BigInteger)

    # 시계열 4개 테이블 일관성 — server_metrics·server_disk_io·server_net_io와 동일 정책.
    # 본 테이블은 시점값(델타 없음)이라 calculator의 reset 판정엔 직접 활용 안 하지만,
    # 운영 디버깅(특정 mount 행만 보고 재부팅 여부 확인) + 미래 활용 + 메타데이터 균일을 위해 보존.
    boot_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
