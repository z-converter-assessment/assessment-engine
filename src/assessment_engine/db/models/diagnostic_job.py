from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class DiagnosticJob(Base):
    """보고서 발행 job — 웹이 enqueue, 발행 시점 정적 스냅샷을 result 에 보존 (이력 보존).

    제약 1개:
    - active partial UNIQUE — (scope, input_hash, job_type)가 pending/running 상태로 동시 1건만.
      더블클릭·중복 enqueue 차단. job_type 까지 포함해 같은 대상 다른 양식 보고서 동시 발행 허용.
      충돌 시 service가 IntegrityError catch → 기존 job_id 반환.

    id는 UUID PK — 라우터 path param에 직접 노출 (E5 정수 PK 노출 금지). 일반 테이블이라
    별도 public_id 컬럼 없이 id 자체가 외부 식별자.

    job_type:
    - 'customer_report' — 고객 제출용 보고서 (양식 A)
    - 'engineer_report' — 엔지니어 검토용 보고서 (양식 B)
    """

    __tablename__ = "diagnostic_jobs"
    __table_args__ = (
        Index(
            "uq_diagnostic_jobs_active_per_input",
            "scope",
            "input_hash",
            "job_type",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    job_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="customer_report",
    )
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    input_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    progress_stage: Mapped[str | None] = mapped_column(String(32))

    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[str | None] = mapped_column(String(64))
