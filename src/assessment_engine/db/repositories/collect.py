"""수집 데이터와 task 상태를 저장하는 repository 계약과 반환 데이터."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from assessment_engine.db.dtos.inbound import (
        ServerInventoryCreate,
        ServerMetricCreate,
        TaskCreate,
        TaskResultUpdate,
    )


@dataclass
class MetricInsertResult:
    """Metrics 저장 debug 로그에 기록할 테이블별 신규 레코드 수."""

    metrics: int
    disk_io: int
    net_io: int
    filesystem: int
    cpu_core: int = 0  # Linux only
    pressure: int = 0  # PSI — Linux 4.20+ only
    disk_error: int = 0


class CollectRepository(Protocol):
    """Consumer와 worker가 의존하는 수집 데이터 저장 계약. 트랜잭션은 호출자가 관리한다."""

    async def find_server_id(self, agent_id: str) -> int | None: ...

    async def upsert_server(self, data: ServerInventoryCreate) -> int:
        """agent_id 기준으로 inventory를 저장하고 서버 내부 ID를 반환한다.

        새 서버이거나 inventory가 변경된 경우에만 history 레코드를 추가한다.
        """
        ...

    async def ensure_server_id(
        self,
        agent_id: str,
        fallback: ServerInventoryCreate,
    ) -> tuple[int, bool]:
        """서버 내부 ID를 반환하고, 없으면 fallback inventory로 placeholder를 저장한다.

        두 번째 반환값은 이번 호출에서 placeholder를 새로 만들었는지 나타낸다.
        """
        ...

    async def create_task(self, data: TaskCreate) -> str:
        """task를 저장하고 agent에 전달할 public ID를 반환한다."""
        ...

    async def complete_task(self, data: TaskResultUpdate) -> bool:
        """task 결과를 저장한다. 대상 task가 없으면 False를 반환한다."""
        ...

    async def expire_overdue_tasks(self, server_ids: list[int]) -> int:
        """지정 서버의 deadline 지난 pending task를 timeout failure로 전이하고 전이 건수를 반환한다."""
        ...

    async def find_pending_task_server_ids(self, server_ids: list[int], task_type: str) -> list[int]:
        """지정 task type의 pending task를 가진 서버 ID를 반환한다."""
        ...

    async def expire_all_overdue_tasks(self) -> int:
        """모든 deadline 지난 pending task를 timeout failure로 전이하고 전이 건수를 반환한다."""
        ...

    async def record_metrics(
        self,
        server_id: int,
        data: ServerMetricCreate,
    ) -> MetricInsertResult:
        """metrics 메시지를 서버 집계와 차원별 시계열 레코드로 저장한다.

        자연키가 이미 있는 레코드는 건너뛴다.
        """
        ...
