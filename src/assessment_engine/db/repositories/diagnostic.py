"""보고서 job 상태머신 데이터 접근 Protocol.

web(발행)과 worker(claim·완료)가 같은 인터페이스를 쓴다 — 상태 전이가 한 계약 안에 모여 있다.
"""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from assessment_engine.db.dtos.inbound import DiagnosticJobCreate
    from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
    from assessment_engine.json_types import JsonObject

# 진단 평가 윈도우 타입·상수는 `db/repositories/query/types.py` 단일 진실 — repo 인터페이스 계층은 안 갖는다.


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
    ) -> str | None:
        """같은 입력의 job_id 조회. status IN ('pending','running') 만."""
        ...

    async def get_by_id(self, job_id: str) -> DiagnosticJobRecord | None: ...
    async def mark_succeeded(self, job_id: str, result: JsonObject) -> None:
        """status -> succeeded, result 저장, finished_at=now(), progress_stage=NULL."""
        ...

    async def claim_next_pending(self) -> DiagnosticJobRecord | None:
        """pending job 1건 원자적 claim (created_at FIFO). 큐 비면 None.

        SELECT ... FOR UPDATE SKIP LOCKED 라 row-lock 이 1 job = 1 워커를 보장한다 — 별도 큐 없이
        DB 가 분산 조정. 커밋은 호출자(워커) 몫이다. claim 트랜잭션은 running 마킹까지만 짧게 닫고
        보고서 생성은 별도 세션에서 돈다. claim 된 row 는 status=running·started_at=now()·
        progress_stage='running' 이 된다.
        """
        ...

    async def mark_failed(self, job_id: str, error_message: str) -> None:
        """status -> failed, finished_at=now(), progress_stage=NULL. error_message 는 sanitize 후 넘긴다."""
        ...

    async def recover_stale_running(self, stale_seconds: int) -> int:
        """started_at 이 stale_seconds 를 넘긴 running job 을 pending 으로 되돌린다. 복구 건수 반환.

        워커 기동 시 1회 호출 — 처리 노드가 죽어 running 에 멈춘 job 을 다른 노드가 다시 집도록.
        started_at=NULL·progress_stage='requeued' 로 되돌려 재claim 이 가능한 상태로 만든다.
        """
        ...

    async def list_recent(
        self,
        days: int,
        scope: str | None = None,
        server_public_ids: list[str] | None = None,
        job_type: str | None = None,
        limit: int = 200,
    ) -> list[DiagnosticJobRecord]:
        """최근 N일 보고서 발행 이력 (created_at DESC). 모든 상태를 포함한다.

        server_public_ids 는 input_params JSONB ANY 매칭이라 server scope job 만 자연히 남는다.
        """
        ...

    async def delete_retention(self, older_than_days: int) -> int:
        """finished_at 이 지난 행 DELETE. 삭제 카운트 반환."""
        ...
