"""Task 조회 도메인 추상 인터페이스 — 운영자 가시성 (modal · timeline · 서버별 latest)."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from assessment_engine.db.dtos.outbound import TaskRow


class TaskQueryRepository(Protocol):
    async def get_task_by_public_id(self, public_id: str) -> TaskRow | None: ...

    async def list_recent_tasks(
        self,
        target_server_id: int,
        limit: int,
        cursor: datetime | None = None,
    ) -> list[TaskRow]: ...

    async def get_latest_tasks_by_servers(
        self,
        server_ids: list[int],
    ) -> dict[int, TaskRow]: ...
