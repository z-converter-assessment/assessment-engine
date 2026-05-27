"""Agent error 메시지 핸들러 — agent 가 발행한 error 메시지 (publish 실패·실행 오류 등) 로그."""

from collections.abc import Callable, Coroutine
from typing import Any

from aio_pika.abc import AbstractIncomingMessage
from loguru import logger
from pydantic import ValidationError
from redis.asyncio import Redis

from assessment_engine.consumer.handlers._common import _check_idempotent, _log_time_invariants
from assessment_engine.consumer.schemas import ErrorInput


def make_error_handler(
    redis: Redis,
) -> Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]:
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = ErrorInput.model_validate_json(message.body)
            except ValidationError as e:
                logger.error("error message parse error count={}", len(e.errors()))
                raise

            if not await _check_idempotent(redis, data.message_id):
                logger.info("error duplicate skipped message_id={}", data.message_id)
                return

            await _log_time_invariants(redis, data)

            logger.warning(
                "agent error composite_id={} component={} code={} msg={} "
                "retry_count={} first_failed_at={} recovered_at={}",
                data.composite_id,
                data.failed_component,
                data.error_code,
                data.error_message,
                data.retry_count,
                data.first_failed_at,
                data.recovered_at,
            )

    return _handle
