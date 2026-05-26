"""Inventory 메시지 핸들러 — server.inventory routing key."""

from collections.abc import Callable, Coroutine
from typing import Any

from aio_pika.abc import AbstractIncomingMessage
from loguru import logger
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.cache.redis import safe_delete, safe_set
from assessment_engine.consumer.handlers._common import (
    _check_idempotent,
    _db_retry,
    _log_time_invariants,
)
from assessment_engine.consumer.mappers import to_inventory_create
from assessment_engine.consumer.schemas import InventoryInput
from assessment_engine.consumer.settings import consumer_settings
from assessment_engine.db.repositories.base_collect_repository import BaseCollectRepository


def make_inventory_handler(
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], BaseCollectRepository],
    redis: Redis,
) -> Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]:
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = InventoryInput.model_validate_json(message.body)
            except ValidationError as e:
                logger.error("inventory parse error count={}", len(e.errors()))
                raise

            if not await _check_idempotent(redis, data.message_id):
                logger.info("inventory duplicate skipped message_id={}", data.message_id)
                return

            await _log_time_invariants(redis, data)

            dto = to_inventory_create(data)

            async def upsert(repo: BaseCollectRepository) -> int:
                return await repo.upsert_server(dto)

            resolved_server_id = await _db_retry(session_factory, repo_factory, upsert)

            online_key = consumer_settings.redis_key_online.format(resolved_server_id)
            inventory_key = consumer_settings.redis_key_cache_inventory.format(resolved_server_id)
            await safe_set(redis, online_key, "1", ex=consumer_settings.redis_ttl_online)
            # 인벤토리 변경(서비스/포트/디스크 등) 즉시 반영 — TTL 만료 대기 제거
            await safe_delete(redis, inventory_key)

            logger.info("inventory stored composite_id={}", data.composite_id)

    return _handle
