import asyncio

import aio_pika
from loguru import logger

from config import consumer_settings
from db.redis import close_pool, get_redis
from db.repositories.collect_repository import CollectRepository
from db.session import AsyncSessionLocal
from consumer.handler import make_error_handler, make_inventory_handler, make_metrics_handler

_EXCHANGE = consumer_settings.rabbitmq_exchange
_DLX      = f"{_EXCHANGE}.dlx"


async def main() -> None:
    logger.info("consumer starting exchange={}", _EXCHANGE)

    redis = get_redis()
    try:
        queues = [
            (consumer_settings.rabbitmq_routing_key_inventory, make_inventory_handler(AsyncSessionLocal, CollectRepository, redis), None),
            (consumer_settings.rabbitmq_routing_key_metrics,   make_metrics_handler(AsyncSessionLocal, CollectRepository, redis),   300_000),
            (consumer_settings.rabbitmq_routing_key_error,     make_error_handler(redis),                                          300_000),
        ]

        conn = await aio_pika.connect_robust(consumer_settings.broker_url)
        async with conn:
            async with conn.channel() as channel:
                await channel.set_qos(prefetch_count=10)

                dlx = await channel.declare_exchange(
                    _DLX,
                    aio_pika.ExchangeType.DIRECT,
                    durable=True,
                )

                exchange = await channel.declare_exchange(
                    _EXCHANGE,
                    aio_pika.ExchangeType.DIRECT,
                    durable=True,
                )

                for key, handler, ttl_ms in queues:
                    dlq = await channel.declare_queue(
                        f"{key}.dead",
                        durable=True,
                    )
                    await dlq.bind(dlx, routing_key=key)

                    args: dict = {
                        "x-dead-letter-exchange":    _DLX,
                        "x-dead-letter-routing-key": key,
                    }
                    if ttl_ms is not None:
                        args["x-message-ttl"] = ttl_ms

                    queue = await channel.declare_queue(
                        key,
                        durable=True,
                        arguments=args,
                    )
                    await queue.bind(exchange, routing_key=key)
                    await queue.consume(handler)
                    logger.info("consuming queue={} ttl_ms={}", key, ttl_ms)

                await asyncio.Future()
    finally:
        await close_pool()