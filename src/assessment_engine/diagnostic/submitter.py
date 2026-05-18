"""진단 job 발행 (submit) — scheduler·web 공통 사용 (#F4 멀티노드 의존 경계).

책임 경계:
- input_params 합성·input_hash 계산·DB enqueue (active partial UNIQUE 충돌 흡수)·RabbitMQ publish
- scope='server'면 server_public_ids 길이만큼 N건, 'environment'면 1건 발행
- query/diagnostic repository 추상 인터페이스만 의존 (#F4)

본 모듈은 `assessment_engine.diagnostic` package 안 — scheduler 노드가 `web.services` 의존 없이
import 가능. 조회·기록 (DiagnosticService.get_*·record_report_emission·to_panel_payload 등) 은
여전히 web/services/diagnostic_service.py 단일 진실.

ADR 0004 단계 3 (active partial UNIQUE 충돌 흡수)·단계 4 (publish 후 워커 소비).
"""
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime

import aio_pika
from aio_pika.abc import AbstractChannel
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.db.repositories.base_diagnostic_repository import (
    BaseDiagnosticRepository,
    DiagnosticTimeRange,
)
from assessment_engine.db.repositories.base_query_repository import BaseQueryRepository
from assessment_engine.db.repositories.inbound import DiagnosticJobCreate
from assessment_engine.diagnostic.settings import diagnostic_settings


class DiagnosticNotFound(Exception):
    """router가 HTTPException(404)로 변환."""


class DiagnosticBadRequest(Exception):
    """router가 HTTPException(400)로 변환."""


class DiagnosticRaceMiss(Exception):
    """router가 HTTPException(409)로 변환 — enqueue 충돌인데 active 회수 실패."""


class DiagnosticSubmitter:
    """진단 발행 (submit) 단일 책임 — scheduler·web 공용.

    의존성 (composition root 주입):
    - query_repo: 미존재 public_id 검출 (`resolve_server_ids`)
    - session_factory: 트랜잭션 경계 1 INSERT = 1 commit
    - diagnostic_repo_factory: enqueue·get_active_by_hash
    - broker_channel: RabbitMQ publish (persistent delivery)
    """

    def __init__(
        self,
        query_repo: BaseQueryRepository,
        session_factory: async_sessionmaker[AsyncSession],
        diagnostic_repo_factory: Callable[[AsyncSession], BaseDiagnosticRepository],
        broker_channel: AbstractChannel,
    ):
        self.query_repo = query_repo
        self.session_factory = session_factory
        self.diagnostic_repo_factory = diagnostic_repo_factory
        self.broker_channel = broker_channel

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
        1) 신규 INSERT 시도 → 성공 시 RabbitMQ publish 후 id 반환
        2) active UNIQUE 충돌 → 기존 진행 중 id 회수

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
            targets: list[str | None] = list(server_public_ids)
        else:
            targets = [None]  # environment scope — 단일 job

        anchor = _normalize_anchor(anchor_at)
        job_ids: list[str] = []
        for target in targets:
            input_params = _build_input_params(scope, target, time_range, anchor)
            input_hash = _compute_hash(scope, input_params)

            async with self.session_factory() as session:
                repo = self.diagnostic_repo_factory(session)

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
                active_id = await repo.get_active_by_hash(scope, input_hash, "ai_diagnostic")
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

    async def _publish(self, job_id: str) -> None:
        """RabbitMQ publish — 진단 워커가 소비. persistent delivery (broker 재시작 생존)."""
        message = aio_pika.Message(
            body=json.dumps({"job_id": job_id}).encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
        )
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
    base: dict = {
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
