from abc import ABC, abstractmethod
from dataclasses import dataclass

from assessment_engine.db.dtos.inbound import (
    ServerInventoryCreate,
    ServerMetricCreate,
    TaskCreate,
    TaskResultUpdate,
)


@dataclass
class MetricInsertResult:
    """record_metrics 결과 — 4개 테이블 각각 INSERT된 행 수. 멱등성 충돌 시 0."""

    metrics: int
    disk_io: int
    net_io: int
    mount_usage: int


class BaseCollectRepository(ABC):
    """Consumer 측 데이터 접근 인터페이스. 트랜잭션 경계는 호출자(`_db_retry`)가 관리."""

    @abstractmethod
    async def find_server_id(self, composite_id: str) -> int | None:
        """composite_id 단일 키로 server_inventory.id 조회. 없으면 None."""

    @abstractmethod
    async def upsert_server(self, data: ServerInventoryCreate) -> int:
        """composite_id UNIQUE 키 ON CONFLICT DO UPDATE upsert. server_inventory.id 반환.

        부수효과: 직전 행 대비 변경(또는 신규) 감지 시 server_inventory_history append.
        정적 정보 동일하면 주기 재발행이라도 history 그대로 — noise 차단.
        """

    @abstractmethod
    async def ensure_server_id(
        self,
        composite_id: str,
        fallback: ServerInventoryCreate,
    ) -> tuple[int, bool]:
        """metrics 핸들러 auto-register 캡슐화. find 후 없으면 fallback upsert.

        반환 (server_id, auto_registered). True 면 placeholder 신규 등록(호출자 운영 로그 남김).
        """

    @abstractmethod
    async def create_task(self, data: TaskCreate) -> str:
        """task 1건 INSERT. 반환: public_id (UUID) — agent에 노출되는 식별자."""

    @abstractmethod
    async def complete_task(self, data: TaskResultUpdate) -> bool:
        """결과 보고 수신 UPDATE. 반환: True 정상 / False public_id 미존재 (DLQ·silent ack 결정)."""

    @abstractmethod
    async def expire_overdue_tasks(self, server_ids: list[int]) -> int:
        """deadline 경과 pending(install) 을 failure(timeout) 로 전이. 반환: 전이 건수.

        발행 경로가 INSERT 직전 호출 — 만료 pending 정리로 pending 부분 UNIQUE 충돌(409) 없이 재발행.
        race-safe (WHERE status='pending'). agent 뒤늦은 result 는 complete_task 가 덮어씀.
        """

    @abstractmethod
    async def find_pending_deadline_servers(self, server_ids: list[int]) -> list[int]:
        """deadline 안 지난 활성 pending(install) 보유 server_id 목록.

        발행 경로가 expire 직후 호출 — all-or-nothing 사전 중복 검증. 하나라도 있으면 전체 발행 취소.
        """

    @abstractmethod
    async def record_metrics(
        self,
        server_id: int,
        data: ServerMetricCreate,
    ) -> MetricInsertResult:
        """metrics 메시지 1건을 4개 시계열 테이블에 INSERT. 빈 list 차원은 skip.

        모두 ON CONFLICT DO NOTHING — 멱등성 자연키 흡수.
        """
