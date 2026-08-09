"""Task 조회 도메인 concrete — modal · timeline · 서버별 latest."""

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from assessment_engine.db.dtos.outbound import TaskRow
from assessment_engine.db.models.server_inventory import ServerInventory
from assessment_engine.db.models.task import Task
from assessment_engine.db.repositories.query._base import _BaseQueryMixin

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import Select


_TASK_COLUMNS = (
    Task.public_id,
    Task.target_server_id,
    Task.task_type,
    Task.status,
    Task.created_at,
    Task.deadline_at,
    Task.completed_at,
    Task.failure_reason,
    Task.exit_code,
    Task.signal_no,
    Task.duration_ms,
    Task.stdout_tail,
    Task.stderr_tail,
    Task.params,
    ServerInventory.public_id.label("target_public_id"),
    ServerInventory.hostname.label("target_hostname"),
)


def _task_select() -> Select[Any]:
    return select(*_TASK_COLUMNS).outerjoin(ServerInventory, ServerInventory.id == Task.target_server_id)


class SqlTaskQueryRepository(_BaseQueryMixin):
    async def get_task_by_public_id(self, public_id: str) -> TaskRow | None:
        result = await self.session.execute(_task_select().where(Task.public_id == public_id))
        row = result.first()
        if row is None:
            return None
        return self._row_to_task(row)

    async def list_recent_tasks(
        self,
        target_server_id: int,
        limit: int,
        cursor: datetime | None = None,
    ) -> list[TaskRow]:

        stmt = _task_select().where(Task.target_server_id == target_server_id)
        if cursor is not None:
            stmt = stmt.where(Task.created_at < cursor)
        stmt = stmt.order_by(Task.created_at.desc(), Task.id.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return [self._row_to_task(r) for r in result.all()]

    async def get_latest_tasks_by_servers(
        self,
        server_ids: list[int],
    ) -> dict[int, TaskRow]:
        if not server_ids:
            return {}

        # DISTINCT ON 선두와 ORDER BY 선두가 같아야 한다는 제약은 ORM 이 검사하지 않는다 — 순서 유지.
        stmt = (
            _task_select()
            .where(Task.target_server_id.in_(server_ids))
            .distinct(Task.target_server_id)
            .order_by(Task.target_server_id, Task.created_at.desc(), Task.id.desc())
        )
        result = await self.session.execute(stmt)
        return {r.target_server_id: self._row_to_task(r) for r in result.all()}

    @staticmethod
    def _row_to_task(row: Any) -> TaskRow:
        return TaskRow(
            public_id=str(row.public_id),
            target_server_id=int(row.target_server_id),
            target_public_id=str(row.target_public_id) if row.target_public_id else None,
            target_hostname=row.target_hostname,
            task_type=row.task_type,
            status=row.status,
            created_at=row.created_at,
            deadline_at=row.deadline_at,
            completed_at=row.completed_at,
            failure_reason=row.failure_reason,
            exit_code=row.exit_code,
            signal_no=row.signal_no,
            duration_ms=row.duration_ms,
            stdout_tail=row.stdout_tail,
            stderr_tail=row.stderr_tail,
            params=row.params,
        )
