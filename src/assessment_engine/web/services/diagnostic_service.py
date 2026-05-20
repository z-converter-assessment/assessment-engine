"""진단 service — web 측 조회·기록 (publish 는 `diagnostic/submitter.py` 단일 진실).

책임 경계:
- 발행 (submit) 은 `DiagnosticSubmitter` 위임 — scheduler 노드도 동일 함수 사용 (#F4)
- 조회·이력·보고서 발행 기록은 본 service 단일 진실 (router 가 호출)
- 추상 `BaseDiagnosticRepository` + `BaseQueryRepository`만 의존 (F4)

호환 re-export — exceptions 와 helpers 는 `diagnostic.submitter` 가 단일 진실:
  DiagnosticNotFound / DiagnosticBadRequest / DiagnosticRaceMiss / _build_input_params /
  _compute_hash / _normalize_anchor.

상세 설계는 ADR 0004.
"""

import json
from collections.abc import Callable
from datetime import datetime

from aio_pika.abc import AbstractChannel
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.cache.redis import safe_get
from assessment_engine.db.dtos.inbound import DiagnosticJobCreate
from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
from assessment_engine.db.repositories.base_diagnostic_repository import (
    BaseDiagnosticRepository,
    DiagnosticTimeRange,
)
from assessment_engine.db.repositories.query.base_query_repository import BaseQueryRepository

# 발행 단일 진실은 diagnostic.submitter — 본 모듈은 호환 re-export.
from assessment_engine.diagnostic.submitter import (  # noqa: F401 (re-export)
    DiagnosticBadRequest,
    DiagnosticNotFound,
    DiagnosticRaceMiss,
    DiagnosticSubmitter,
    _build_input_params,
    _compute_hash,
    _normalize_anchor,
)
from assessment_engine.web.settings import diagnostic_settings


class DiagnosticService:
    """web 라우터용 진단 facade — submit 은 DiagnosticSubmitter 위임 + 조회/기록 직접 처리."""

    def __init__(
        self,
        query_repo: BaseQueryRepository,
        session_factory: async_sessionmaker[AsyncSession],
        diagnostic_repo_factory: Callable[[AsyncSession], BaseDiagnosticRepository],
        broker_channel: AbstractChannel,
        redis: Redis,
    ):
        self.query_repo = query_repo
        self.session_factory = session_factory
        self.diagnostic_repo_factory = diagnostic_repo_factory
        self.broker_channel = broker_channel
        self.redis = redis
        self._submitter = DiagnosticSubmitter(
            query_repo=query_repo,
            session_factory=session_factory,
            diagnostic_repo_factory=diagnostic_repo_factory,
            broker_channel=broker_channel,
        )

    async def submit(
        self,
        scope: str,
        server_public_ids: list[str] | None,
        time_range: DiagnosticTimeRange = "14d",
        anchor_at: datetime | None = None,
        requested_by: str | None = None,
    ) -> list[str]:
        """진단 발행 위임 — 단일 진실 `DiagnosticSubmitter.submit`."""
        return await self._submitter.submit(
            scope,
            server_public_ids,
            time_range,
            anchor_at,
            requested_by,
        )

    async def get_many_latest_server(
        self,
        server_public_ids: list[str],
        time_range: DiagnosticTimeRange = "14d",
    ) -> dict[str, dict | None]:
        """보고서(report.html) 진단 컬럼용 — N개 server latest 14일 진단 batch.

        반환: server_public_id → to_panel_payload(record) dict (또는 미존재면 키 누락).
        """
        if not server_public_ids:
            return {}
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            records = await repo.get_many_latest_by_context_server(time_range, server_public_ids)
        return {pid: to_panel_payload(rec) for pid, rec in records.items()}

    async def get_latest(
        self,
        scope: str,
        server_public_id: str | None,
        time_range: DiagnosticTimeRange = "14d",
    ) -> DiagnosticJobRecord | None:
        """SSR 페이지 로드 시 마지막 진단 결과 자동 표시용 (ADR 0004).

        anchor_at 무관 — (scope, time_range, server_public_id) 기반 가장 최근 succeeded.
        스케줄러 매일 03시 발화·운영자 자유 anchor 발행 결과 둘 다 표시.
        """
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            return await repo.get_latest_by_context(scope, time_range, server_public_id)

    async def list_recent(
        self,
        days: int,
        scope: str | None = None,
        server_public_ids: list[str] | None = None,
        job_type: str | None = None,
    ) -> list[DiagnosticJobRecord]:
        """AI 진단 이력 페이지(/diagnostics/history) 용 — 최근 N일 발행 이력.

        - server_public_ids: 해당 서버 관련 job 만 (server scope 자연 필터)
        - job_type: 라우터 단에서 'ai_diagnostic' 고정 (보고서 이력은 list_reports 별도).
        """
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            return await repo.list_recent(days, scope, server_public_ids, job_type)

    async def list_reports(
        self,
        days: int,
        view: str = "all",
        server_public_ids: list[str] | None = None,
        cursor: datetime | None = None,
        limit: int = 20,
        scope: str | None = None,
    ) -> list[DiagnosticJobRecord]:
        """보고서 발행 이력 페이지(/reports/history) 용 — customer + engineer 통합.

        view='all' 이면 두 job_type union (SQL 2회 + Python merge — 발행 빈도 낮아 비효율 수용).
        scope=None 전체 / 'environment' / 'server' — record 의 scope 필드 기준 filter.
        cursor: created_at < cursor 행만 반환 (더보기 페이징). None 이면 최근부터.
        """
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            if view == "all":
                customer = await repo.list_recent(days, scope, server_public_ids, "customer_report")
                engineer = await repo.list_recent(days, scope, server_public_ids, "engineer_report")
                merged = customer + engineer
            else:
                job_type = f"{view}_report"
                merged = await repo.list_recent(days, scope, server_public_ids, job_type)
            if cursor is not None:
                merged = [r for r in merged if r.created_at < cursor]
            merged.sort(key=lambda r: r.created_at, reverse=True)
            return merged[:limit]

    async def record_report_emission(
        self,
        *,
        view: str,
        scope: str,
        server_public_ids: list[str],
        period_days: float,
        time_range: str | None = None,
        summary_meta: dict | None = None,
        requested_by: str | None = None,
    ) -> str | None:
        """보고서 발행 이력 row INSERT — 합성 직후 즉시 succeeded 로 마킹.

        view: 'customer' | 'engineer' → job_type 'customer_report' | 'engineer_report'.
        time_range: 보고서 모달 윈도우 식별자(15m/1h/6h/24h/7d/14d/30d). 이력 표시·재조회 link 복원용.
                    period_days 는 1일 미만 윈도우에서 0 이 되어 손실되므로 time_range 가 단일 진실.
        같은 input 활성 충돌 (동시 두 요청) 시 None — best-effort, 호출자 응답 흐름 영향 없음.
        result JSONB 에는 메타만 (서버 수·기간·view) — 양식 HTML 전체 저장은 비대화 회피.
        """
        job_type = f"{view}_report"
        input_params: dict = {
            "view": view,
            "server_public_ids": sorted(server_public_ids),
            "period_days": period_days,
        }
        if time_range:
            input_params["time_range"] = time_range
        # server scope 1대 보고서는 server_public_id 단수 키도 추가 — list_recent SQL 의 단수 매칭 hit.
        # AI 진단 server scope job 과 동일 형식이라 보고서 이력 filter (server 상세 link 진입) 정합.
        if scope == "server" and len(server_public_ids) == 1:
            input_params["server_public_id"] = server_public_ids[0]
        input_hash = _compute_hash(scope, input_params)
        result_payload: dict = {
            "view": view,
            "period_days": period_days,
            "server_count": len(server_public_ids),
            **(summary_meta or {}),
        }
        if time_range and "time_range" not in result_payload:
            result_payload["time_range"] = time_range
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            new_id = await repo.enqueue(
                DiagnosticJobCreate(
                    scope=scope,
                    job_type=job_type,
                    input_params=input_params,
                    input_hash=input_hash,
                    requested_by=requested_by,
                )
            )
            if new_id is None:
                await session.rollback()
                logger.debug(
                    "report record active conflict view={} scope={} hash={}",
                    view,
                    scope,
                    input_hash[:12],
                )
                return None
            await repo.mark_succeeded(new_id, result_payload)
            await session.commit()
            logger.info(
                "report recorded view={} scope={} hash={} job_id={}",
                view,
                scope,
                input_hash[:12],
                new_id,
            )
            return new_id

    async def get_one(self, job_id: str) -> DiagnosticJobRecord | None:
        """단건 조회 — Redis polling 캐시 우선, miss 시 DB fallback (#C3 fail-open)."""
        cached = await safe_get(
            self.redis,
            diagnostic_settings.redis_key_diagnostic_progress.format(job_id),
        )
        if cached:
            return _deserialize_record(cached)
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            return await repo.get_by_id(job_id)

    async def get_many(self, job_ids: list[str]) -> list[DiagnosticJobRecord]:
        """배치 조회 — Redis는 단건 캐시 의도라 다건은 바로 DB. N개 단일 SQL."""
        if not job_ids:
            return []
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            return await repo.get_many_by_ids(job_ids)

    # _publish 는 DiagnosticSubmitter 가 담당 — 본 service 에 별도 메서드 없음.


def to_panel_payload(rec: DiagnosticJobRecord | None) -> dict | None:
    """SSR inline JSON·API 응답 dict — `diagnostic_mapper.to_view`의 직렬화 wrapper.

    P2 단일 변환 — badge·label·window·classification 파생은 mapper에서. 본 함수는 dict 변환만.
    """
    from assessment_engine.web.services.mappers.diagnostic import to_view

    view = to_view(rec)
    return view.to_dict() if view else None


def _deserialize_record(blob: str) -> DiagnosticJobRecord:
    """워커가 Redis SET한 progress 캐시 JSON → DiagnosticJobRecord.

    워커 측 직렬화는 dataclasses.asdict + datetime ISO 변환 (cache_serializer 패턴 참조).
    여기는 역방향 — datetime 필드만 fromisoformat 복원.
    """
    data = json.loads(blob)
    for key in ("created_at", "started_at", "finished_at"):
        v = data.get(key)
        if isinstance(v, str):
            data[key] = datetime.fromisoformat(v)
    return DiagnosticJobRecord(**data)
