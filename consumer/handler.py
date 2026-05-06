import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

from uuid import UUID

from aio_pika.abc import AbstractIncomingMessage
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import consumer_settings
from consumer.mappers import to_inventory_create, to_metric_create
from consumer.schemas import ErrorInput, InventoryInput, MetricsInput
from db.repositories.base_collect_repository import BaseCollectRepository


async def _db_retry(
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], BaseCollectRepository],
    fn: Callable[[BaseCollectRepository], Coroutine[Any, Any, T]],
) -> T:
    for attempt in range(3):
        try:
            async with session_factory() as session:
                result = await fn(repo_factory(session))
                await session.commit()
            return result
        except Exception as e:
            if attempt == 2:
                logger.error("db error after 3 attempts: {}", e)
                raise
            logger.warning("db error attempt={} error={}", attempt + 1, e)
            await asyncio.sleep(2 ** attempt)
    raise AssertionError("unreachable")


async def _check_idempotent(redis: Redis, message_id: UUID) -> bool:
    """SET NX로 원자적으로 처리 여부 확인. 처음 처리면 True, 중복이면 False."""
    key = consumer_settings.redis_key_idempotent.format(message_id.hex)
    return bool(await redis.set(key, 1, ex=consumer_settings.redis_ttl_idempotent, nx=True))


def make_inventory_handler(
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], BaseCollectRepository],
    redis: Redis,
) -> Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]:
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = InventoryInput.model_validate_json(message.body)
            except Exception as e:
                logger.error("inventory parse error: {}", e)
                raise

            if not await _check_idempotent(redis, data.message_id):
                logger.info("inventory duplicate skipped message_id={}", data.message_id)
                return

            dto = to_inventory_create(data)

            async def upsert(repo: BaseCollectRepository) -> int | None:
                return await repo.upsert_server(dto)

            resolved_server_id = await _db_retry(session_factory, repo_factory, upsert)

            if resolved_server_id is not None:
                online_key = consumer_settings.redis_key_online.format(resolved_server_id)
                await redis.set(online_key, 1, ex=consumer_settings.redis_ttl_online)

            logger.info("inventory stored machine_id={}", data.machine_id)

    return _handle


def make_metrics_handler(
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], BaseCollectRepository],
    redis: Redis,
) -> Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]:
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = MetricsInput.model_validate_json(message.body)
            except Exception as e:
                logger.error("metrics parse error: {}", e)
                raise

            if not await _check_idempotent(redis, data.message_id):
                logger.info("metrics duplicate skipped message_id={}", data.message_id)
                return

            dto = to_metric_create(data)

            async def save(repo: BaseCollectRepository) -> int | None:
                server_id = await repo.find_server_id(data.machine_id)
                if server_id is None:
                    logger.warning(
                        "metrics dropped — server not registered machine_id={}",
                        data.machine_id,
                    )
                    return None
                await repo.insert_metric(server_id, dto)
                return server_id

            resolved_server_id = await _db_retry(session_factory, repo_factory, save)

            if resolved_server_id is not None:
                online_key = consumer_settings.redis_key_online.format(resolved_server_id)
                cache_key = consumer_settings.redis_key_cache_metrics.format(resolved_server_id)
                await redis.set(online_key, 1, ex=consumer_settings.redis_ttl_online)
                await redis.delete(cache_key)
                await redis.publish(
                    consumer_settings.redis_channel_metrics,
                    json.dumps({"server_id": resolved_server_id, "machine_id": data.machine_id}),
                )
                logger.info("metrics stored machine_id={}", data.machine_id)

    return _handle


def make_error_handler(
    redis: Redis,
) -> Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]:
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = ErrorInput.model_validate_json(message.body)
            except Exception as e:
                logger.error("error message parse error: {}", e)
                raise

            if not await _check_idempotent(redis, data.message_id):
                logger.info("error duplicate skipped message_id={}", data.message_id)
                return

            logger.warning(
                "agent error machine_id={} component={} code={} msg={}",
                data.machine_id,
                data.failed_component,
                data.error_code,
                data.error_message,
            )

    return _handle