from abc import ABC, abstractmethod
from typing import Literal

from assessment_engine.db.dtos.inbound import DiagnosticJobCreate
from assessment_engine.db.dtos.outbound import DiagnosticJobRecord

# ADR 0004 — 차트 TimeRange와 동일 7개. 짧은 윈도우(15m/1h/6h)는 USE Method 표본 부족으로
# 의미 약하지만 차트와 토글 통합 UX 일관성을 위해 노출. 기본 "14d" — ADR 0003 WINDOW_DAYS와 동일.
DiagnosticTimeRange = Literal["15m", "1h", "6h", "24h", "7d", "14d", "30d"]

# fraction day — SQL interval 표현은 fraction 지원 (e.g., interval '0.25 days' = 6h).
# aggregator·report_aggregate의 period_days 인자는 float·int 둘 다 호환.
DIAGNOSTIC_RANGE_DAYS: dict[str, float] = {
    "15m": 15 / 1440,  # 0.0104 day
    "1h": 1 / 24,  # 0.0417 day
    "6h": 6 / 24,  # 0.25 day
    "24h": 1.0,
    "7d": 7.0,
    "14d": 14.0,
    "30d": 30.0,
}

# 한국어 표시 라벨 — mock LLM narrative + frontend 표시 통합. P5 단일 진실 (서버/클라 동일).
DIAGNOSTIC_RANGE_LABEL_KR: dict[str, str] = {
    "15m": "15분",
    "1h": "1시간",
    "6h": "6시간",
    "24h": "24시간",
    "7d": "7일",
    "14d": "14일",
    "30d": "30일",
}

# USE Method 분류 라벨 — mapper(view) + mock LLM(narrative) 양쪽 import. 분류 추가 시 본 dict만 갱신.
CLASSIFICATION_LABEL_KR: dict[str, str] = {
    "idle": "idle",
    "shutdown": "shutdown 검토",
    "over_provisioned": "over-provisioned",
    "under_provisioned": "under-provisioned",
    "optimal": "optimal",
    "insufficient_data": "데이터 부족",
}

# 진단 발행 기본 윈도우 — service default·UI 기본값 단일 진실 (F10). WINDOW_DAYS(7d)와 정합.
DIAGNOSTIC_DEFAULT_TIME_RANGE = "7d"


class BaseDiagnosticRepository(ABC):
    """진단 job 영속성 인터페이스 (ADR 0004).

    Web router·진단 워커·스케줄러 모두 본 추상에만 의존. 구체 구현체는 composition root
    (`web/deps.py` / 워커 main / 스케줄러 main)에서만 import (F4).
    """

    @abstractmethod
    async def enqueue(self, job: DiagnosticJobCreate) -> str | None:
        """진단 job INSERT. 새 id (UUID 문자열) 반환.

        active UNIQUE = (scope, input_hash, job_type). 충돌 시 None — caller는
        `get_active_by_hash(scope, input_hash, job_type)` 로 기존 job_id 조회.
        """
        ...

    @abstractmethod
    async def get_active_by_hash(
        self,
        scope: str,
        input_hash: str,
        job_type: str,
    ) -> str | None:
        """동일 (scope, input_hash, job_type) + status IN ('pending','running') job_id 조회.

        active UNIQUE 충돌 시 caller가 기존 진행 중 job_id 회수.
        """
        ...

    @abstractmethod
    async def get_latest_succeeded(
        self,
        scope: str,
        input_hash: str,
    ) -> "DiagnosticJobRecord | None":
        """가장 최근 succeeded job 1건 (input_hash 정확 일치, 시간 무관)."""
        ...

    @abstractmethod
    async def get_latest_by_context(
        self,
        scope: str,
        time_range: str,
        server_public_id: str | None,
    ) -> "DiagnosticJobRecord | None":
        """SSR latest 진단 표시용 — (scope, time_range, server_public_id) 기반 가장 최근 succeeded.

        input_hash 무관 — anchor가 달라도 같은 context 진단으로 묶어 latest 반환.
        scope='environment'는 server_public_id None.
        """
        ...

    @abstractmethod
    async def get_many_latest_by_context_server(
        self,
        time_range: str,
        server_public_ids: list[str],
    ) -> dict[str, "DiagnosticJobRecord"]:
        """N개 server에 대해 latest succeeded 단일 SQL batch (DISTINCT ON).

        server_public_id → DiagnosticJobRecord. 미존재 server는 dict에서 누락.
        보고서(report.html)의 진단 컬럼 N+1 회피 (#C5).
        """
        ...

    @abstractmethod
    async def get_by_id(self, job_id: str) -> DiagnosticJobRecord | None: ...

    @abstractmethod
    async def get_many_by_ids(self, job_ids: list[str]) -> list[DiagnosticJobRecord]:
        """배치 polling — N개 ids를 단일 SQL로. 순서는 DB 임의 (caller 정렬 책임)."""
        ...

    @abstractmethod
    async def mark_running(self, job_id: str, stage: str) -> None:
        """status pending → running, started_at=now(), progress_stage=stage."""
        ...

    @abstractmethod
    async def update_progress_stage(self, job_id: str, stage: str) -> None: ...

    @abstractmethod
    async def mark_succeeded(self, job_id: str, result: dict) -> None:
        """status → succeeded, result 저장, finished_at=now(), progress_stage=NULL."""
        ...

    @abstractmethod
    async def save_report_snapshot(self, job_id: str, result: dict) -> None:
        """발행 시점 보고서 snapshot 을 result 에 저장 (status pending 유지).

        engineer 보고서 발행 — web 이 ViewModel snapshot 을 result 에 저장(pending 유지), worker 가
        pending job 을 받아 mark_running -> narrative 생성 -> mark_succeeded(snapshot + narrative).
        customer 는 본 메서드 없이 발행 즉시 mark_succeeded.
        """
        ...

    @abstractmethod
    async def mark_failed(self, job_id: str, error_message: str) -> None:
        """status → failed, error_message 저장, finished_at=now()."""
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

        보고서 이력 페이지(`/reports/history`)용. 모든 상태(pending/running/succeeded/failed) 포함.
        server_public_ids 지정 시 input_params JSONB에서 ANY 매칭 (server scope job만 자연 필터).
        job_type 미지정은 전체 (customer_report + engineer_report).
        """
        ...

    @abstractmethod
    async def delete_retention(self, older_than_days: int) -> int:
        """finished_at < now() - days 인 행 DELETE. 삭제 카운트 반환.

        스케줄러 발화 시 호출 — 별도 cron 인프라 없이 진단 사이클에 묶어 retention 적용
        (ADR 0004 90일 정책).
        """
        ...
