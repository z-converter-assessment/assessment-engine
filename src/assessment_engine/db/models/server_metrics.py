from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class ServerMetrics(Base):
    """호스트 집계 시계열 (wire system.*).

    단위 canonical — CPU 시간 s(Float, cpu.time attr.cpu 합산), 메모리 By(BigInteger). 카운터(cpu 시간·
    paging·oom·tcp 재전송)는 counter_agg 로 reset-safe 집계 (#C5) — 재부팅/재시작 gate 불요.

    boot_time / agent_started_at 는 본 테이블에만 둔다 (수집 1회당 1행 = envelope) — 재부팅·에이전트 재시작
    관측 신호 (`_log_time_invariants`·`_track_agent_restart`). 자식 시계열(disk_io·net_io·filesystem·cpu_core·
    pressure·disk_error)은 동일 (server_id, collected_at) 로 본 행을 참조 — 메타 N중복 회피 (cpu_core 와 동일 규약).
    """

    __tablename__ = "server_metrics"
    __table_args__ = (UniqueConstraint("server_id", "collected_at", name="uq_server_metrics_sid_ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)

    cpu_user_s: Mapped[float | None] = mapped_column(Float)
    cpu_nice_s: Mapped[float | None] = mapped_column(Float)
    cpu_system_s: Mapped[float | None] = mapped_column(Float)
    cpu_idle_s: Mapped[float | None] = mapped_column(Float)
    cpu_iowait_s: Mapped[float | None] = mapped_column(Float)
    cpu_irq_s: Mapped[float | None] = mapped_column(Float)
    cpu_softirq_s: Mapped[float | None] = mapped_column(Float)
    cpu_steal_s: Mapped[float | None] = mapped_column(Float)
    cpu_logical_count: Mapped[int | None] = mapped_column(Integer)
    cpu_run_queue: Mapped[float | None] = mapped_column(Float)
    cpu_blocked: Mapped[float | None] = mapped_column(Float)  # cpu.blocked (D-state gauge — IO 대기 근본원인)
    cpu_mce: Mapped[int | None] = mapped_column(BigInteger)

    # 메모리 (By). available = 실효 여유, limit = 물리 총량. commit = Windows 커밋 차지.
    mem_free_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mem_cached_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mem_buffered_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mem_available_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mem_used_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mem_limit_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mem_commit_usage_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mem_commit_limit_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mem_hardware_corrupted_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mem_oom_kill: Mapped[int | None] = mapped_column(BigInteger)

    # paging (counter) — in/out = 스왑 방향, major = 하드 폴트(디스크 재적재). Linux 메모리 포화 주신호.
    paging_in: Mapped[int | None] = mapped_column(BigInteger)
    paging_out: Mapped[int | None] = mapped_column(BigInteger)
    paging_major: Mapped[int | None] = mapped_column(BigInteger)

    net_tcp_retransmits: Mapped[int | None] = mapped_column(BigInteger)
    net_conntrack_usage: Mapped[int | None] = mapped_column(Integer)
    net_conntrack_limit: Mapped[int | None] = mapped_column(Integer)

    boot_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
