import asyncio

import aio_pika
from loguru import logger

from config import consumer_settings
from consumer.deps import error_handler, inventory_handler, metrics_handler

_EXCHANGE = consumer_settings.rabbitmq_exchange
_DLX      = f"{_EXCHANGE}.dlx"

_QUEUES = [
    (consumer_settings.rabbitmq_routing_key_inventory, consumer_settings.rabbitmq_routing_key_inventory, inventory_handler),
    (consumer_settings.rabbitmq_routing_key_metrics,   consumer_settings.rabbitmq_routing_key_metrics,   metrics_handler),
    (consumer_settings.rabbitmq_routing_key_error,     consumer_settings.rabbitmq_routing_key_error,     error_handler),
]


async def main() -> None:
    logger.info("consumer starting exchange={}", _EXCHANGE)

    conn = await aio_pika.connect_robust(consumer_settings.broker_url)
    async with conn:
        channel = await conn.channel()
        await channel.set_qos(prefetch_count=10)

        dlx = await channel.declare_exchange(
            _DLX,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )

        exchange = await channel.declare_exchange(
            _EXCHANGE,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )

        for queue_name, routing_key, handler in _QUEUES:
            dlq = await channel.declare_queue(
                f"{queue_name}.dead",
                durable=True,
            )
            await dlq.bind(dlx, routing_key=queue_name)

            queue = await channel.declare_queue(
                queue_name,
                durable=True,
                arguments={
                    "x-dead-letter-exchange":     _DLX,
                    "x-dead-letter-routing-key":  queue_name,
                    "x-message-ttl":              60_000,
                },
            )
            await queue.bind(exchange, routing_key=routing_key)
            await queue.consume(handler)
            logger.info("consuming queue={} routing_key={}", queue_name, routing_key)

        await asyncio.Future()