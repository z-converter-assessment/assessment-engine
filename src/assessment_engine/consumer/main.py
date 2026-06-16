import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import aio_pika
from aio_pika.abc import AbstractIncomingMessage
from loguru import logger

from assessment_engine.cache.redis import close_pool, get_redis
from assessment_engine.consumer.handlers import (
    make_error_handler,
    make_inventory_handler,
    make_metrics_handler,
    make_task_result_handler,
)
from assessment_engine.consumer.settings import consumer_settings
from assessment_engine.db.repositories.collect_repository import CollectRepository
from assessment_engine.db.session import AsyncSessionLocal
from assessment_engine.log_config import setup_logging

_COLLECT_EXCHANGE = consumer_settings.rabbitmq_exchange
_COLLECT_DLX = f"{_COLLECT_EXCHANGE}.dlx"
_TASK_EXCHANGE = consumer_settings.rabbitmq_task_exchange
_TASK_DLX = f"{_TASK_EXCHANGE}.dlx"

# 큐 정책 변경 시 broker의 기존 큐 재선언이 PRECONDITION_FAILED — 큐 수동 삭제 필요.
# TTL/max-length 둘 중 먼저 도달 시 oldest -> DLX.
_METRICS_TTL_MS = 72 * 60 * 60 * 1000  # 72h
_METRICS_MAX_LEN = 1_000_000
_ERROR_TTL_MS = 300_000  # 5분
_TASK_RESULT_TTL_MS = 24 * 60 * 60 * 1000  # 24h
_TASK_RESULT_MAX_LEN = 100_000


@dataclass
class _QueueBinding:
    exchange_name: str
    dlx_name: str
    queue_name: str
    routing_key: str
    handler: Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]
    ttl_ms: int | None
    max_len: int | None


async def main() -> None:
    setup_logging(consumer_settings.log_format)

    logger.info(
        "consumer starting collect_exchange={} task_exchange={}",
        _COLLECT_EXCHANGE,
        _TASK_EXCHANGE,
    )

    redis = get_redis()
    try:
        bindings = [
            _QueueBinding(
                exchange_name=_COLLECT_EXCHANGE,
                dlx_name=_COLLECT_DLX,
                queue_name=consumer_settings.rabbitmq_routing_key_inventory,
                routing_key=consumer_settings.rabbitmq_routing_key_inventory,
                handler=make_inventory_handler(AsyncSessionLocal, CollectRepository, redis),
                ttl_ms=None,
                max_len=None,
            ),
            _QueueBinding(
                exchange_name=_COLLECT_EXCHANGE,
                dlx_name=_COLLECT_DLX,
                queue_name=consumer_settings.rabbitmq_routing_key_metrics,
                routing_key=consumer_settings.rabbitmq_routing_key_metrics,
                handler=make_metrics_handler(AsyncSessionLocal, CollectRepository, redis),
                ttl_ms=_METRICS_TTL_MS,
                max_len=_METRICS_MAX_LEN,
            ),
            _QueueBinding(
                exchange_name=_COLLECT_EXCHANGE,
                dlx_name=_COLLECT_DLX,
                queue_name=consumer_settings.rabbitmq_routing_key_error,
                routing_key=consumer_settings.rabbitmq_routing_key_error,
                handler=make_error_handler(redis),
                ttl_ms=_ERROR_TTL_MS,
                max_len=None,
            ),
            _QueueBinding(
                exchange_name=_TASK_EXCHANGE,
                dlx_name=_TASK_DLX,
                queue_name=consumer_settings.rabbitmq_queue_worker_result,
                routing_key=consumer_settings.rabbitmq_routing_key_task_result,
                handler=make_task_result_handler(AsyncSessionLocal, CollectRepository, redis),
                ttl_ms=_TASK_RESULT_TTL_MS,
                max_len=_TASK_RESULT_MAX_LEN,
            ),
        ]

        conn = await aio_pika.connect_robust(consumer_settings.broker_url, timeout=10)
        async with conn, conn.channel() as channel:
            await channel.set_qos(prefetch_count=10)

            exchanges: dict[str, aio_pika.abc.AbstractExchange] = {}
            for name in (_COLLECT_EXCHANGE, _TASK_EXCHANGE, _COLLECT_DLX, _TASK_DLX):
                exchanges[name] = await channel.declare_exchange(
                    name,
                    aio_pika.ExchangeType.DIRECT,
                    durable=True,
                )

            for b in bindings:
                dlq = await channel.declare_queue(
                    f"{b.queue_name}.dead",
                    durable=True,
                )
                await dlq.bind(exchanges[b.dlx_name], routing_key=b.queue_name)

                args: dict = {
                    "x-dead-letter-exchange": b.dlx_name,
                    "x-dead-letter-routing-key": b.queue_name,
                }
                if b.ttl_ms is not None:
                    args["x-message-ttl"] = b.ttl_ms
                if b.max_len is not None:
                    args["x-max-length"] = b.max_len

                queue = await channel.declare_queue(
                    b.queue_name,
                    durable=True,
                    arguments=args,
                )
                await queue.bind(exchanges[b.exchange_name], routing_key=b.routing_key)
                await queue.consume(b.handler)
                logger.info(
                    "consuming exchange={} queue={} routing_key={} ttl_ms={} max_len={}",
                    b.exchange_name,
                    b.queue_name,
                    b.routing_key,
                    b.ttl_ms,
                    b.max_len,
                )

            await asyncio.Future()
    finally:
        await close_pool()
