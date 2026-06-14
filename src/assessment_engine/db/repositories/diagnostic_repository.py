from datetime import timedelta

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import array as pg_array
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from assessment_engine.db.dtos.inbound import DiagnosticJobCreate
from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
from assessment_engine.db.models.diagnostic_job import DiagnosticJob
from assessment_engine.db.repositories.base_diagnostic_repository import BaseDiagnosticRepository


class DiagnosticRepository(BaseDiagnosticRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def enqueue(self, job: DiagnosticJobCreate) -> str | None:
        # active partial UNIQUE = (scope, input_hash, job_type). 충돌 시 do_nothing → returning None.
        # index_where 명시 의무 — partial unique index는 column만으로 자동 매칭 안 되고
        # WHERE 조건이 정확히 일치해야 한다.
        stmt = (
            pg_insert(DiagnosticJob)
            .values(
                scope=job.scope,
                job_type=job.job_type,
                input_params=job.input_params,
                input_hash=job.input_hash,
                requested_by=job.requested_by,
                status="pending",
                progress_stage="queued",
            )
            .on_conflict_do_nothing(
                index_elements=["scope", "input_hash", "job_type"],
                index_where=text("status IN ('pending', 'running')"),
            )
            .returning(DiagnosticJob.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_by_hash(
        self,
        scope: str,
        input_hash: str,
        job_type: str,
    ) -> str | None:
        stmt = (
            select(DiagnosticJob.id)
            .where(
                DiagnosticJob.scope == scope,
                DiagnosticJob.input_hash == input_hash,
                DiagnosticJob.job_type == job_type,
                DiagnosticJob.status.in_(("pending", "running")),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, job_id: str) -> DiagnosticJobRecord | None:
        stmt = select(DiagnosticJob).where(DiagnosticJob.id == job_id)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        return _row_to_diagnostic_record(row) if row is not None else None

    async def mark_succeeded(self, job_id: str, result: dict) -> None:
        stmt = (
            update(DiagnosticJob)
            .where(DiagnosticJob.id == job_id)
            .values(
                status="succeeded",
                result=result,
                finished_at=func.now(),
                progress_stage=None,
            )
        )
        await self.session.execute(stmt)

    async def list_recent(
        self,
        days: int,
        scope: str | None = None,
        server_public_ids: list[str] | None = None,
        job_type: str | None = None,
        limit: int = 200,
    ) -> list[DiagnosticJobRecord]:
        # days=0 sentinel = 전체 (시간 필터 생략). retention 미적용 시 모든 row 반환 — limit=200 cap 으로 보호.
        stmt = select(DiagnosticJob)
        if days > 0:
            stmt = stmt.where(
                DiagnosticJob.created_at > func.now() - timedelta(days=days),
            )
        if scope:
            stmt = stmt.where(DiagnosticJob.scope == scope)
        if job_type:
            stmt = stmt.where(DiagnosticJob.job_type == job_type)
        if server_public_ids:
            # 단수 키(server scope 1대) 또는 복수 키(server scope N대) 중 하나라도 매칭하면 hit.
            # environment scope는 두 키 모두 없어 자연 제외.
            # 복수 키는 JSONB ?| 연산자로 array element 중 하나라도 일치하는지 검사.
            single_match = DiagnosticJob.input_params["server_public_id"].astext.in_(
                server_public_ids,
            )
            multi_match = DiagnosticJob.input_params["server_public_ids"].op("?|")(
                pg_array(server_public_ids),
            )
            stmt = stmt.where(or_(single_match, multi_match))
        stmt = stmt.order_by(DiagnosticJob.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return [_row_to_diagnostic_record(row) for row in result.scalars().all()]

    async def delete_retention(self, older_than_days: int) -> int:
        stmt = delete(DiagnosticJob).where(DiagnosticJob.finished_at < func.now() - timedelta(days=older_than_days))
        result = await self.session.execute(stmt)
        return result.rowcount or 0


def _row_to_diagnostic_record(row: DiagnosticJob) -> DiagnosticJobRecord:
    return DiagnosticJobRecord(
        id=row.id,
        job_type=row.job_type,
        scope=row.scope,
        input_params=row.input_params,
        input_hash=row.input_hash,
        status=row.status,
        progress_stage=row.progress_stage,
        result=row.result,
        error_message=row.error_message,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        requested_by=row.requested_by,
    )
