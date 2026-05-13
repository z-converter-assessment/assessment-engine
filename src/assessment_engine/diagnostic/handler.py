"""진단 메시지 핸들러 — RabbitMQ 메시지 1건 = 진단 job 1건 처리 (ADR 0004).

단계 흐름: extracting_stats → applying_rules → generating_narrative → succeeded.
각 단계 UPDATE 후 commit + Redis polling 캐시 SET. 폴링은 Redis 우선, miss 시 DB.
"""
import dataclasses
import json
from collections.abc import Callable
from datetime import datetime

import aio_pika
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.config import diagnostic_settings
from assessment_engine.db.redis import safe_set, safe_set_nx
from assessment_engine.db.repositories.base_diagnostic_repository import (
    DIAGNOSTIC_RANGE_DAYS,
    BaseDiagnosticRepository,
)
from assessment_engine.db.repositories.base_query_repository import BaseQueryRepository
from assessment_engine.diagnostic import aggregator
from assessment_engine.diagnostic.llm.base import BaseLlmClient


def make_diagnostic_handler(
    session_factory: async_sessionmaker[AsyncSession],
    query_repo_factory: Callable[[AsyncSession], BaseQueryRepository],
    diagnostic_repo_factory: Callable[[AsyncSession], BaseDiagnosticRepository],
    llm_client: BaseLlmClient,
    redis: Redis,
):
    """consumer handler 팩토리 패턴(F4) — composition root에서 의존성 주입."""

    async def handler(message: aio_pika.abc.AbstractIncomingMessage):
        async with message.process(requeue=False):
            try:
                body = json.loads(message.body)
                job_id = body["job_id"]
            except (json.JSONDecodeError, KeyError) as e:
                # 메시지 자체 결함 — silent ack + 로그 (DLQ 보낼 가치 X, 운영자 개입 의미 없음).
                # #F8 — body raw dump 금지. message_id·길이·예외 타입만 로깅.
                logger.error("invalid diagnostic message message_id={} body_size={} err_type={}",
                             message.message_id, len(message.body or b""), type(e).__name__)
                return

            # 멱등성 1단 (fail-open) — 동일 message_id 중복 전송 차단. Redis 장애 시 통과.
            message_id = message.message_id or job_id
            ok = await safe_set_nx(
                redis,
                diagnostic_settings.redis_key_idempotent.format(message_id),
                "1",
                diagnostic_settings.redis_ttl_idempotent,
            )
            if ok is False:
                logger.info("duplicate diagnostic message dropped job_id={} message_id={}",
                            job_id, message_id)
                return

            async with session_factory() as session:
                query_repo = query_repo_factory(session)
                diag_repo = diagnostic_repo_factory(session)

                job = await diag_repo.get_by_id(job_id)
                if job is None:
                    logger.warning("diagnostic job not found id={}", job_id)
                    return
                if job.status != "pending":
                    # 이미 처리 중·완료 — 멱등성 1단이 fail-open이라 가능
                    logger.info("diagnostic skip non-pending job_id={} status={}", job_id, job.status)
                    return

                try:
                    # 단계 1 — 통계 추출
                    await diag_repo.mark_running(job_id, "extracting_stats")
                    await session.commit()
                    await _publish_progress(redis, diag_repo, job_id)

                    time_range = job.input_params["time_range"]
                    period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
                    end = datetime.fromisoformat(job.input_params["anchor_at"])

                    if job.scope == "server":
                        payload = await aggregator.extract_server(
                            query_repo,
                            job.input_params["server_public_id"],
                            period_days,
                            end,
                            time_range,
                        )
                    else:
                        payload = await aggregator.extract_environment(
                            query_repo, period_days, end, time_range,
                        )

                    # 단계 2 — 룰 분류 (aggregator 안에 classify·top_actions 이미 포함, stage 신호만)
                    await diag_repo.update_progress_stage(job_id, "applying_rules")
                    await session.commit()
                    await _publish_progress(redis, diag_repo, job_id)

                    # 단계 3 — LLM narrative
                    await diag_repo.update_progress_stage(job_id, "generating_narrative")
                    await session.commit()
                    await _publish_progress(redis, diag_repo, job_id)

                    narrative = await llm_client.generate_narrative(job.scope, payload)
                    payload["narrative"] = narrative

                    await diag_repo.mark_succeeded(job_id, payload)
                    await session.commit()
                    await _publish_progress(redis, diag_repo, job_id)

                    logger.info("diagnostic succeeded job_id={} scope={}", job_id, job.scope)

                except OperationalError:
                    # DB 일시 장애 — DLQ 재시도 기회 (F6 fail-close). job은 pending 상태 유지.
                    logger.exception("diagnostic db unavailable job_id={}", job_id)
                    raise
                except (ValueError, KeyError, IntegrityError) as e:
                    # 비즈니스/영구 실패 (aggregator no metrics·input_params 누락·UNIQUE 위반)
                    # status='failed'로 흡수 — 운영자 polling으로 인지·재발행
                    logger.exception("diagnostic failed job_id={}", job_id)
                    await diag_repo.mark_failed(job_id, str(e)[:500])
                    await session.commit()
                    await _publish_progress(redis, diag_repo, job_id)

    return handler


async def _publish_progress(redis: Redis, diag_repo: BaseDiagnosticRepository, job_id: str) -> None:
    """단계 UPDATE 후 Redis polling 캐시 SET — web GET이 본 캐시 우선 read (#C3 fail-open)."""
    record = await diag_repo.get_by_id(job_id)
    if record is None:
        return
    await safe_set(
        redis,
        diagnostic_settings.redis_key_diagnostic_progress.format(job_id),
        _serialize(record),
        ex=diagnostic_settings.redis_ttl_diagnostic_progress,
    )


def _serialize(record) -> str:
    """DiagnosticJobRecord → JSON 문자열. datetime은 ISO 8601 UTC (web service가 fromisoformat 복원)."""
    data = dataclasses.asdict(record)
    for key in ("created_at", "started_at", "finished_at"):
        v = data.get(key)
        if v is not None and hasattr(v, "isoformat"):
            data[key] = v.isoformat()
    return json.dumps(data)
