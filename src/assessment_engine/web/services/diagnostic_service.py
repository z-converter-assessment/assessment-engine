"""진단/보고서 발행 service — web 측 발행·조회·이력 단일 진실.

책임 경계:
- 보고서 발행(emit_report) — 발행 시점 정적 스냅샷 저장 + engineer 면 worker narrative publish 위임
  (publish 는 `DiagnosticSubmitter`, ADR 0014 분리 본질 유지, ADR 0023 scheduler 폐기)
- 보고서 단건/배치 조회(narrative polling)·발행 이력은 본 service 단일 진실 (router 가 호출)
- 추상 `BaseDiagnosticRepository` + `BaseQueryRepository`만 의존 (F4)

호환 re-export — input_hash/anchor helper 는 `diagnostic.submitter` 가 단일 진실:
  _compute_hash / _normalize_anchor (라우터 발행 시 anchor 정규화).

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
)
from assessment_engine.db.repositories.query.base_query_repository import BaseQueryRepository

# 발행 publish·해시 helper 단일 진실은 diagnostic.submitter — 본 모듈은 호환 re-export.
from assessment_engine.diagnostic.submitter import (  # noqa: F401 (re-export)
    DiagnosticSubmitter,
    _compute_hash,
    _normalize_anchor,
)
from assessment_engine.web.services.report_serializer import build_report_result
from assessment_engine.web.settings import diagnostic_settings


class DiagnosticService:
    """web 라우터용 보고서 발행 facade — emit_report 발행 + narrative 조회/이력 직접 처리."""

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
        self._submitter = DiagnosticSubmitter(broker_channel=broker_channel)

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

    async def emit_report(
        self,
        *,
        view: str,
        scope: str,
        kind: str,
        snapshot: dict,
        server_public_ids: list[str],
        time_range: str,
        anchor_at: datetime,
        aux: dict | None = None,
        child_jobs: dict | None = None,
        publish: bool = True,
        requested_by: str | None = None,
    ) -> str | None:
        """보고서 발행 — 발행 시점 정적 스냅샷 저장 + engineer 면 worker narrative 발행.

        요구 정합: AI narrative 트리거는 engineer 보고서 발행 시점에만 1회. customer 는 narrative 없이 즉시 succeeded.
        engineer 는 status=pending + worker 가 narratives 채운 뒤 mark_succeeded (개별 실패 시 entry.status=failed).
        GET(세부·이력)은 본 job_id 의 정적 스냅샷만 렌더 — 따로 진단 트리거 없음 (요구: 정적 보관).
        같은 input 활성 충돌(더블클릭) 시 기존 진행 중 job_id 회수.

        anchor_at: 발행 시점 기준 시각 (필수) — 스냅샷 ViewModel 과 worker narrative 가 같은 윈도우 재현.
                   라우터가 _normalize_anchor 로 분 단위 truncate 후 snapshot 합성에도 동일 값 사용.
        kind: report_result.REPORT_KIND_ENV — 모든 보고서(selection N대·환경·단일서버) 공통 양식.
        snapshot: 발행 시점 완성 ViewModel 직렬화 dict (report_serializer.*_to_dict).
        aux: ViewModel 밖 부가 정적 데이터 (운영신호 attention 등) — GET 정적 렌더가 그대로 읽음.
        child_jobs: {public_id: child_job_id} — N대 표(engineer)가 발행한 개별 단일 job 맵.
                    worker 가 표의 server 별 narrative 를 본 child job 들에 복사(단일 생성·공유, 이력 일관).
        publish: False 면 worker publish 생략 (engineer child — narrative 는 부모 표 worker 가 복사).
        """
        job_type = f"{view}_report"
        input_params: dict = {
            "view": view,
            "server_public_ids": sorted(server_public_ids),
            "time_range": time_range,
            "anchor_at": anchor_at.isoformat(),
        }
        # server scope 1대는 단수 키도 — list_recent SQL 단수 매칭(이력 server 상세 link) 정합 + worker extract_server.
        if scope == "server" and len(server_public_ids) == 1:
            input_params["server_public_id"] = server_public_ids[0]
        input_hash = _compute_hash(scope, input_params)
        narrative_status = "none" if view == "customer" else "pending"
        result = build_report_result(
            kind=kind,
            snapshot=snapshot,
            view=view,
            narrative_status=narrative_status,
            aux=aux,
        )
        if child_jobs:
            # input_hash 에는 미포함(더블클릭 dedup 보존) — result 에만 보관, worker 가 narrative 복사 대상.
            result["child_jobs"] = child_jobs
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
                active_id = await repo.get_active_by_hash(scope, input_hash, job_type)
                await session.rollback()
                logger.info("report emit active conflict view={} scope={} hash={}", view, scope, input_hash[:12])
                return active_id
            if view == "customer":
                await repo.mark_succeeded(new_id, result)
            else:
                await repo.save_report_snapshot(new_id, result)
            await session.commit()
        if view == "engineer" and publish:
            await self._submitter.publish_job(new_id)
        logger.info(
            "report emitted view={} scope={} job_id={} status={} publish={}",
            view,
            scope,
            new_id,
            "pending" if (view == "engineer" and publish) else ("child-pending" if view == "engineer" else "succeeded"),
            publish,
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

    # publish 는 DiagnosticSubmitter 가 담당 — 본 service 에 별도 메서드 없음.


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
