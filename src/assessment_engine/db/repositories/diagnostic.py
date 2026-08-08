"""보고서 job 상태머신 데이터 접근 Protocol.

web(발행)과 worker(claim·완료)가 같은 인터페이스를 쓴다 — 상태 전이가 한 계약 안에 모여 있다.
"""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from assessment_engine.db.dtos.inbound import DiagnosticJobCreate
    from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
    from assessment_engine.json_types import JsonObject


class DiagnosticRepository(Protocol):
    """진단 job 영속성 인터페이스."""

    async def enqueue(self, job: DiagnosticJobCreate) -> str | None:
        """진단 job INSERT. 새 id(UUID) 반환.

        active UNIQUE(scope, input_hash, job_type) 충돌 시 None — 호출자가 `get_active_by_hash` 로
        기존 job 에 합류한다.
        """
        ...

    async def get_active_by_hash(
        self,
        scope: str,
        input_hash: str,
        job_type: str,
    ) -> str | None: ...

    async def get_by_id(self, job_id: str) -> DiagnosticJobRecord | None: ...
    async def mark_succeeded(self, job_id: str, result: JsonObject) -> None: ...

    async def claim_next_pending(self) -> DiagnosticJobRecord | None:
        """pending job 1건 원자적 claim (created_at FIFO). 큐 비면 None.

        SELECT ... FOR UPDATE SKIP LOCKED 라 row-lock 이 1 job = 1 워커를 보장한다 — 별도 큐 없이
        DB 가 분산 조정. 커밋은 호출자(워커) 몫이다. claim 트랜잭션은 running 마킹까지만 짧게 닫고
        보고서 생성은 별도 세션에서 돈다. claim 된 row 는 status=running·started_at=now()·
        progress_stage='running' 이 된다.
        """
        ...

    async def mark_failed(self, job_id: str, error_message: str) -> None: ...

    async def recover_stale_running(self, stale_seconds: int) -> int: ...

    async def list_recent(
        self,
        days: int,
        scope: str | None = None,
        server_public_ids: list[str] | None = None,
        job_type: str | None = None,
        limit: int = 200,
    ) -> list[DiagnosticJobRecord]: ...

    async def delete_retention(self, older_than_days: int) -> int: ...
