"""Metrics 메시지 핸들러 — server.metrics routing key. metrics 핸들러는 미등록 서버 auto-register."""

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from aio_pika.abc import AbstractIncomingMessage
from loguru import logger
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.cache.redis import safe_delete, safe_set
from assessment_engine.consumer.handlers._common import (
    _check_idempotent,
    _correct_skewed_collected_at,
    _db_retry,
    _log_time_invariants,
    _track_agent_restart,
)
from assessment_engine.consumer.mappers import build_placeholder_inventory, to_metric_create
from assessment_engine.consumer.schemas import MetricsInput
from assessment_engine.consumer.settings import consumer_settings
from assessment_engine.db.repositories.base_collect_repository import BaseCollectRepository, MetricInsertResult


def make_metrics_handler(
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], BaseCollectRepository],
    redis: Redis,
) -> Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]:
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = MetricsInput.model_validate_json(message.body)
            except ValidationError as e:
                logger.error("metrics parse error count={}", len(e.errors()))
                raise

            if not await _check_idempotent(redis, data.message_id):
                logger.info("metrics duplicate skipped message_id={}", data.message_id)
                return

            # 수신 경계 보정 — 시계오차로 미래·과거로 틀어진 collected_at 을 수신시각으로 보정 (양방향, #F2).
            # _log_time_invariants 앞에 둬 보정 후 잔여 시계이상값이 신호로 노출되게.
            await _correct_skewed_collected_at(redis, data, datetime.now(UTC))
            await _log_time_invariants(redis, data)

            dto = to_metric_create(data)
            placeholder = build_placeholder_inventory(data)

            async def save(repo: BaseCollectRepository) -> tuple[int, bool, MetricInsertResult]:
                # ensure_server_id 가 find->upsert 캡슐화. find 성공 시 placeholder 미사용.
                server_id, auto_registered = await repo.ensure_server_id(data.composite_id, placeholder)
                result = await repo.record_metrics(server_id, dto)
                return server_id, auto_registered, result

            resolved_server_id, auto_registered, insert_result = await _db_retry(session_factory, repo_factory, save)

            if auto_registered:
                logger.info(
                    "auto-registered server from metrics composite_id={} hostname={} "
                    "(정적 정보는 다음 inventory 도착 시 채워짐)",
                    data.composite_id,
                    data.hostname,
                )

            online_key = consumer_settings.redis_key_online.format(resolved_server_id)
            cache_key = consumer_settings.redis_key_cache_metrics.format(resolved_server_id)
            await safe_set(redis, online_key, "1", ex=consumer_settings.redis_ttl_online)
            await safe_delete(redis, cache_key)
            await _track_agent_restart(redis, resolved_server_id, data.composite_id, data.agent_started_at)

            # F7: 메시지별 처리 흐름은 DEBUG — 1만 서버 시 분당 1만 line 방지.
            logger.debug(
                "metrics stored composite_id={} rows metrics={} disk_io={} net_io={} mount_usage={}",
                data.composite_id,
                insert_result.metrics,
                insert_result.disk_io,
                insert_result.net_io,
                insert_result.mount_usage,
            )

    return _handle
