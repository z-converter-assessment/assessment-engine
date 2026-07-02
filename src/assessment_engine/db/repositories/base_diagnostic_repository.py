from abc import ABC, abstractmethod
from typing import Literal

from assessment_engine.db.dtos.inbound import DiagnosticJobCreate
from assessment_engine.db.dtos.outbound import DiagnosticJobRecord

# ADR 0004 — 차트 TimeRange와 동일 7개. 짧은 윈도우(15m/1h/6h)는 USE Method 표본 부족으로
# 의미 약하지만 차트와 토글 통합 UX 일관성을 위해 노출. 기본 "7d" — ADR 0003 WINDOW_DAYS와 동일.
DiagnosticTimeRange = Literal["15m", "1h", "6h", "24h", "7d", "14d", "30d"]

# fraction day — SQL interval 은 fraction 지원 (interval '0.25 days' = 6h). period_days 는 float·int 호환.
DIAGNOSTIC_RANGE_DAYS: dict[str, float] = {
    "15m": 15 / 1440,
    "1h": 1 / 24,
    "6h": 6 / 24,
    "24h": 1.0,
    "7d": 7.0,
    "14d": 14.0,
    "30d": 30.0,
}

# 한국어 표시 라벨 — frontend 표시 단일 진실 (서버/클라 동일).
# USE Method 분류 라벨 — mapper(view) import. 분류 추가 시 본 dict만 갱신.
CLASSIFICATION_LABEL_KR: dict[str, str] = {
    "idle": "idle",
    "shutdown": "shutdown 검토",
    "over_provisioned": "over-provisioned",
    "under_provisioned": "under-provisioned",
    "optimal": "optimal",
    "insufficient_data": "표본 부족",
}

# 진단 발행 기본 윈도우 — service default·UI 기본값 단일 진실 (F10). WINDOW_DAYS(7d)와 정합.
DIAGNOSTIC_DEFAULT_TIME_RANGE = "7d"


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
    async def mark_succeeded(self, job_id: str, result: dict) -> None:
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
    ) -> list["DiagnosticJobRecord"]:
        """최근 N일 보고서 발행 이력. scope·server_public_ids·job_type 필터 옵션. created_at DESC.

        모든 상태(succeeded/failed) 포함. server_public_ids 지정 시 input_params JSONB ANY 매칭
        (server scope job 만 자연 필터). job_type 미지정은 전체.
        """
        ...

    @abstractmethod
    async def delete_retention(self, older_than_days: int) -> int:
        """finished_at < now() - days 인 행 DELETE. 삭제 카운트 반환 (보고서 이력 retention)."""
        ...
