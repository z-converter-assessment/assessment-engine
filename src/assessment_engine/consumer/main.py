import asyncio
import signal
from collections.abc import Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractQueue, ConsumerTag
from aio_pika.exceptions import AMQPError, ChannelInvalidStateError
from loguru import logger

from assessment_engine.cache.redis import close_pool, get_redis
from assessment_engine.consumer.handlers import (
    make_error_handler,
    make_inventory_handler,
    make_metrics_handler,
    make_task_result_handler,
)
from assessment_engine.consumer.settings import get_consumer_settings
from assessment_engine.db.repositories.collect_sql import SqlCollectRepository
from assessment_engine.db.session import get_session_factory
from assessment_engine.log_config import setup_logging

# 큐 정책 변경 시 broker의 기존 큐 재선언이 PRECONDITION_FAILED — 큐 수동 삭제 필요.
# TTL/max-length 둘 중 먼저 도달 시 oldest -> DLX.
_METRICS_TTL_MS = 72 * 60 * 60 * 1000  # 72h
_METRICS_MAX_LEN = 1_000_000
_ERROR_TTL_MS = 300_000  # 5분
_TASK_RESULT_TTL_MS = 24 * 60 * 60 * 1000  # 24h
_TASK_RESULT_MAX_LEN = 100_000

# 종료 신호 후 진행 중 핸들러를 기다리는 총 예산. docker 기본 stop_grace_period(10s) 안에서 끝나야
# SIGKILL 이 drain 을 자르지 않는다.
_SHUTDOWN_DRAIN_SEC = 5.0

type _Handler = Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]


@dataclass
class _QueueBinding:
    exchange_name: str
    dlx_name: str
    queue_name: str
    routing_key: str
    handler: _Handler
    ttl_ms: int | None
    max_len: int | None


async def _run_logged(handler: _Handler, message: AbstractIncomingMessage) -> None:
    """핸들러 예외를 loguru 로 회수한다.

    aio-pika 는 콜백을 task 로 띄우고 결과를 아무도 회수하지 않아, 여기서 잡지 않으면 asyncio 기본
    핸들러가 stdlib logging 으로 평문 traceback 을 낸다 — LOG_FORMAT=json 계약이 깨진다 (F7). ack/nack
    은 핸들러 안 `message.process` 컨텍스트가 이미 끝냈으므로 삼켜도 배달 처리에 영향이 없다.
    타입을 좁히지 않는 유일한 자리 — 미리 알 수 없는 예외를 회수하는 것이 이 wrapper 의 목적이다.
    """
    try:
        await handler(message)
    except Exception:
        logger.exception(
            "handler failed routing_key={} message_id={} delivery_tag={}",
            message.routing_key,
            message.message_id,
            message.delivery_tag,
        )


def _track_inflight(handler: _Handler, inflight: set[asyncio.Task[Any]]) -> _Handler:
    """핸들러 실행 task 를 등록한다 — 종료 시 기다릴 대상 (`_drain`)."""

    async def _run(message: AbstractIncomingMessage) -> None:
        task = asyncio.current_task()
        if task is None:  # aio-pika 는 콜백을 task 로 띄운다. 아니면 추적할 대상이 없다.
            await _run_logged(handler, message)
            return
        inflight.add(task)
        try:
            await _run_logged(handler, message)
        finally:
            inflight.discard(task)

    return _run


async def _drain(consumers: list[tuple[AbstractQueue, ConsumerTag]], inflight: set[asyncio.Task[Any]]) -> None:
    """새 배달을 끊고 진행 중 핸들러가 ack/nack 를 마칠 때까지 예산 안에서 기다린다 (F11).

    채널 close 는 aiormq 가 진행 중 consumer task 에 CancelledError 를 던지므로, 그전에 기다리지 않으면
    커밋 직전 취소된 메시지가 재전송 시 멱등성 1단(#D2)에 중복으로 걸려 조용히 사라진다. basic.cancel 은
    이미 배달된 메시지의 ack 를 막지 않아 남은 in-flight 는 그대로 마칠 수 있다.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _SHUTDOWN_DRAIN_SEC
    for queue, tag in consumers:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        # broker 가 이미 끊긴 상태면 끊을 배달도 없다 — 남은 메시지는 unack 이라 재전송된다.
        # cancel 인자 timeout 은 basic.cancel RPC 만 덮는다. robust 채널은 그전에 재연결 완료를 무기한
        # 기다리므로(RobustChannel.get_underlay_channel -> connection.ready) 호출 전체를 wait_for 로 묶는다.
        with suppress(AMQPError, ChannelInvalidStateError, TimeoutError):
            await asyncio.wait_for(queue.cancel(tag), timeout=remaining)
    while inflight:
        remaining = deadline - loop.time()
        if remaining <= 0:
            logger.warning("drain timeout inflight={} — 미완 메시지는 unack 잔류(재전송)", len(inflight))
            return
        await asyncio.wait(set(inflight), timeout=remaining)


async def main() -> None:
    setup_logging(get_consumer_settings().log_format)

    # exchange 이름은 여기서 읽는다 — 모듈 스코프에 두면 import 만으로 설정을 요구한다.
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
    # graceful shutdown (F11) — asyncio.run 은 SIGTERM(docker stop)을 취소로 변환하지 않으므로 asyncio-native
    # add_signal_handler 로 stop_event 를 set 한다(signal.signal 아님 — 이벤트 루프 안전). stop_event 가 깨면
    # `_drain` 이 배달을 끊고 in-flight 를 기다린 뒤 `async with conn` unwind 로 채널/커넥션 close,
    # finally 로 redis close.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):  # 비 POSIX 방어
            loop.add_signal_handler(sig, stop_event.set)
    try:
        bindings = [
            _QueueBinding(
                exchange_name=collect_exchange,
                dlx_name=collect_dlx,
                queue_name=get_consumer_settings().rabbitmq_routing_key_inventory,
                routing_key=get_consumer_settings().rabbitmq_routing_key_inventory,
                handler=make_inventory_handler(get_session_factory(), SqlCollectRepository, redis),
                ttl_ms=None,
                max_len=None,
            ),
            _QueueBinding(
                exchange_name=collect_exchange,
                dlx_name=collect_dlx,
                queue_name=get_consumer_settings().rabbitmq_routing_key_metrics,
                routing_key=get_consumer_settings().rabbitmq_routing_key_metrics,
                handler=make_metrics_handler(get_session_factory(), SqlCollectRepository, redis),
                ttl_ms=_METRICS_TTL_MS,
                max_len=_METRICS_MAX_LEN,
            ),
            _QueueBinding(
                exchange_name=collect_exchange,
                dlx_name=collect_dlx,
                queue_name=get_consumer_settings().rabbitmq_routing_key_error,
                routing_key=get_consumer_settings().rabbitmq_routing_key_error,
                handler=make_error_handler(redis),
                ttl_ms=_ERROR_TTL_MS,
                max_len=None,
            ),
            _QueueBinding(
                exchange_name=task_exchange,
                dlx_name=task_dlx,
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

            exchanges: dict[str, aio_pika.abc.AbstractExchange] = {}
            for name in (collect_exchange, task_exchange, collect_dlx, task_dlx):
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

                args: dict[str, Any] = {
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
                consumers.append((queue, await queue.consume(_track_inflight(b.handler, inflight))))
                logger.info(
                    "consuming exchange={} queue={} routing_key={} ttl_ms={} max_len={}",
                    b.exchange_name,
                    b.queue_name,
                    b.routing_key,
                    b.ttl_ms,
                    b.max_len,
                )

            await stop_event.wait()  # SIGTERM/SIGINT 까지 소비
            logger.info("consumer stopping (signal received) — draining in-flight={}", len(inflight))
            await _drain(consumers, inflight)
    finally:
        await close_pool()
