"""Inventory 메시지 핸들러 — server.inventory routing key."""

from typing import TYPE_CHECKING

from loguru import logger

from assessment_engine.cache.redis import safe_delete, safe_set
from assessment_engine.consumer.handlers._common import (
    _check_idempotent,
    _db_retry,
    _in_message_context,
    _log_time_invariants,
)
from assessment_engine.consumer.mappers import to_inventory_create
from assessment_engine.consumer.schemas import InventoryInput
from assessment_engine.consumer.settings import get_consumer_settings

if TYPE_CHECKING:
    from collections.abc import Callable

    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from assessment_engine.consumer.handlers._types import MessageHandler
    from assessment_engine.db.repositories.collect import CollectRepository


def make_inventory_handler(
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], CollectRepository],
    redis: Redis,
) -> MessageHandler:
    async def _store(data: InventoryInput) -> None:
        if not await _check_idempotent(redis, data.message_id):
            logger.info("inventory duplicate skipped message_id={}", data.message_id)
            return

        await _log_time_invariants(redis, data)

        dto = to_inventory_create(data)

        async def upsert(repo: CollectRepository) -> int:
            return await repo.upsert_server(dto)

        resolved_server_id = await _db_retry(session_factory, repo_factory, upsert)

        online_key = get_consumer_settings().redis_key_online.format(resolved_server_id)
        inventory_key = get_consumer_settings().redis_key_cache_inventory.format(resolved_server_id)
        await safe_set(redis, online_key, "1", ex=get_consumer_settings().redis_ttl_online)
        # 인벤토리 변경 즉시 반영 — TTL 만료 대기 제거
        await safe_delete(redis, inventory_key)

        logger.info("inventory stored agent_id={}", data.agent_id)

    return _in_message_context(InventoryInput, "inventory", _store)
