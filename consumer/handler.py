from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from aio_pika.abc import AbstractIncomingMessage
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from consumer.schemas import ErrorInput, InventoryInput, MetricsInput
from db.repositories.base_collect_repository import BaseCollectRepository


async def _db_retry(
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], BaseCollectRepository],
    fn: Callable[[BaseCollectRepository], Coroutine[Any, Any, None]],
) -> None:
    for attempt in range(3):
        try:
            async with session_factory() as session:
                await fn(repo_factory(session))
                await session.commit()
            return
        except Exception as e:
            if attempt == 2:
                logger.error("db error after 3 attempts: {}", e)
                raise
            logger.warning("db error attempt={} error={}", attempt + 1, e)
            await asyncio.sleep(2 ** attempt)


def make_inventory_handler(
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], BaseCollectRepository],
) -> Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]:
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = InventoryInput.model_validate_json(message.body)
            except Exception as e:
                logger.error("inventory parse error: {}", e)
                raise

            await _db_retry(
                session_factory, repo_factory,
                lambda repo: repo.upsert_server(data),
            )
            logger.info("inventory stored machine_id={}", data.machine_id)

    return _handle


def make_metrics_handler(
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], BaseCollectRepository],
) -> Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]:
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = MetricsInput.model_validate_json(message.body)
            except Exception as e:
                logger.error("metrics parse error: {}", e)
                raise

            async def _save(repo: BaseCollectRepository) -> None:
                server_id = await repo.find_server_id(data.hostname)
                if server_id is None:
                    logger.warning(
                        "metrics dropped — server not registered hostname={}",
                        data.hostname,
                    )
                    return
                await repo.insert_metric(server_id, data)

            await _db_retry(session_factory, repo_factory, _save)
            logger.info("metrics stored hostname={}", data.hostname)

    return _handle


def make_error_handler() -> Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]:
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = ErrorInput.model_validate_json(message.body)
            except Exception as e:
                logger.error("error message parse error: {}", e)
                raise

            logger.warning(
                "agent error hostname={} component={} code={} msg={}",
                data.hostname,
                data.failed_component,
                data.error_code,
                data.error_message,
            )

    return _handle