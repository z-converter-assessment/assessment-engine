from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import CursorResult, Result, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import array as pg_array
from sqlalchemy.dialects.postgresql import insert as pg_insert

from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
from assessment_engine.db.models.diagnostic_job import DiagnosticJob
from assessment_engine.db.repositories.base_diagnostic_repository import BaseDiagnosticRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from assessment_engine.db.dtos.inbound import DiagnosticJobCreate
    from assessment_engine.json_types import JsonObject


class DiagnosticRepository(BaseDiagnosticRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def enqueue(self, job: DiagnosticJobCreate) -> str | None:
        # active partial UNIQUE = (scope, input_hash, job_type). 충돌 시 returning None.
        # index_where 명시 의무 — partial unique index 는 column 만으로 자동 매칭 안 되고
        # WHERE 조건이 정확히 일치해야 ON CONFLICT 가 인덱스를 잡는다.
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

    async def mark_succeeded(self, job_id: str, result: JsonObject) -> None:
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

    async def claim_next_pending(self) -> DiagnosticJobRecord | None:
        # FOR UPDATE SKIP LOCKED — 다른 워커가 이미 잠근 row 는 건너뛰어 1 job = 1 워커 보장(멀티노드 분산).
        # created_at 오름차순(FIFO). running 마킹까지가 claim 트랜잭션 — 커밋은 워커.
        sel = (
            select(DiagnosticJob)
            .where(DiagnosticJob.status == "pending")
            .order_by(DiagnosticJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        row = (await self.session.execute(sel)).scalar_one_or_none()
        if row is None:
            return None
        row.status = "running"
        row.started_at = func.now()
        row.progress_stage = "running"
        await self.session.flush()
        await self.session.refresh(row)  # started_at func.now() 실제값 반영
        return _row_to_diagnostic_record(row)

    async def mark_failed(self, job_id: str, error_message: str) -> None:
        stmt = (
            update(DiagnosticJob)
            .where(DiagnosticJob.id == job_id)
            .values(
                status="failed",
                error_message=error_message,
                finished_at=func.now(),
                progress_stage=None,
            )
        )
        await self.session.execute(stmt)

    async def recover_stale_running(self, stale_seconds: int) -> int:
        stmt = (
            update(DiagnosticJob)
            .where(
                DiagnosticJob.status == "running",
                DiagnosticJob.started_at < func.now() - timedelta(seconds=stale_seconds),
            )
            .values(status="pending", started_at=None, progress_stage="requeued")
        )
        return _affected_rows(await self.session.execute(stmt))

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
            # 단수 키(1대) 또는 복수 키(N대) 중 하나라도 매칭하면 hit (environment scope 는 두 키 부재로 자연 제외).
            # 복수 키는 JSONB ?| 로 array element 중 하나라도 일치하는지 검사.
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
        return _affected_rows(await self.session.execute(stmt))


def _affected_rows(result: Result[Any]) -> int:
    """UPDATE/DELETE 영향 행 수.

    `session.execute` 는 정적으로 `Result` 를 돌려주지만 DML 은 런타임에 `rowcount` 를 가진
    `CursorResult` 라 좁혀서 읽는다.
    """
    return cast("CursorResult[Any]", result).rowcount or 0


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
