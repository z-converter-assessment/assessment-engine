"""Task 조회 도메인 추상 인터페이스 — 운영자 가시성 (modal · timeline · 서버별 latest)."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from assessment_engine.db.dtos.outbound import TaskRow


class TaskQueryRepository(Protocol):
    async def get_task_by_public_id(self, public_id: str) -> TaskRow | None:
        """단일 task 조회 — task_id (UUID) 기준. API + modal 디버깅."""
        ...

    async def list_recent_tasks(
        self,
        target_server_id: int,
        limit: int,
        cursor: datetime | None = None,
    ) -> list[TaskRow]:
        """한 서버의 task timeline — created_at 역순. cursor < created_at WHERE 조건 (E2)."""
        ...

    async def get_latest_tasks_by_servers(
        self,
        server_ids: list[int],
    ) -> dict[int, TaskRow]:
        """서버별 가장 최근 task 1건 — DISTINCT ON (target_server_id). list 행별 표시 source."""
        ...
