from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from assessment_engine.db.models.diagnostic_job import DiagnosticJob
from assessment_engine.db.repositories.base_diagnostic_repository import BaseDiagnosticRepository
from assessment_engine.db.repositories.inbound import DiagnosticJobCreate
from assessment_engine.db.repositories.outbound import DiagnosticJobRecord


class DiagnosticRepository(BaseDiagnosticRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def enqueue(self, job: DiagnosticJobCreate) -> str | None:
        # active partial UNIQUE 충돌 시 do_nothing → returning이 None.
        # index_where 명시 의무 — partial unique index는 column만으로 자동 매칭 안 되고
        # WHERE 조건이 정확히 일치해야 한다.
        stmt = (
            pg_insert(DiagnosticJob)
            .values(
                scope=job.scope,
                input_params=job.input_params,
                input_hash=job.input_hash,
                requested_by=job.requested_by,
                status="pending",
                progress_stage="queued",
            )
            .on_conflict_do_nothing(
                index_elements=["scope", "input_hash"],
                index_where=text("status IN ('pending', 'running')"),
            )
            .returning(DiagnosticJob.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_by_hash(self, scope: str, input_hash: str) -> str | None:
        stmt = (
            select(DiagnosticJob.id)
            .where(
                DiagnosticJob.scope == scope,
                DiagnosticJob.input_hash == input_hash,
                DiagnosticJob.status.in_(("pending", "running")),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_succeeded(
        self, scope: str, input_hash: str,
    ) -> DiagnosticJobRecord | None:
        stmt = (
            select(DiagnosticJob)
            .where(
                DiagnosticJob.scope == scope,
                DiagnosticJob.input_hash == input_hash,
                DiagnosticJob.status == "succeeded",
            )
            .order_by(DiagnosticJob.finished_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_record(row) if row is not None else None

    async def get_many_latest_by_context_server(
        self,
        time_range: str,
        server_public_ids: list[str],
    ) -> dict[str, DiagnosticJobRecord]:
        if not server_public_ids:
            return {}
        # DISTINCT ON (server_public_id) — server별 가장 최근 finished_at 1건 선택.
        # PostgreSQL DISTINCT ON은 ORDER BY 첫 컬럼을 DISTINCT 키로 사용.
        pid_expr = DiagnosticJob.input_params["server_public_id"].astext
        stmt = (
            select(DiagnosticJob)
            .where(
                DiagnosticJob.scope == "server",
                DiagnosticJob.status == "succeeded",
                DiagnosticJob.input_params["time_range"].astext == time_range,
                pid_expr.in_(server_public_ids),
            )
            .distinct(pid_expr)
            .order_by(pid_expr, DiagnosticJob.finished_at.desc())
        )
        result = await self.session.execute(stmt)
        rows = result.scalars().all()
        return {row.input_params["server_public_id"]: _to_record(row) for row in rows}

    async def get_latest_by_context(
        self,
        scope: str,
        time_range: str,
        server_public_id: str | None,
    ) -> DiagnosticJobRecord | None:
        # JSONB 필드 매칭 — input_params['time_range'] + (server scope면 server_public_id).
        # input_hash 일치 안 봐도 됨 — anchor 달라도 같은 context는 latest 후보.
        stmt = (
            select(DiagnosticJob)
            .where(
                DiagnosticJob.scope == scope,
                DiagnosticJob.status == "succeeded",
                DiagnosticJob.input_params["time_range"].astext == time_range,
            )
        )
        if scope == "server":
            stmt = stmt.where(
                DiagnosticJob.input_params["server_public_id"].astext == server_public_id,
            )
        stmt = stmt.order_by(DiagnosticJob.finished_at.desc()).limit(1)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_record(row) if row is not None else None

    async def get_by_id(self, job_id: str) -> DiagnosticJobRecord | None:
        stmt = select(DiagnosticJob).where(DiagnosticJob.id == job_id)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_record(row) if row is not None else None

    async def get_many_by_ids(self, job_ids: list[str]) -> list[DiagnosticJobRecord]:
        if not job_ids:
            return []
        stmt = select(DiagnosticJob).where(DiagnosticJob.id.in_(job_ids))
        result = await self.session.execute(stmt)
        return [_to_record(row) for row in result.scalars().all()]

    async def mark_running(self, job_id: str, stage: str) -> None:
        stmt = (
            update(DiagnosticJob)
            .where(DiagnosticJob.id == job_id)
            .values(status="running", started_at=func.now(), progress_stage=stage)
        )
        await self.session.execute(stmt)

    async def update_progress_stage(self, job_id: str, stage: str) -> None:
        stmt = (
            update(DiagnosticJob)
            .where(DiagnosticJob.id == job_id)
            .values(progress_stage=stage)
        )
        await self.session.execute(stmt)

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

    async def list_recent(
        self, days: int, scope: str | None = None,
        server_public_ids: list[str] | None = None, limit: int = 200,
    ) -> list[DiagnosticJobRecord]:
        stmt = select(DiagnosticJob).where(
            DiagnosticJob.created_at > func.now() - text(f"interval '{days} days'"),
        )
        if scope:
            stmt = stmt.where(DiagnosticJob.scope == scope)
        if server_public_ids:
            # input_params JSONB의 server_public_id 추출 후 ANY 매칭.
            # server scope job만 본 키 보유 — environment scope는 자연스럽게 제외됨.
            stmt = stmt.where(
                DiagnosticJob.input_params["server_public_id"].astext.in_(server_public_ids),
            )
        stmt = stmt.order_by(DiagnosticJob.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return [_to_record(row) for row in result.scalars().all()]

    async def delete_retention(self, older_than_days: int) -> int:
        stmt = (
            delete(DiagnosticJob)
            .where(DiagnosticJob.finished_at < func.now() - text(f"interval '{older_than_days} days'"))
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0


def _to_record(row: DiagnosticJob) -> DiagnosticJobRecord:
    return DiagnosticJobRecord(
        id=row.id,
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
