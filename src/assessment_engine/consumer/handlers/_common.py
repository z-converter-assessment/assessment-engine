"""Consumer 핸들러 공통 helper — DB 재시도 / 멱등성 / 시계 invariant / agent 재시작 추적."""

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
# class 08 = connection exception — 하위 코드가 전부 일시(커넥션 유실)라 prefix 로 묶는다.
_RETRYABLE_SQLSTATE_PREFIX = "08"
# 40001 serialization_failure · 40P01 deadlock_detected — victim rollback 후 재시도하면 경합이 풀린다.
# 57P01 admin_shutdown · 57P02 crash_shutdown · 57P03 cannot_connect_now — 서버 재기동 중.
_RETRYABLE_SQLSTATES = frozenset({"40001", "40P01", "57P01", "57P02", "57P03"})

# base 2 — 한 메시지가 재시도에 붙잡히는 시간을 최악 6s 로 묶어 prefetch 슬롯 점유를 짧게 유지한다.
_RETRY_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_BASE_SEC = 2

# 검증 오류 로그에 남길 최대 필드 수 — inventory 는 한 메시지에 수십 건이 나올 수 있다 (F7).
_VALIDATION_ERROR_LIMIT = 5
# 경로 한 조각의 길이 상한 — 에이전트가 정한 dict 키가 그대로 실린다.
_VALIDATION_LOC_MAX = 48


def _format_db_error(e: DBAPIError) -> str:
    """DB 예외에서 SQL·param·connection string 을 뺀 진단 메타만 추출 (F8)."""
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
    """에이전트가 정한 문자열을 로그에 안전하게 싣는다.

    개행이 섞이면 로그 줄이 위조되고, 길이 상한이 없는 필드는 레코드 하나를 임의 크기로 부풀린다.
    """
    text = "".join(ch for ch in value if ch.isprintable())
    return text if len(text) <= limit else text[:limit] + "~"


def _sanitize_loc_part(part: object) -> str:
    return _sanitize_log_text(str(part), _VALIDATION_LOC_MAX)


def _format_validation_error(e: ValidationError, limit: int = _VALIDATION_ERROR_LIMIT) -> str:
    """검증 오류에서 필드 경로와 오류 종류만 추출 (F8).

    `msg` 는 입력값 조각을 싣는 경우가 있어(uuid_parsing 은 실패 문자를 노출) 제외한다.
    """
    errors = e.errors(include_url=False, include_context=False, include_input=False)
    head = " ".join(".".join(_sanitize_loc_part(p) for p in it["loc"]) + "=" + it["type"] for it in errors[:limit])
    more = f" +{len(errors) - limit}more" if len(errors) > limit else ""
    return f"count={len(errors)} {head}{more}"


def _hide_bound_params(e: DBAPIError) -> None:
    """핸들러 밖으로 나가는 DB 예외에서 bound parameter 를 지운다 (F8).

    나간 예외는 asyncio 가 traceback 전문을 출력하는데, DBAPIError 문자열은 INSERT 에 실린
    hostname·IP·interfaces 를 `[parameters: ...]` 로 그대로 담는다.
    """
    e.hide_parameters = True


def _is_retryable_db_failure(e: DBAPIError) -> bool:
    if isinstance(e, _RETRYABLE_DB_EXC):
        return True
    sqlstate = getattr(getattr(e, "orig", None), "sqlstate", None)
    if not isinstance(sqlstate, str):
        return False
    return sqlstate.startswith(_RETRYABLE_SQLSTATE_PREFIX) or sqlstate in _RETRYABLE_SQLSTATES


def _describe_db_failure(e: DBAPIError | TimeoutError) -> tuple[str, str]:
    """(무엇이 실패했나, 진단 메타) — 재시도 로그 두 자리가 같은 문구를 쓰게 한다."""
    if isinstance(e, DBAPIError):
        return "db error", f" {_format_db_error(e)}"
    return "db timeout", ""


def _is_permanent_db_failure(e: DBAPIError | TimeoutError) -> bool:
    """영구 장애면 진단 메타를 남기고 True — 호출자가 그대로 raise 해 nack -> DLQ (F6).

    asyncpg 의 connect/command timeout 은 dialect 예외 번역표에 없어 DBAPIError 로 감싸이지 않는다.
    타입으로 안 갈리므로 `TimeoutError` 는 항상 일시 장애로 받는다.
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
    """세션 1회 — 이 함수 범위가 곧 트랜잭션 경계다."""
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
    """일시 DB 장애만 백오프 재시도. 영구 장애는 즉시 raise -> nack -> DLQ (F6).

    `sleep` 은 백오프 대기를 주입받는 자리다. 기본값이 실제 경로이고, 테스트가 여기를 지르지 않으면
    한 케이스에 최대 6초가 붙는다.
    """
    for attempt in range(1, _RETRY_MAX_ATTEMPTS):
        try:
            return await _db_attempt(session_factory, repo_factory, fn)
        except (DBAPIError, TimeoutError) as e:
            if _is_permanent_db_failure(e):
                raise
            label, meta = _describe_db_failure(e)
            logger.warning("{} attempt={}{}", label, attempt, meta)
        # full jitter: [0, base^attempt] 균등 — 동시 재연결 쏠림 방지.
        await sleep(random.uniform(0, _RETRY_BACKOFF_BASE_SEC**attempt))  # noqa: S311

    # 마지막 시도를 루프 밖에 둔다 — 루프가 소진될 수 없음을 타입 검사기에 증명하지 않아도 되고,
    # 도달 불가 분기가 사라진다.
    try:
        return await _db_attempt(session_factory, repo_factory, fn)
    except (DBAPIError, TimeoutError) as e:
        if not _is_permanent_db_failure(e):
            label, meta = _describe_db_failure(e)
            logger.error("{} after {} attempts{}", label, _RETRY_MAX_ATTEMPTS, meta)
        raise


def _parse[T: MessageBase](model: type[T], label: str, body: bytes) -> T:
    """wire JSON -> 인바운드 모델. 실패는 nack 에 필요한 만큼만 담은 예외로 바꿔 던진다.

    핸들러 밖으로 빠져나간 예외는 asyncio 가 전문을 출력하는데, 원본 `ValidationError` 문자열은
    실패 필드의 입력값을 그대로 싣는다 (#F8).
    """
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
    """파싱과 처리를 `message.process` 안에 넣어 ack/nack 경계를 한 곳에서 만든다.

    컨텍스트 안에서 모든 await 를 마치는 것이 #F11 의 요구다.
    """

    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            await handle(_parse(model, label, message.body))

    return _handle


async def _check_idempotent(redis: Redis, message_id: UUID) -> bool:
    """SET NX 멱등성 체크 — 첫 처리면 True, 중복이면 False.

    Redis 장애 시 fail-open(True) — DB UNIQUE 가 중복 INSERT 를 흡수한다 (#D2 2단 방어).
    """
    key = get_consumer_settings().redis_key_idempotent.format(message_id.hex)
    result = await safe_set_nx(redis, key, "1", get_consumer_settings().redis_ttl_idempotent)
    return True if result is None else result


async def _time_warn_allowed(redis: Redis, agent_id: UUID) -> bool:
    """쿨다운 창이 비어 있으면 True. Redis 장애 시 fail-open (매번 출력) — F7 빈도 제어."""
    key = get_consumer_settings().redis_key_time_invariant_warned.format(agent_id)
    result = await safe_set_nx(redis, key, "1", get_consumer_settings().redis_ttl_time_invariant_warned)
    return result is not False


async def _log_time_invariants(redis: Redis, data: AgentMessageBase) -> None:
    """시계·systemd 시작 순서 invariant 검증 — warning 로그만 남기고 처리는 그대로 진행한다.

    위반은 systemd 시작 순서 이상이거나 VM 시계 동기화 문제(resume 직후 흔함)라 reject 할 대상이
    아니다. 같은 서버가 매 메시지 warning 을 내지 않게 agent 별 쿨다운을 둔다 (#F7).
    boot_time·agent_started_at 은 판독 불가 시 null 이라 그 축의 순서 검증을 건너뛴다.
    """
    if data.agent_started_at is None:
        return  # 발행 기동시각 미상(task.result 등) — 순서 검증 불가
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
    """짧은 간격으로 이어지는 에이전트 재시작을 감지한다.

    시스템 재부팅도 수집 연속성에 영향을 주므로 같은 카운터에 포함한다. 시각이 없거나 Redis가
    응답하지 않으면 메시지 처리를 계속한다.
    """
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
