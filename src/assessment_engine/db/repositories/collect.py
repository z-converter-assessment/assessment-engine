"""Consumer 측 데이터 접근 Protocol + 그 반환 타입.

핸들러가 구현이 아니라 이 인터페이스에 의존하게 해서, 대역으로 갈아끼울 수 있게 한다.
"""

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
    """record_metrics 결과 — 시계열 테이블 각각 INSERT된 행 수. 멱등성 충돌 시 0."""

    metrics: int
    disk_io: int
    net_io: int
    filesystem: int
    cpu_core: int = 0  # Linux only
    pressure: int = 0  # PSI — Linux 4.20+ only
    disk_error: int = 0


class CollectRepository(Protocol):
    """Consumer 측 데이터 접근 인터페이스. 트랜잭션 경계는 호출자(`_db_retry`)가 관리."""

    async def find_server_id(self, agent_id: str) -> int | None: ...

    async def upsert_server(self, data: ServerInventoryCreate) -> int:
        """agent_id UNIQUE 키 ON CONFLICT DO UPDATE upsert. server_inventory.id 반환.

        부수효과: 직전 행 대비 변경(또는 신규) 감지 시 server_inventory_history append.
        정적 정보가 같으면 주기 재발행이라도 history 를 남기지 않는다 — noise 차단.
        """
        ...

    async def ensure_server_id(
        self,
        agent_id: str,
        fallback: ServerInventoryCreate,
    ) -> tuple[int, bool]:
        """metrics 핸들러 auto-register 캡슐화 — find 후 없으면 fallback upsert.

        반환 (server_id, auto_registered). auto_registered=True 면 placeholder 신규 등록이라
        호출자가 운영 로그를 남긴다.
        """
        ...

    async def create_task(self, data: TaskCreate) -> str:
        """task 1건 INSERT. 반환: public_id (UUID) — agent 에 노출되는 식별자."""
        ...

    async def complete_task(self, data: TaskResultUpdate) -> bool: ...

    async def expire_overdue_tasks(self, server_ids: list[int]) -> int:
        """deadline 경과 pending(install) 을 failure(timeout) 로 전이. 반환: 전이 건수.

        발행 경로가 INSERT 직전 호출 — 만료 pending 을 치워야 pending 부분 UNIQUE 충돌 없이 재발행된다.
        race-safe (WHERE status='pending'). agent 의 뒤늦은 result 는 complete_task 가 덮어쓴다.
        """
        ...

    async def find_pending_deadline_servers(self, server_ids: list[int]) -> list[int]: ...

    async def expire_all_overdue_tasks(self) -> int:
        """`expire_overdue_tasks` 의 server_ids 무필터 전역판. 반환: 전이 건수.

        reaper 루프가 다음 emit 없이도 미배달·무회신 pending 을 terminal 로 보낸다. race-safe.
        """
        ...

    async def record_metrics(
        self,
        server_id: int,
        data: ServerMetricCreate,
    ) -> MetricInsertResult:
        """metrics 메시지 1건을 host 집계 + 6개 자식 시계열 테이블에 INSERT. 빈 list 차원은 skip.

        모두 ON CONFLICT DO NOTHING — 자연키 UNIQUE 가 중복을 흡수한다.
        """
        ...
