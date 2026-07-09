from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class ServerPressure(Base):
    """PSI (Pressure Stall Information) 시계열 — Linux 4.20+ (wire v2 system.pressure).

    stall_time_s 는 counter(s, counter_agg reset-safe) — 14일 saturation 판정 canonical(자원이 실제로
    부족해 태스크가 멈춘 시간). ratio_avg10/60/300 는 gauge(0~1, 실시간 참고). NK 축 = resource x scope
    (window 10/60/300 은 ratio 컬럼으로 평탄화 — 행 폭발 회피). Windows 는 PSI 미지원이라 행 없음.
    envelope 메타 미보유 — server_metrics 행 참조.
    """

    __tablename__ = "server_pressure"
    __table_args__ = (
        UniqueConstraint("server_id", "resource", "scope", "collected_at", name="uq_server_pressure_sid_res_scope_ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)
    resource: Mapped[str] = mapped_column(String(16), nullable=False)  # cpu|memory|io
    scope: Mapped[str] = mapped_column(String(8), nullable=False)  # some|full

    stall_time_s: Mapped[float | None] = mapped_column(Float)  # pressure.stall.time (s counter — 포화 canonical)
    ratio_avg10: Mapped[float | None] = mapped_column(Float)  # pressure.stall.ratio window=10 (gauge)
    ratio_avg60: Mapped[float | None] = mapped_column(Float)
    ratio_avg300: Mapped[float | None] = mapped_column(Float)
