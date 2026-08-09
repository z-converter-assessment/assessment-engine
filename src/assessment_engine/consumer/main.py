"""RabbitMQ Consumer 프로세스의 큐 선언, 핸들러 등록, 종료 처리를 조립한다."""

import asyncio
import signal
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import aio_pika
from aio_pika.exceptions import AMQPError, ChannelInvalidStateError
from loguru import logger

from assessment_engine.cache.redis import close_pool, get_redis
from assessment_engine.consumer.handlers._types import MessageHandler
from assessment_engine.consumer.handlers.error import make_error_handler
from assessment_engine.consumer.handlers.inventory import make_inventory_handler
from assessment_engine.consumer.handlers.metrics import make_metrics_handler
from assessment_engine.consumer.handlers.task_result import make_task_result_handler
from assessment_engine.consumer.settings import get_consumer_settings
from assessment_engine.db.repositories.collect_sql import SqlCollectRepository
from assessment_engine.db.session import dispose_engine, get_session_factory
from assessment_engine.log_config import setup_logging

if TYPE_CHECKING:
    from aio_pika.abc import AbstractIncomingMessage, AbstractQueue, ConsumerTag


_METRICS_TTL_MS = 72 * 60 * 60 * 1000
_METRICS_MAX_LEN = 1_000_000
_ERROR_TTL_MS = 300_000
_TASK_RESULT_TTL_MS = 24 * 60 * 60 * 1000
_TASK_RESULT_MAX_LEN = 100_000

# 종료 신호 후 진행 중 핸들러를 기다리는 총 예산. compose 의 `stop_grace_period` 안에서 끝나야
# SIGKILL 이 drain 을 자르지 않는다 (docker-compose.yml 의 consumer 서비스가 그 값을 선언한다).
_SHUTDOWN_DRAIN_SEC = 5.0


@dataclass
class _QueueBinding:
    exchange_name: str
    dead_letter_exchange_name: str
    queue_name: str
    routing_key: str
    handler: MessageHandler
    ttl_ms: int | None
    max_len: int | None


async def _run_logged(handler: MessageHandler, message: AbstractIncomingMessage) -> None:
    """핸들러 예외를 구조화 로그로 기록해 Consumer 이벤트 루프를 계속 실행한다."""
    try:
        await handler(message)
    except Exception:  # noqa: BLE001  루프를 죽이지 않는 것이 목적이라 좁히지 않는다
        logger.exception(
            "handler failed routing_key={} message_id={} delivery_tag={}",
            message.routing_key,
            message.message_id,
            message.delivery_tag,
        )


def _track_inflight(handler: MessageHandler, inflight: set[asyncio.Task[Any]]) -> MessageHandler:
    """메시지 처리 task를 작업 집합에 등록하고, 처리가 끝나면 제거하는 핸들러를 반환한다."""

    async def _run(message: AbstractIncomingMessage) -> None:
        task = asyncio.current_task()
        if task is None:
            await _run_logged(handler, message)
            return
        inflight.add(task)
        try:
            await _run_logged(handler, message)
        finally:
            inflight.discard(task)

    return _run


async def _drain(consumers: list[tuple[AbstractQueue, ConsumerTag]], inflight: set[asyncio.Task[Any]]) -> None:
    """Consumer 종료 시 새 delivery를 중단하고, 종료 제한 시간 안에서 실행 중 handler task의 완료를 기다린다.

    제한 시간을 넘기면 이 함수가 return한다. 호출자 main()이 채널을 닫으면 broker가 남은 unack 메시지를 requeue한다.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _SHUTDOWN_DRAIN_SEC
    for queue, tag in consumers:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break

        with suppress(AMQPError, ChannelInvalidStateError, TimeoutError):
            await asyncio.wait_for(queue.cancel(tag), timeout=remaining)
    while inflight:
        remaining = deadline - loop.time()
        if remaining <= 0:
            logger.warning("drain timeout inflight={} unack messages remain for redelivery", len(inflight))
            return
        await asyncio.wait(set(inflight), timeout=remaining)


async def main() -> None:
    """4개 RabbitMQ 큐를 선언하고 핸들러를 등록한 뒤 종료 신호까지 Consumer를 실행한다."""
    setup_logging(get_consumer_settings().log_format, get_consumer_settings().log_level)

    collect_exchange = get_consumer_settings().rabbitmq_exchange
    collect_dlx = f"{collect_exchange}.dlx"
    task_exchange = get_consumer_settings().rabbitmq_task_exchange
    task_dlx = f"{task_exchange}.dlx"

    logger.info(
        "consumer starting collect_exchange={} task_exchange={}",
        collect_exchange,
        task_exchange,
    )

    redis = get_redis()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # 일부 환경에서는 핸들러 등록이 지원되지 않는다.
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)
    try:
        bindings = [
            _QueueBinding(
                exchange_name=collect_exchange,
                dead_letter_exchange_name=collect_dlx,
                queue_name=get_consumer_settings().rabbitmq_routing_key_inventory,
                routing_key=get_consumer_settings().rabbitmq_routing_key_inventory,
                handler=make_inventory_handler(get_session_factory(), SqlCollectRepository, redis),
                ttl_ms=None,
                max_len=None,
            ),
            _QueueBinding(
                exchange_name=collect_exchange,
                dead_letter_exchange_name=collect_dlx,
                queue_name=get_consumer_settings().rabbitmq_routing_key_metrics,
                routing_key=get_consumer_settings().rabbitmq_routing_key_metrics,
                handler=make_metrics_handler(get_session_factory(), SqlCollectRepository, redis),
                ttl_ms=_METRICS_TTL_MS,
                max_len=_METRICS_MAX_LEN,
            ),
            _QueueBinding(
                exchange_name=collect_exchange,
                dead_letter_exchange_name=collect_dlx,
                queue_name=get_consumer_settings().rabbitmq_routing_key_error,
                routing_key=get_consumer_settings().rabbitmq_routing_key_error,
                handler=make_error_handler(redis),
                ttl_ms=_ERROR_TTL_MS,
                max_len=None,
            ),
            _QueueBinding(
                exchange_name=task_exchange,
                dead_letter_exchange_name=task_dlx,
                queue_name=get_consumer_settings().rabbitmq_queue_worker_result,
                routing_key=get_consumer_settings().rabbitmq_routing_key_task_result,
                handler=make_task_result_handler(
                    get_session_factory(),
                    SqlCollectRepository,
                    redis,
                    get_consumer_settings().task_install_success_exit_codes,
                ),
                ttl_ms=_TASK_RESULT_TTL_MS,
                max_len=_TASK_RESULT_MAX_LEN,
            ),
        ]

        inflight: set[asyncio.Task[Any]] = set()
        consumers: list[tuple[AbstractQueue, ConsumerTag]] = []

        conn = await aio_pika.connect_robust(get_consumer_settings().broker_url, timeout=10)
        async with conn, conn.channel() as channel:
            await channel.set_qos(prefetch_count=10)

            collect_exchange_obj = await channel.declare_exchange(
                collect_exchange,
                aio_pika.ExchangeType.DIRECT,
                durable=True,
            )
            task_exchange_obj = await channel.declare_exchange(
                task_exchange,
                aio_pika.ExchangeType.DIRECT,
                durable=True,
            )
            collect_dlx_obj = await channel.declare_exchange(
                collect_dlx,
                aio_pika.ExchangeType.DIRECT,
                durable=True,
            )
            task_dlx_obj = await channel.declare_exchange(
                task_dlx,
                aio_pika.ExchangeType.DIRECT,
                durable=True,
            )
            source_exchanges = {
                collect_exchange: collect_exchange_obj,
                task_exchange: task_exchange_obj,
            }
            dead_letter_exchanges = {
                collect_dlx: collect_dlx_obj,
                task_dlx: task_dlx_obj,
            }

            for b in bindings:
                dlq = await channel.declare_queue(
                    f"{b.queue_name}.dead",
                    durable=True,
                )
                await dlq.bind(
                    dead_letter_exchanges[b.dead_letter_exchange_name],
                    routing_key=b.queue_name,
                )

            for b in bindings:
                args: dict[str, Any] = {
                    "x-dead-letter-exchange": b.dead_letter_exchange_name,
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
                await queue.bind(source_exchanges[b.exchange_name], routing_key=b.routing_key)
                # 각 일반 queue에 대해, 콜백 등록
                consumers.append((queue, await queue.consume(_track_inflight(b.handler, inflight))))
                logger.info(
                    "consuming exchange={} queue={} routing_key={} ttl_ms={} max_len={}",
                    b.exchange_name,
                    b.queue_name,
                    b.routing_key,
                    b.ttl_ms,
                    b.max_len,
                )

            await stop_event.wait()
            logger.info("consumer stopping (signal received) — draining in-flight={}", len(inflight))
            await _drain(consumers, inflight)
    finally:
        await dispose_engine()
        await close_pool()
