from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class ServerCpuCore(Base):
    """per-core CPU 시간 시계열 — 단일스레드 병목 감지용 (wire cpu.time attr.cpu=N).

    어느 코어든 이용률 p95 가 임계(CPU_PERCORE_HOLD_PCT) 이상이면 집계 평균은 낮아도 다운사이즈/
    유휴 판정을 보류한다(단일스레드 앱 보호). server_metrics(host 집계)와 별개 정규화 테이블 —
    코어별 counter_agg cagg 로 reset 일률 처리 (#C5).

    core_id = cpu.time attr.cpu 논리 코어 인덱스. Windows 는 per-core 미발행이라 행 없음(Linux 전용).
    envelope 메타(boot_time)는 미보유 — 동일 (server_id, collected_at) 의 server_metrics 행 참조
    (코어 수만큼 중복 저장 회피 — 자식 시계열 공통 규약).
    """

    __tablename__ = "server_cpu_core"
    __table_args__ = (UniqueConstraint("server_id", "core_id", "collected_at", name="uq_server_cpu_core_sid_core_ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)
    core_id: Mapped[int] = mapped_column(Integer, nullable=False)

    cpu_user_s: Mapped[float | None] = mapped_column(Float)
    cpu_nice_s: Mapped[float | None] = mapped_column(Float)
    cpu_system_s: Mapped[float | None] = mapped_column(Float)
    cpu_idle_s: Mapped[float | None] = mapped_column(Float)
    cpu_iowait_s: Mapped[float | None] = mapped_column(Float)
    cpu_irq_s: Mapped[float | None] = mapped_column(Float)
    cpu_softirq_s: Mapped[float | None] = mapped_column(Float)
    cpu_steal_s: Mapped[float | None] = mapped_column(Float)
