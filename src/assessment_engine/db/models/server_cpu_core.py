from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class ServerCpuCore(Base):
    """per-core CPU jiffies — 단일스레드 병목 감지용 (ADR 0052).

    어느 코어든 이용률 p95 가 임계(RS_CPU_PERCORE_HOLD_PCT) 이상이면 집계 평균은 낮아도 다운사이즈/
    유휴 판정을 보류한다(단일스레드 앱 보호). server_metrics(host-wide)와 별개 정규화 테이블 —
    server_disk_io(디바이스별)와 동일 패턴(코어별 counter_agg cagg 로 reset 일률 처리, ADR 0043).

    core_id = /proc/stat cpu0..N 순서(위치 인덱스). Windows 는 per-core 미발행이라 행 없음(Linux 전용).
    boot_time/agent_started_at 미보유 — counter_agg 가 reset-safe 라 불요 + 동일 (server,collected_at) 의
    envelope 메타는 server_metrics 에 이미 있어 코어 수만큼 중복 저장 회피(#C1 4테이블과 의도적 차이).
    """

    __tablename__ = "server_cpu_core"
    __table_args__ = (UniqueConstraint("server_id", "core_id", "collected_at", name="uq_server_cpu_core_sid_core_ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)
    core_id: Mapped[int] = mapped_column(Integer, nullable=False)  # /proc/stat cpuN 인덱스

    # per-core jiffies (host-wide server_metrics 와 동일 성분 — cagg 가 total=COALESCE 성분합, util%=1-idle/total)
    cpu_user: Mapped[int | None] = mapped_column(BigInteger)
    cpu_nice: Mapped[int | None] = mapped_column(BigInteger)
    cpu_system: Mapped[int | None] = mapped_column(BigInteger)
    cpu_idle: Mapped[int | None] = mapped_column(BigInteger)
    cpu_iowait: Mapped[int | None] = mapped_column(BigInteger)
    cpu_irq: Mapped[int | None] = mapped_column(BigInteger)
    cpu_softirq: Mapped[int | None] = mapped_column(BigInteger)
    cpu_steal: Mapped[int | None] = mapped_column(BigInteger)
