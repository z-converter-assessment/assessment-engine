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
    """record_metrics 결과 — 4개 테이블 각각 INSERT된 행 수.

    ON CONFLICT DO NOTHING이므로 멱등성 충돌 시 0. 관측·디버깅 용도.
    """

    metrics: int  # server_metrics: 0 또는 1
    disk_io: int  # server_disk_io
    net_io: int  # server_net_io
    mount_usage: int  # server_mount_usage


class BaseCollectRepository(ABC):
    """Consumer 측 데이터 접근 인터페이스.

    트랜잭션 경계는 호출자(`_db_retry`)가 관리. 본 인터페이스는 SQL 실행만.
    """

    @abstractmethod
    async def find_server_id(self, host_id: str) -> int | None:
        """host_id 단일 키로 server_inventory.id 조회. 없으면 None."""

    @abstractmethod
    async def upsert_server(self, data: ServerInventoryCreate) -> int:
        """host_id UNIQUE 키 ON CONFLICT DO UPDATE upsert. server_inventory.id 반환.

        부수효과: 직전 행과 비교 시 변경(또는 신규) 감지되면 server_inventory_history에
        한 행 append (앱 레벨 trigger). 1시간 주기 재발행이라도 정적 정보 동일하면 history 그대로.
        """

    @abstractmethod
    async def ensure_server_id(
        self,
        host_id: str,
        fallback: ServerInventoryCreate,
    ) -> tuple[int, bool]:
        """find_server_id 시도 후, 없으면 fallback inventory로 upsert.

        metrics 핸들러의 auto-register 흐름을 캡슐화.

        반환: (server_id, auto_registered).
            - auto_registered=True: host_id 가 server_inventory에 없어서
              fallback으로 새로 등록. 호출자는 placeholder임을 인지하고 운영 로그 남김.
            - auto_registered=False: 기존 server_id 사용. fallback 미사용.
        """

    @abstractmethod
    async def create_task(self, data: TaskCreate) -> str:
        """task 1건 INSERT. 반환: public_id (UUID 문자열). agent에 노출되는 식별자."""

    @abstractmethod
    async def complete_task(self, data: TaskResultUpdate) -> bool:
        """결과 보고 수신 — status / completed_at / failure_reason / exit_code /
        duration_ms / stdout_tail / stderr_tail UPDATE.

        반환: True 정상 update / False public_id 미존재 (DLQ 또는 silent ack 결정).
        """

    @abstractmethod
    async def record_metrics(
        self,
        server_id: int,
        data: ServerMetricCreate,
    ) -> MetricInsertResult:
        """metrics 메시지 1건을 4개 시계열 테이블에 INSERT.

        - server_metrics: 스칼라 1행
        - server_disk_io: 디바이스별 N행 (data.disk_io 비어있으면 skip)
        - server_net_io: 인터페이스별 N행 (data.net_io 비어있으면 skip)
        - server_mount_usage: 마운트별 N행 (data.mounts 비어있으면 skip)

        모두 ON CONFLICT DO NOTHING — 멱등성 자연키 흡수.
        """
