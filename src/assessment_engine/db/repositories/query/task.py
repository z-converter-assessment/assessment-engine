"""Task 조회 도메인 concrete — modal · timeline · 서버별 latest."""

from typing import TYPE_CHECKING, Any, override

from sqlalchemy import text

from assessment_engine.db.dtos.outbound import TaskRow
from assessment_engine.db.repositories.query._base import _BaseQueryMixin
from assessment_engine.db.repositories.query.base_task import BaseTaskQueryRepository

if TYPE_CHECKING:
    from datetime import datetime


class TaskQueryRepository(_BaseQueryMixin, BaseTaskQueryRepository):
    @override
    async def get_task_by_public_id(self, public_id: str) -> TaskRow | None:
        sql = text("""
            SELECT
                t.public_id, t.target_server_id, t.task_type, t.status,
                t.created_at, t.deadline_at, t.completed_at,
                t.failure_reason, t.exit_code, t.signal_no, t.duration_ms,
                t.stdout_tail, t.stderr_tail, t.params,
                s.public_id AS target_public_id, s.hostname AS target_hostname
            FROM tasks t
            LEFT JOIN server_inventory s ON s.id = t.target_server_id
            WHERE t.public_id = :pid
        """)
        result = await self.session.execute(sql, {"pid": public_id})
        row = result.first()
        if row is None:
            return None
        return self._row_to_task(row)

    @override
    async def list_recent_tasks(
        self,
        target_server_id: int,
        limit: int,
        cursor: datetime | None = None,
    ) -> list[TaskRow]:
        # cursor: created_at < cursor 조건 (시간 역순 pagination). None 이면 첫 페이지.
        # ORDER BY 에 id DESC tie-breaker — 같은 created_at microsecond 에 다중 INSERT 시 정렬 안정성.
        params: dict[str, Any] = {"sid": target_server_id, "lim": limit}
        cursor_clause = ""
        if cursor is not None:
            cursor_clause = " AND t.created_at < :cursor"
            params["cursor"] = cursor
        sql = text(f"""
            SELECT
                t.public_id, t.target_server_id, t.task_type, t.status,
                t.created_at, t.deadline_at, t.completed_at,
                t.failure_reason, t.exit_code, t.signal_no, t.duration_ms,
                t.stdout_tail, t.stderr_tail, t.params,
                s.public_id AS target_public_id, s.hostname AS target_hostname
            FROM tasks t
            LEFT JOIN server_inventory s ON s.id = t.target_server_id
            WHERE t.target_server_id = :sid{cursor_clause}
            ORDER BY t.created_at DESC, t.id DESC
            LIMIT :lim
        """)
        result = await self.session.execute(sql, params)
        return [self._row_to_task(r) for r in result.all()]

    @override
    async def latest_tasks_by_servers(
        self,
        server_ids: list[int],
    ) -> dict[int, TaskRow]:
        if not server_ids:
            return {}
        # DISTINCT ON 서버별 최근 1건. id DESC tie-breaker — 같은 created_at microsecond INSERT 정렬 안정성.
        sql = text("""
            SELECT DISTINCT ON (t.target_server_id)
                t.public_id, t.target_server_id, t.task_type, t.status,
                t.created_at, t.deadline_at, t.completed_at,
                t.failure_reason, t.exit_code, t.signal_no, t.duration_ms,
                t.stdout_tail, t.stderr_tail, t.params,
                s.public_id AS target_public_id, s.hostname AS target_hostname
            FROM tasks t
            LEFT JOIN server_inventory s ON s.id = t.target_server_id
            WHERE t.target_server_id = ANY(:sids)
            ORDER BY t.target_server_id, t.created_at DESC, t.id DESC
        """)
        result = await self.session.execute(sql, {"sids": server_ids})
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
