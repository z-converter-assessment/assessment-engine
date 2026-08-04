"""Metrics 메시지 핸들러 — server.metrics routing key. metrics 핸들러는 미등록 서버 auto-register."""

from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import ValidationError

from assessment_engine.cache.redis import safe_delete, safe_set
from assessment_engine.consumer.handlers._common import (
    _check_idempotent,
    _db_retry,
    _format_validation_err,
    _log_time_invariants,
    _track_agent_restart,
)
from assessment_engine.consumer.mappers import build_placeholder_inventory, to_metric_create
from assessment_engine.consumer.schemas import MetricsInput
from assessment_engine.consumer.settings import get_consumer_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from aio_pika.abc import AbstractIncomingMessage
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
                detail = _format_validation_err(e)
                logger.error("metrics parse error {}", detail)
                # 핸들러 밖으로 빠져나간 예외는 asyncio 가 전문을 출력한다 — 실패 필드의 입력값을
                # 문자열에 싣는 원본 대신, nack 에 필요한 만큼만 담은 예외로 바꿔 던진다.
                raise ValueError(f"metrics validation failed: {detail}") from None

            if not await _check_idempotent(redis, data.message_id):
                logger.info("metrics duplicate skipped message_id={}", data.message_id)
                return

            # 에이전트 설치 서버는 UTC 정상 시각 발행 전제 — collected_at 을 발행값 그대로 신뢰 (ADR 0050).
            # _log_time_invariants 는 시계·시작순서 이상을 로그로만 노출 (데이터 변형 0, 관측 방어선).
            await _log_time_invariants(redis, data)

            dto = to_metric_create(data)
            placeholder = build_placeholder_inventory(data)

            async def save(repo: BaseCollectRepository) -> tuple[int, bool, MetricInsertResult]:
                # ensure_server_id 가 find->upsert 캡슐화. find 성공 시 placeholder 미사용.
                server_id, auto_registered = await repo.ensure_server_id(str(data.agent_id), placeholder)
                result = await repo.record_metrics(server_id, dto)
                return server_id, auto_registered, result

            resolved_server_id, auto_registered, insert_result = await _db_retry(session_factory, repo_factory, save)

            if auto_registered:
                logger.info(
                    "auto-registered server from metrics agent_id={} "
                    "(hostname·정적 정보는 다음 inventory 도착 시 채워짐)",
                    data.agent_id,
                )

            online_key = get_consumer_settings().redis_key_online.format(resolved_server_id)
            cache_key = get_consumer_settings().redis_key_cache_metrics.format(resolved_server_id)
            await safe_set(redis, online_key, "1", ex=get_consumer_settings().redis_ttl_online)
            await safe_delete(redis, cache_key)
            await _track_agent_restart(redis, resolved_server_id, str(data.agent_id), data.agent_started_at)

            # F7: 메시지별 처리 흐름은 DEBUG — 1만 서버 시 분당 1만 line 방지.
            logger.debug(
                "metrics stored agent_id={} rows metrics={} disk_io={} net_io={} "
                "filesystem={} cpu_core={} pressure={} disk_error={}",
                data.agent_id,
                insert_result.metrics,
                insert_result.disk_io,
                insert_result.net_io,
                insert_result.filesystem,
                insert_result.cpu_core,
                insert_result.pressure,
                insert_result.disk_error,
            )

    return _handle
