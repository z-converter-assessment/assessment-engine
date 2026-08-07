"""Task 조회 mixin — public_id 기준 task 상세·최근 목록·서버별 최신 task."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from assessment_engine.web.services.mappers.task import to_task_detail, to_task_summary
from assessment_engine.web.services.query._base import _BaseQueryServiceMixin

if TYPE_CHECKING:
    from assessment_engine.db.repositories.query import QueryRepository
    from assessment_engine.web.view_models.task import TaskDetailItem, TaskSummaryItem


async def latest_task_summaries(repo: QueryRepository, server_ids: list[int]) -> dict[int, TaskSummaryItem]:
    """서버별 최근 task 1건 — 목록 행의 마지막 작업 칸.

    서버 도메인이 목록을 그릴 때 같은 것을 필요로 한다. mixin 메서드였을 때는 그 호출이 형제 호출이라
    `self` 를 Protocol 로 좁혀야 했고, 그 Protocol 은 런타임에 아무것도 강제하지 않았다.
    """
    rows = await repo.get_latest_tasks_by_servers(server_ids)
    now = datetime.now(UTC)
    return {sid: to_task_summary(r, now) for sid, r in rows.items()}


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

    async def get_latest_tasks_by_servers(
        self,
        server_ids: list[int],
    ) -> dict[int, TaskSummaryItem]:
        return await latest_task_summaries(self.repo, server_ids)
