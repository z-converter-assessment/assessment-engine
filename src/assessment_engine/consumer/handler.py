import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

from uuid import UUID

from aio_pika.abc import AbstractIncomingMessage
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.config import consumer_settings
from assessment_engine.consumer.mappers import placeholder_inventory_from_metrics, to_inventory_create, to_metric_create
from assessment_engine.consumer.schemas import ErrorInput, InventoryInput, MetricsInput
from assessment_engine.db.redis import safe_delete, safe_publish, safe_set, safe_set_nx
from assessment_engine.db.repositories.base_collect_repository import BaseCollectRepository, MetricInsertResult


# retry 가치가 있는 예외(connection lost, deadlock 등)와 영구 장애(스키마 위반·UNIQUE 등)를 분리.
# - OperationalError: connection·timeout·deadlock 등 일시 장애.
# - DBAPIError: asyncpg 드라이버 일시 오류. 단 IntegrityError는 DBAPIError를 상속하므로 별도 캐치.
# - IntegrityError: UNIQUE/FK 위반 — retry 무의미 (단 record_metrics는 ON CONFLICT DO NOTHING이라 도달 거의 없음).
_RETRYABLE_DB_EXC = (OperationalError, DBAPIError)


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
        except IntegrityError as e:
            # 영구 장애 — retry 의미 없음. 즉시 raise → 핸들러가 nack → DLQ.
            logger.error("db integrity error (non-retryable): {}", e)
            raise
        except _RETRYABLE_DB_EXC as e:
            if attempt == 2:
                logger.error("db error after 3 attempts: {}", e)
                raise
            logger.warning("db error attempt={} error={}", attempt + 1, e)
            await asyncio.sleep(5 ** (attempt + 1))
    raise AssertionError("unreachable")


async def _check_idempotent(redis: Redis, message_id: UUID) -> bool:
    """SET NX 멱등성 체크. 첫 처리면 True, 중복이면 False.

    Redis 장애 시 fail-open (True 반환) — 처리 진행. DB UNIQUE 제약(2단)이 중복 INSERT를 흡수.
    docs/decisions/redis-decoupling.md §6 단계 4 참조.
    """
    key = consumer_settings.redis_key_idempotent.format(message_id.hex)
    result = await safe_set_nx(redis, key, "1", consumer_settings.redis_ttl_idempotent)
    return True if result is None else result


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

            async def upsert(repo: BaseCollectRepository) -> int:
                return await repo.upsert_server(dto)

            resolved_server_id = await _db_retry(session_factory, repo_factory, upsert)

            online_key    = consumer_settings.redis_key_online.format(resolved_server_id)
            inventory_key = consumer_settings.redis_key_cache_inventory.format(resolved_server_id)
            await safe_set(redis, online_key, "1", ex=consumer_settings.redis_ttl_online)
            # 인벤토리 변경(서비스/포트/디스크 등) 즉시 반영 — TTL 만료 대기 제거
            await safe_delete(redis, inventory_key)

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
            placeholder = placeholder_inventory_from_metrics(data)

            async def save(repo: BaseCollectRepository) -> tuple[int, bool, MetricInsertResult]:
                # find→upsert 흐름은 repo.ensure_server_id로 캡슐화. find 성공 시 placeholder 미사용.
                server_id, auto_registered = await repo.ensure_server_id(data.machine_id, placeholder)
                result = await repo.record_metrics(server_id, dto)
                return server_id, auto_registered, result

            resolved_server_id, auto_registered, insert_result = await _db_retry(
                session_factory, repo_factory, save
            )

            if auto_registered:
                logger.info(
                    "auto-registered server from metrics machine_id={} hostname={} "
                    "(정적 정보는 다음 inventory 도착 시 채워짐)",
                    data.machine_id, data.hostname,
                )

            online_key = consumer_settings.redis_key_online.format(resolved_server_id)
            cache_key  = consumer_settings.redis_key_cache_metrics.format(resolved_server_id)
            await safe_set(redis, online_key, "1", ex=consumer_settings.redis_ttl_online)
            await safe_delete(redis, cache_key)
            await safe_publish(
                redis,
                consumer_settings.redis_channel_metrics,
                json.dumps({"server_id": resolved_server_id, "machine_id": data.machine_id}),
            )
            logger.info(
                "metrics stored machine_id={} rows metrics={} disk_io={} net_io={} mount_usage={}",
                data.machine_id,
                insert_result.metrics, insert_result.disk_io,
                insert_result.net_io, insert_result.mount_usage,
            )

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
                "agent error machine_id={} component={} code={} msg={} "
                "retry_count={} first_failed_at={} recovered_at={}",
                data.machine_id,
                data.failed_component,
                data.error_code,
                data.error_message,
                data.retry_count,
                data.first_failed_at,
                data.recovered_at,
            )

    return _handle