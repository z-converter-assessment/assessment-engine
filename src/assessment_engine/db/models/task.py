from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class Task(Base):
    """원격 작업 명령 + 실행 이력. 영구 보존 (source of truth).

    pending 상태 부분 UNIQUE (target_server_id, task_type) — 동시 다중 pending 차단(더블클릭 방어).
    UniqueViolation은 service가 catch해서 409 반환.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        Index(
            "uq_tasks_pending_per_server_type",
            "target_server_id",
            "task_type",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), nullable=False, server_default=func.gen_random_uuid(), unique=True
    )
    target_server_id: Mapped[int] = mapped_column(Integer, ForeignKey("server_inventory.id"), nullable=False)
    # 발행 대상 호스트 식별자 (agent_id UUID) — 감사·라우팅 대상 기록. MQ 큐/라우팅 키와 동일 값.
    target_agent_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), nullable=False, index=True)

    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # pending -> success / failure. agent 무응답 시 deadline_at 경과로 failure(timeout) 전이.

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # 응답 마감 (install 발행 시 now + install_task_deadline_sec, 그 외 null). 경과 pending 은
    # reaper·재발행 양 경로가 failure(timeout) 로 전이 (부분 UNIQUE 해소).
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # task 종료 시각 — result 메시지 값 그대로 저장 (DB now() 미사용).
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 결과 보고 메시지의 부가 필드. failure_reason 은 wire 계약상 자유 문자열이라 값 집합을 컬럼으로 좁히지 않는다.
    failure_reason: Mapped[str | None] = mapped_column(String(32))
    exit_code: Mapped[int | None] = mapped_column(SmallInteger)
    # install.sh 를 종료시킨 시그널 번호 (WIFSIGNALED). exit_code 와 상호배타 (정상종료=exit_code / 시그널=signal_no).
    signal_no: Mapped[int | None] = mapped_column(SmallInteger)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    stdout_tail: Mapped[str | None] = mapped_column(Text)
    stderr_tail: Mapped[str | None] = mapped_column(Text)
    # task_policy — agent worker 의 실제 설치 성공 판정(데몬 기동+등록 점검). 종료 판정 1순위 raw 보존.
    # exit_code 보다 우선 (non-zero exit 이어도 policy True 면 success, exit 0 이어도 policy False 면 failure).
    # nullable: agent 미보고 시 null -> exit_code 폴백.
    task_policy: Mapped[bool | None] = mapped_column(Boolean)
