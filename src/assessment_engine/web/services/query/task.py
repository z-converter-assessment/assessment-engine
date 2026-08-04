"""Task 조회 mixin — public_id 기준 task 상세·최근 목록·서버별 최신 task."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from assessment_engine.web.services.mappers.task import to_task_detail, to_task_summary
from assessment_engine.web.services.query._base import _BaseQueryServiceMixin

if TYPE_CHECKING:
    from assessment_engine.web.view_models.task import TaskDetailItem, TaskSummaryItem


class TaskQueryMixin(_BaseQueryServiceMixin):
    async def get_task(self, task_id: str) -> TaskDetailItem | None:
        row = await self.repo.get_task_by_public_id(task_id)
        return to_task_detail(row, datetime.now(UTC)) if row else None

    async def list_recent_tasks(
        self,
        server_public_id: str,
        limit: int,
        cursor: datetime | None,
    ) -> list[TaskSummaryItem]:
        sid = await self.repo.resolve_server_id(server_public_id)
        if sid is None:
            return []
        rows = await self.repo.list_recent_tasks(sid, limit, cursor)
        now = datetime.now(UTC)
        return [to_task_summary(r, now) for r in rows]

    async def latest_tasks_by_servers(
        self,
        server_ids: list[int],
    ) -> dict[int, TaskSummaryItem]:
        rows = await self.repo.latest_tasks_by_servers(server_ids)
        now = datetime.now(UTC)
        return {sid: to_task_summary(r, now) for sid, r in rows.items()}
