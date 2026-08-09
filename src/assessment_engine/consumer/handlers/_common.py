"""Consumer 핸들러의 메시지 검증, DB 저장, Redis 후처리 공통 기능."""

import asyncio
import random
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, IntegrityError, InterfaceError, OperationalError

from assessment_engine.cache.redis import safe_get, safe_incr_with_ttl, safe_set, safe_set_nx
from assessment_engine.consumer.settings import get_consumer_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from datetime import datetime
    from uuid import UUID

    from aio_pika.abc import AbstractIncomingMessage
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from assessment_engine.consumer.handlers._types import MessageHandler
    from assessment_engine.consumer.schemas import AgentMessageBase, MessageBase
    from assessment_engine.db.repositories.collect import CollectRepository

# asyncpg dialect 의 예외 번역표에는 sqlalchemy OperationalError 로 가는 항목이 없다 — 커넥션 유실·서버
# 재기동·deadlock 은 base DBAPIError 로만 래핑돼 타입으로 안 갈린다. 그래서 SQLSTATE 를 함께 본다.
_RETRYABLE_DB_EXC = (OperationalError, InterfaceError)

_RETRYABLE_SQLSTATE_PREFIX = "08"
# 40001 serialization_failure · 40P01 deadlock_detected — victim rollback 후 재시도하면 경합이 풀린다.
# 57P01 admin_shutdown · 57P02 crash_shutdown · 57P03 cannot_connect_now — 서버 재기동 중.
_RETRYABLE_SQLSTATES = frozenset({"40001", "40P01", "57P01", "57P02", "57P03"})

# base 2 — 한 메시지가 재시도에 붙잡히는 시간을 최악 6s 로 묶어 prefetch 슬롯 점유를 짧게 유지한다.
_RETRY_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_BASE_SEC = 2


_VALIDATION_ERROR_LIMIT = 5

_VALIDATION_LOC_MAX = 48


def _format_db_error(e: DBAPIError) -> str:
    """SQL과 바인딩 값을 제외한 DB 오류 진단 문자열을 만든다."""
    sa_cls = type(e).__name__
    orig = getattr(e, "orig", None)
    if orig is None:
        return f"sa={sa_cls}"
    orig_cls = type(orig).__name__
    sqlstate = getattr(orig, "sqlstate", None)
    if sqlstate:
        return f"sa={sa_cls} orig={orig_cls} sqlstate={sqlstate}"
    return f"sa={sa_cls} orig={orig_cls}"


def _sanitize_log_text(value: str, limit: int) -> str:
    text = "".join(ch for ch in value if ch.isprintable())
    return text if len(text) <= limit else text[:limit] + "~"


def _sanitize_loc_part(part: object) -> str:
    return _sanitize_log_text(str(part), _VALIDATION_LOC_MAX)


def _format_validation_error(e: ValidationError, limit: int = _VALIDATION_ERROR_LIMIT) -> str:
    """입력값을 제외하고 검증 오류의 필드 경로와 종류만 로그 문자열로 만든다."""
    errors = e.errors(include_url=False, include_context=False, include_input=False)
    head = " ".join(".".join(_sanitize_loc_part(p) for p in it["loc"]) + "=" + it["type"] for it in errors[:limit])
    more = f" +{len(errors) - limit}more" if len(errors) > limit else ""
    return f"count={len(errors)} {head}{more}"


def _hide_bound_params(e: DBAPIError) -> None:
    """예외 문자열에서 SQL 바인딩 값을 숨겨 메시지 데이터 노출을 막는다."""
    e.hide_parameters = True


def _is_retryable_db_failure(e: DBAPIError) -> bool:
    if isinstance(e, _RETRYABLE_DB_EXC):
        return True
    sqlstate = getattr(getattr(e, "orig", None), "sqlstate", None)
    if not isinstance(sqlstate, str):
        return False
    return sqlstate.startswith(_RETRYABLE_SQLSTATE_PREFIX) or sqlstate in _RETRYABLE_SQLSTATES


def _describe_db_failure(e: DBAPIError | TimeoutError) -> tuple[str, str]:
    """재시도 로그에 쓸 오류 종류와 안전한 진단 메타를 반환한다."""
    if isinstance(e, DBAPIError):
        return "db error", f" {_format_db_error(e)}"
    return "db timeout", ""


def _is_permanent_db_failure(e: DBAPIError | TimeoutError) -> bool:
    """재시도해도 해결되지 않는 DB 오류면 로그를 남기고 True를 반환한다.

    TimeoutError는 드라이버가 SQLAlchemy 예외로 감싸지지 않는 일시 장애이므로 False다.
    """
    if not isinstance(e, DBAPIError):
        return False
    _hide_bound_params(e)
    # IntegrityError(UNIQUE·FK 위반)도 DBAPIError 하위라 같은 except 로 들어온다 — 재시도 판정 전에 갈라낸다.
    if isinstance(e, IntegrityError):
        logger.error("db integrity error (non-retryable) {}", _format_db_error(e))
        return True
    if not _is_retryable_db_failure(e):
        # ProgrammingError·DataError 등 — 재시도해도 같은 결과다.
        logger.error("db error (non-retryable) {}", _format_db_error(e))
        return True
    return False


async def _db_attempt[T](
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], CollectRepository],
    fn: Callable[[CollectRepository], Coroutine[Any, Any, T]],
) -> T:
    """새 세션에서 작업을 실행하고 성공하면 커밋한 뒤 작업 결과를 반환한다."""
    async with session_factory() as session:
        result = await fn(repo_factory(session))
        await session.commit()
        return result


async def _db_retry[T](
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], CollectRepository],
    fn: Callable[[CollectRepository], Coroutine[Any, Any, T]],
    sleep: Callable[[float], Coroutine[Any, Any, None]] = asyncio.sleep,
) -> T:
    """DB 작업을 일시 장애에만 백오프 재시도하고 원래 작업 결과를 반환한다.

    영구 장애와 재시도 소진 오류는 호출자에게 다시 발생시킨다.
    """
    for attempt in range(1, _RETRY_MAX_ATTEMPTS):
        try:
            return await _db_attempt(session_factory, repo_factory, fn)
        except (DBAPIError, TimeoutError) as e:
            if _is_permanent_db_failure(e):
                raise
            label, meta = _describe_db_failure(e)
            logger.warning("{} attempt={}{}", label, attempt, meta)

        await sleep(random.uniform(0, _RETRY_BACKOFF_BASE_SEC**attempt))  # noqa: S311

    try:
        return await _db_attempt(session_factory, repo_factory, fn)
    except (DBAPIError, TimeoutError) as e:
        if not _is_permanent_db_failure(e):
            label, meta = _describe_db_failure(e)
            logger.error("{} after {} attempts{}", label, _RETRY_MAX_ATTEMPTS, meta)
        raise


def _parse[T: MessageBase](model: type[T], label: str, body: bytes) -> T:
    """메시지 본문을 모델로 검증하고, 실패 시 입력값 없는 ValueError로 변환한다."""
    try:
        return model.model_validate_json(body)
    except ValidationError as e:
        detail = _format_validation_error(e)
        logger.error("{} parse error {}", label, detail)
        raise ValueError(f"{label} validation failed: {detail}") from None


def _in_message_context[T: MessageBase](
    model: type[T],
    label: str,
    handle: Callable[[T], Coroutine[Any, Any, None]],
) -> MessageHandler:
    """본문 검증과 RabbitMQ ack/nack 처리를 적용한 메시지 핸들러를 만든다."""

    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            await handle(_parse(model, label, message.body))

    return _handle


async def _check_idempotent(redis: Redis, message_id: UUID) -> bool:
    """첫 메시지면 True, 중복이면 False를 반환한다. Redis 장애에서는 True를 반환한다."""
    key = get_consumer_settings().redis_key_idempotent.format(message_id.hex)
    result = await safe_set_nx(redis, key, "1", get_consumer_settings().redis_ttl_idempotent)
    return True if result is None else result


async def _time_warn_allowed(redis: Redis, agent_id: UUID) -> bool:
    """agent의 시간 이상 경고를 지금 출력해도 되면 True를 반환한다."""
    key = get_consumer_settings().redis_key_time_invariant_warned.format(agent_id)
    result = await safe_set_nx(redis, key, "1", get_consumer_settings().redis_ttl_time_invariant_warned)
    return result is not False


async def _log_time_invariants(redis: Redis, data: AgentMessageBase) -> None:
    """시간 순서 이상을 agent별 빈도 제한 경고로 기록하고 메시지 처리는 계속한다."""
    if data.agent_started_at is None:
        return
    boot_ok = data.boot_time is None or data.boot_time <= data.agent_started_at
    if boot_ok and data.agent_started_at <= data.collected_at:
        return
    if not await _time_warn_allowed(redis, data.agent_id):
        return
    if data.boot_time is not None and data.boot_time > data.agent_started_at:
        logger.warning(
            "time invariant violated boot_time>agent_started_at agent_id={} boot_time={} agent_started_at={}",
            data.agent_id,
            data.boot_time,
            data.agent_started_at,
        )
    if data.agent_started_at > data.collected_at:
        logger.warning(
            "time invariant violated agent_started_at>collected_at agent_id={} agent_started_at={} collected_at={}",
            data.agent_id,
            data.agent_started_at,
            data.collected_at,
        )


async def _track_agent_restart(redis: Redis, server_id: int, agent_id: str, agent_started_at: datetime | None) -> None:
    """agent 시작 시각 변화를 Redis로 추적하고 임계값 이상의 재시작을 경고한다."""
    if agent_started_at is None:
        return
    last_key = get_consumer_settings().redis_key_last_agent_start.format(server_id)
    counter_key = get_consumer_settings().redis_key_agent_restarts.format(server_id)
    current_iso = agent_started_at.isoformat()

    last_iso = await safe_get(redis, last_key)
    if last_iso and last_iso != current_iso:
        count = await safe_incr_with_ttl(redis, counter_key, get_consumer_settings().redis_ttl_agent_restarts)
        if count is not None and count >= get_consumer_settings().agent_restart_alert_threshold:
            logger.warning(
                "agent restart frequency alert agent_id={} server_id={} "
                "consecutive_count={} threshold={} max_gap_sec={}",
                agent_id,
                server_id,
                count,
                get_consumer_settings().agent_restart_alert_threshold,
                get_consumer_settings().redis_ttl_agent_restarts,
            )

    await safe_set(redis, last_key, current_iso, ex=get_consumer_settings().redis_ttl_last_agent_start)
