import asyncio

import aio_pika
from loguru import logger

from assessment_engine.config import consumer_settings
from assessment_engine.consumer.handler import (
    make_error_handler,
    make_inventory_handler,
    make_metrics_handler,
    make_task_result_handler,
)
from assessment_engine.db.redis import close_pool, get_redis
from assessment_engine.db.repositories.collect_repository import CollectRepository
from assessment_engine.db.session import AsyncSessionLocal

_EXCHANGE = consumer_settings.rabbitmq_exchange
_DLX = f"{_EXCHANGE}.dlx"

# 큐 정책 (변경 시 broker의 기존 큐 재선언 PRECONDITION_FAILED 발생 — 큐 수동 삭제 필요)
# - metrics: 1분 주기 발행 + 단기 장애 회복 여유.
#   TTL 72h + max-length 1M(≈70KB/msg → broker 디스크 ~70GB), 둘 중 먼저 도달 시 oldest→DLX
# - error: 운영 알림용. 노이즈 방지를 위해 단기 TTL 유지
# - task.result: 결과 보고. 24h TTL로 운영자 처리 시간 확보, max-length로 백로그 폭주 방어
_METRICS_TTL_MS = 72 * 60 * 60 * 1000  # 72h
_METRICS_MAX_LEN = 1_000_000  # 큐 폭주 방어 (초과 시 oldest → DLX)
_ERROR_TTL_MS = 300_000  # 5분
_TASK_RESULT_TTL_MS = 24 * 60 * 60 * 1000  # 24h
_TASK_RESULT_MAX_LEN = 100_000


async def main() -> None:
    logger.info("consumer starting exchange={}", _EXCHANGE)

    redis = get_redis()
    try:
        queues = [
            (
                consumer_settings.rabbitmq_routing_key_inventory,
                make_inventory_handler(AsyncSessionLocal, CollectRepository, redis),
                None,
                None,
            ),
            (
                consumer_settings.rabbitmq_routing_key_metrics,
                make_metrics_handler(AsyncSessionLocal, CollectRepository, redis),
                _METRICS_TTL_MS,
                _METRICS_MAX_LEN,
            ),
            (
                consumer_settings.rabbitmq_routing_key_error,
                make_error_handler(redis),
                _ERROR_TTL_MS,
                None,
            ),
            (
                consumer_settings.rabbitmq_routing_key_task_result,
                make_task_result_handler(AsyncSessionLocal, CollectRepository, redis),
                _TASK_RESULT_TTL_MS,
                _TASK_RESULT_MAX_LEN,
            ),
        ]

        conn = await aio_pika.connect_robust(consumer_settings.broker_url, timeout=10)
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

                for key, handler, ttl_ms, max_len in queues:
                    dlq = await channel.declare_queue(
                        f"{key}.dead",
                        durable=True,
                    )
                    await dlq.bind(dlx, routing_key=key)

                    args: dict = {
                        "x-dead-letter-exchange": _DLX,
                        "x-dead-letter-routing-key": key,
                    }
                    if ttl_ms is not None:
                        args["x-message-ttl"] = ttl_ms
                    if max_len is not None:
                        args["x-max-length"] = max_len

                    queue = await channel.declare_queue(
                        key,
                        durable=True,
                        arguments=args,
                    )
                    await queue.bind(exchange, routing_key=key)
                    await queue.consume(handler)
                    logger.info("consuming queue={} ttl_ms={} max_len={}", key, ttl_ms, max_len)

                await asyncio.Future()
    finally:
        await close_pool()
