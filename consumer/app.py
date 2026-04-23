import asyncio

import aio_pika
from loguru import logger

from config import ConsumerSettings
from consumer.deps import handler

settings = ConsumerSettings()


async def run() -> None:
    logger.info("consumer starting queue={}", settings.rabbitmq_queue)

    conn = await aio_pika.connect_robust(settings.broker_url)
    async with conn:
        channel = await conn.channel()
        await channel.set_qos(prefetch_count=10)
        queue = await channel.declare_queue(settings.rabbitmq_queue, durable=True)
        await queue.consume(handler)
        logger.info("waiting for messages on queue={}", settings.rabbitmq_queue)
        await asyncio.Future()