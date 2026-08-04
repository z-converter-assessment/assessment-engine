"""Agent error 메시지 핸들러 — agent 가 발행한 error 메시지 (publish 실패·실행 오류 등) 로그."""

from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import ValidationError

from assessment_engine.consumer.handlers._common import (
    _check_idempotent,
    _format_validation_err,
    _log_time_invariants,
    _sanitize_log_text,
)
from assessment_engine.consumer.schemas import ErrorInput

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from aio_pika.abc import AbstractIncomingMessage
    from redis.asyncio import Redis

# error_message 는 wire 계약에 길이 상한이 없다 — 로그 지점에서 자른다.
# 스키마를 좁히면 유효 메시지가 DLQ 로 간다 (#B wire permissive).
_ERROR_MESSAGE_LOG_MAX = 500


def make_error_handler(
    redis: Redis,
) -> Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]:
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = ErrorInput.model_validate_json(message.body)
            except ValidationError as e:
                detail = _format_validation_err(e)
                logger.error("error message parse error {}", detail)
                # 핸들러 밖으로 빠져나간 예외는 asyncio 가 전문을 출력한다 — 실패 필드의 입력값을
                # 문자열에 싣는 원본 대신, nack 에 필요한 만큼만 담은 예외로 바꿔 던진다.
                raise ValueError(f"error message validation failed: {detail}") from None

            if not await _check_idempotent(redis, data.message_id):
                logger.info("error duplicate skipped message_id={}", data.message_id)
                return

            await _log_time_invariants(redis, data)

            error_message = _sanitize_log_text(data.error_message, _ERROR_MESSAGE_LOG_MAX) or "(empty)"

            logger.warning(
                "agent error agent_id={} component={} code={} msg={} retry_count={} first_failed_at={} recovered_at={}",
                data.agent_id,
                data.failed_component,
                data.error_code,
                error_message,
                data.retry_count,
                data.first_failed_at,
                data.recovered_at,
            )

    return _handle
