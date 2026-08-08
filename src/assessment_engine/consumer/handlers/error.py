"""Agent error 메시지 핸들러 — agent 가 발행한 error 메시지 (publish 실패·실행 오류 등) 로그."""

from typing import TYPE_CHECKING

from loguru import logger

from assessment_engine.consumer.handlers._common import (
    _check_idempotent,
    _in_message_context,
    _log_time_invariants,
    _sanitize_log_text,
)
from assessment_engine.consumer.schemas import ErrorInput

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from assessment_engine.consumer.handlers._types import MessageHandler

# error_message 는 wire 계약에 길이 상한이 없다 — 로그 지점에서 자른다.
# 스키마를 좁히면 유효 메시지가 DLQ 로 간다 (#B wire permissive).
_ERROR_MESSAGE_LOG_MAX = 500


def make_error_handler(redis: Redis) -> MessageHandler:
    """Agent 오류 메시지를 안전하게 로그로 기록하는 핸들러를 만든다."""

    async def _log(data: ErrorInput) -> None:
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

    return _in_message_context(ErrorInput, "error message", _log)
