"""진단 service — 운영자가 웹에서 진단 job을 발행하고 polling으로 결과 조회.

책임 경계:
- input_hash 계산·1시간 캐시 조회·신규 enqueue·RabbitMQ publish 캡슐화 — router는 service만 호출
- 추상 `BaseDiagnosticRepository` + `BaseQueryRepository`만 의존 (F4) — composition root에서 주입
- 트랜잭션 경계는 service가 관리

상세 설계는 ADR 0004.
"""
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime

import aio_pika
from aio_pika.abc import AbstractChannel
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.db.redis import safe_get
from assessment_engine.db.repositories.base_diagnostic_repository import (
    BaseDiagnosticRepository,
    DiagnosticTimeRange,
)
from assessment_engine.db.repositories.base_query_repository import BaseQueryRepository
from assessment_engine.db.repositories.inbound import DiagnosticJobCreate
from assessment_engine.db.repositories.outbound import DiagnosticJobRecord
from assessment_engine.web.settings import diagnostic_settings


class DiagnosticService:
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

    async def submit(
        self,
        scope: str,
        server_public_ids: list[str] | None,
        time_range: DiagnosticTimeRange = "14d",
        anchor_at: datetime | None = None,
        requested_by: str | None = None,
    ) -> list[str]:
        """진단 job N개 발행 — server scope면 server_public_ids 길이만큼, environment면 1건.

        각 input별로:
        1) 1h 캐시 조회 → 있으면 그 id 반환
        2) 신규 INSERT 시도 → 성공 시 RabbitMQ publish 후 id 반환
        3) active UNIQUE 충돌 → 기존 진행 중 id 회수

        server scope에서 미존재 public_id는 즉시 DiagnosticNotFound.
        anchor_at None이면 분 단위로 truncate한 now() 사용 (같은 분 호출은 같은 input_hash).
        """
        if scope == "server":
            if not server_public_ids:
                raise DiagnosticBadRequest("server_ids required for scope='server'")
            sid_map = await self.query_repo.resolve_server_ids(server_public_ids)
            missing = [pid for pid in server_public_ids if pid not in sid_map]
            if missing:
                raise DiagnosticNotFound(f"server not found: {','.join(missing[:5])}")
            targets = server_public_ids
        else:
            targets = [None]  # environment scope — 단일 job

        anchor = _normalize_anchor(anchor_at)
        job_ids: list[str] = []
        for target in targets:
            input_params = _build_input_params(scope, target, time_range, anchor)
            input_hash = _compute_hash(scope, input_params)

            async with self.session_factory() as session:
                repo = self.diagnostic_repo_factory(session)

                # 1시간 input_hash 캐시는 제거됨 (anchor 분 단위 변화로 실효성 없음).
                # 더블클릭은 active partial UNIQUE(pending/running)가 흡수 — 아래 enqueue 충돌 분기.
                new_id = await repo.enqueue(DiagnosticJobCreate(
                    scope=scope,
                    input_params=input_params,
                    input_hash=input_hash,
                    requested_by=requested_by,
                ))
                await session.commit()

                if new_id:
                    await self._publish(new_id)
                    job_ids.append(new_id)
                    logger.info("diagnostic enqueued scope={} hash={} job_id={}", scope, input_hash[:12], new_id)
                    continue

                # active 충돌 — 같은 input이 진행 중. 기존 job_id 회수.
                active_id = await repo.get_active_by_hash(scope, input_hash)
                if active_id:
                    job_ids.append(active_id)
                    logger.info(
                        "diagnostic active conflict scope={} hash={} job_id={}",
                        scope, input_hash[:12], active_id,
                    )
                else:
                    # INSERT 충돌인데 active 조회 없음 (race: 조회 시점에 이미 종료된 case).
                    # skip하면 job_ids 길이가 줄어 클라이언트 혼란 — 명시적 raise.
                    raise DiagnosticRaceMiss(f"enqueue conflict but no active job (race) scope={scope}")

        return job_ids

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
        self, days: int, scope: str | None = None,
        server_public_ids: list[str] | None = None,
    ) -> list[DiagnosticJobRecord]:
        """이력 페이지(/diagnostics/history)용 — 최근 N일 발행 이력. server_public_ids 지정 시 그 서버들 진단만."""
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            return await repo.list_recent(days, scope, server_public_ids)

    async def get_one(self, job_id: str) -> DiagnosticJobRecord | None:
        """단건 조회 — Redis polling 캐시 우선, miss 시 DB fallback (#C3 fail-open)."""
        cached = await safe_get(
            self.redis, diagnostic_settings.redis_key_diagnostic_progress.format(job_id),
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

    async def _publish(self, job_id: str) -> None:
        """RabbitMQ publish — 진단 워커가 소비. persistent delivery (broker 재시작 생존)."""
        message = aio_pika.Message(
            body=json.dumps({"job_id": job_id}).encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
        )
        # exchange는 ConsumerSettings.rabbitmq_exchange. routing_key는 진단 전용.
        exchange = await self.broker_channel.get_exchange(diagnostic_settings.rabbitmq_exchange)
        await exchange.publish(
            message,
            routing_key=diagnostic_settings.diagnostic_routing_key,
        )


def _build_input_params(
    scope: str,
    server_public_id: str | None,
    time_range: str,
    anchor_at: datetime,
) -> dict:
    """input_hash 안정성 — 키·값 카탈로그 고정. 새 키 추가 시 hash 변경(의도된 동작).

    anchor_at은 ISO 8601 UTC 문자열로 직렬화 (canonical JSON 호환).
    """
    base = {
        "time_range": time_range,
        "anchor_at":  anchor_at.isoformat(),
    }
    if scope == "server":
        base["server_public_id"] = server_public_id
    return base


def _compute_hash(scope: str, input_params: dict) -> str:
    canonical = json.dumps(input_params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{scope}|{canonical}".encode()).hexdigest()


def _normalize_anchor(at: datetime | None) -> datetime:
    """anchor 분 단위 truncate — 같은 분 호출은 같은 input_hash (캐시 일관성).

    None이면 now() UTC 분 단위. 명시 시 timezone-aware 후 UTC 변환 + 분 단위.
    """
    if at is None:
        at = datetime.now(UTC)
    elif at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(UTC).replace(second=0, microsecond=0)


def to_panel_payload(rec: DiagnosticJobRecord | None) -> dict | None:
    """SSR inline JSON·API 응답 dict — `diagnostic_mapper.to_view`의 직렬화 wrapper.

    P2 단일 변환 — badge·label·window·classification 파생은 mapper에서. 본 함수는 dict 변환만.
    """
    from assessment_engine.web.services.diagnostic_mapper import to_view
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


class DiagnosticNotFound(Exception):
    """router가 HTTPException(404)로 변환."""


class DiagnosticBadRequest(Exception):
    """router가 HTTPException(400)로 변환."""


class DiagnosticRaceMiss(Exception):
    """router가 HTTPException(409)로 변환 — enqueue 충돌인데 active 회수 실패."""
