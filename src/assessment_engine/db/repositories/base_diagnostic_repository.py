from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assessment_engine.db.dtos.inbound import DiagnosticJobCreate
    from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
    from assessment_engine.json_types import JsonObject

# 진단 평가 윈도우 타입·상수(TimeRange/DIAGNOSTIC_RANGE_DAYS/DIAGNOSTIC_DEFAULT_TIME_RANGE)는
# db/repositories/query/types.py 단일 진실 (#F10) — repo 인터페이스 계층에 표시/윈도우 상수 미보유.


class BaseDiagnosticRepository(ABC):
    """진단 job 영속성 인터페이스. 보고서 발행 이력·진단 job enqueue·조회·확정·retention."""

    @abstractmethod
    async def enqueue(self, job: DiagnosticJobCreate) -> str | None:
        """진단 job INSERT. 새 id (UUID) 반환.

        active UNIQUE = (scope, input_hash, job_type). 충돌 시 None — caller 가
        `get_active_by_hash` 로 기존 job_id 조회.
        """
        ...

    @abstractmethod
    async def get_active_by_hash(
        self,
        scope: str,
        input_hash: str,
        job_type: str,
    ) -> str | None:
        """동일 (scope, input_hash, job_type) + status IN ('pending','running') job_id 조회."""
        ...

    @abstractmethod
    async def get_by_id(self, job_id: str) -> DiagnosticJobRecord | None: ...

    @abstractmethod
    async def mark_succeeded(self, job_id: str, result: JsonObject) -> None:
        """status → succeeded, result 저장, finished_at=now(), progress_stage=NULL."""
        ...

    @abstractmethod
    async def claim_next_pending(self) -> DiagnosticJobRecord | None:
        """pending job 1건 원자적 claim — SELECT ... FOR UPDATE SKIP LOCKED + status=running.

        멀티워커·멀티노드 안전 (row-lock 으로 1 job = 1 워커, 큐 없이 DB 가 분산 조정). created_at
        오름차순(FIFO). claim 된 row 의 started_at=now()·progress_stage='running'. None 이면 큐 빔.
        커밋은 호출자(워커) — claim 트랜잭션은 running 마킹까지 짧게 닫고, 보고서 생성은 별도 세션.
        """
        ...

    @abstractmethod
    async def mark_failed(self, job_id: str, error_message: str) -> None:
        """status → failed, finished_at=now(), error_message 저장(F8 sanitize 후 전달), progress_stage=NULL."""
        ...

    @abstractmethod
    async def recover_stale_running(self, stale_seconds: int) -> int:
        """started_at 이 stale_seconds 초과한 running job 을 pending 으로 복구 (크래시/SIGTERM in-flight 회수).

        워커 기동 시 1회 호출 — 처리 노드 다운으로 running 에 멈춘 job 을 다른 노드가 재집도록.
        started_at=NULL·progress_stage='requeued' 로 되돌림. 복구 건수 반환.
        """
        ...

    @abstractmethod
    async def list_recent(
        self,
        days: int,
        scope: str | None = None,
        server_public_ids: list[str] | None = None,
        job_type: str | None = None,
        limit: int = 200,
    ) -> list[DiagnosticJobRecord]:
        """최근 N일 보고서 발행 이력. scope·server_public_ids·job_type 필터 옵션. created_at DESC.

        모든 상태(succeeded/failed) 포함. server_public_ids 지정 시 input_params JSONB ANY 매칭
        (server scope job 만 자연 필터). job_type 미지정은 전체.
        """
        ...

    @abstractmethod
    async def delete_retention(self, older_than_days: int) -> int:
        """finished_at < now() - days 인 행 DELETE. 삭제 카운트 반환 (보고서 이력 retention)."""
        ...
