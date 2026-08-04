"""Consumer 핸들러 공통 helper — DB 재시도 / 멱등성 / 시계 invariant / agent 재시작 추적.

4 routing key 핸들러 (inventory · metrics · task_result · error) 가 본 모듈을 sibling import.
"""

import asyncio
import random
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any
from uuid import UUID

from loguru import logger
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.exc import DBAPIError, IntegrityError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.cache.redis import safe_get, safe_incr_with_ttl, safe_set, safe_set_nx
from assessment_engine.consumer.schemas import AgentMessageBase
from assessment_engine.consumer.settings import get_consumer_settings
from assessment_engine.db.repositories.base_collect_repository import BaseCollectRepository

# 일시 장애만 retry. 영구 장애(IntegrityError·ProgrammingError·DataError 등)는 즉시 raise -> nack -> DLQ (F6).
# IntegrityError(= UNIQUE/FK 위반)도 DBAPIError 상속이라 먼저 별도 캐치.
# asyncpg dialect 의 예외 번역표에는 sqlalchemy OperationalError 로 가는 항목이 없다 — 커넥션 유실·서버
# 재기동·deadlock 은 base DBAPIError 로만 래핑돼 타입으로 안 갈린다. 그래서 SQLSTATE 를 함께 본다.
_RETRYABLE_DB_EXC = (OperationalError, InterfaceError)
# class 08 = connection exception (커넥션 유실 — 전 코드 일시).
_RETRYABLE_SQLSTATE_PREFIX = "08"
# 40001 serialization_failure · 40P01 deadlock_detected — victim rollback 후 재시도하면 경합이 풀린다.
# 57P01 admin_shutdown · 57P02 crash_shutdown · 57P03 cannot_connect_now — 서버 재기동 중.
_RETRYABLE_SQLSTATES = frozenset({"40001", "40P01", "57P01", "57P02", "57P03"})

# exponential backoff + full jitter — thundering herd 방지 + 메시지 처리 블로킹 최소화.
# base 2 — 한 메시지가 재시도에 붙잡히는 시간을 최악 6s 로 묶어 prefetch 슬롯 점유를 짧게 유지한다.
_RETRY_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_BASE_SEC = 2

# 검증 오류 로그에 남길 최대 필드 수 — inventory 는 한 메시지에 수십 건이 나올 수 있다 (F7).
_VALIDATION_ERR_LIMIT = 5
# 경로 한 조각의 길이 상한 — 에이전트가 정한 dict 키가 그대로 실린다.
_VALIDATION_LOC_MAX = 48


def _format_db_err(e: DBAPIError) -> str:
    """DB 예외에서 SQL·param·connection string 제외한 진단 메타만 추출 (F8)."""
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
    """에이전트가 정한 문자열을 로그 안전하게. 인쇄 가능 문자만 남기고 길이를 자른다.

    개행이 섞이면 로그 줄이 위조되고, 길이 상한이 없는 필드는 레코드 하나를 임의 크기로 부풀린다.
    """
    text = "".join(ch for ch in value if ch.isprintable())
    return text if len(text) <= limit else text[:limit] + "~"


def _sanitize_loc_part(part: object) -> str:
    """검증 오류 경로 한 조각 — metric 명·device id 처럼 에이전트가 정한 dict 키가 실린다."""
    return _sanitize_log_text(str(part), _VALIDATION_LOC_MAX)


def _format_validation_err(e: ValidationError, limit: int = _VALIDATION_ERR_LIMIT) -> str:
    """검증 오류에서 필드 경로와 오류 종류만 추출 (F8).

    `msg` 는 입력값 조각을 싣는 경우가 있어(uuid_parsing 은 실패 문자를 노출) 제외한다. 필드가 많은
    inventory 는 한 메시지에 오류가 수십 건 나올 수 있어 상위 limit 건만 남긴다 (F7).
    """
    errors = e.errors(include_url=False, include_context=False, include_input=False)
    head = " ".join(
        ".".join(_sanitize_loc_part(p) for p in it["loc"]) + "=" + it["type"] for it in errors[:limit]
    )
    more = f" +{len(errors) - limit}more" if len(errors) > limit else ""
    return f"count={len(errors)} {head}{more}"


def _hide_bound_params(e: DBAPIError) -> None:
    """핸들러 밖으로 나가는 DB 예외에서 bound parameter 를 지운다 (F8).

    나간 예외는 asyncio 가 traceback 전문을 출력하는데, DBAPIError 문자열은 INSERT 에 실린
    hostname·IP·interfaces 를 `[parameters: ...]` 로 그대로 담는다.
    """
    e.hide_parameters = True


def _is_retryable_db_exc(e: DBAPIError) -> bool:
    """일시 DB 장애 판정 — 예외 타입 또는 SQLSTATE 둘 중 하나라도 걸리면 재시도."""
    if isinstance(e, _RETRYABLE_DB_EXC):
        return True
    sqlstate = getattr(getattr(e, "orig", None), "sqlstate", None)
    if not isinstance(sqlstate, str):
        return False
    return sqlstate.startswith(_RETRYABLE_SQLSTATE_PREFIX) or sqlstate in _RETRYABLE_SQLSTATES


async def _db_retry[T](
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], BaseCollectRepository],
    fn: Callable[[BaseCollectRepository], Coroutine[Any, Any, T]],
) -> T:
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        try:
            async with session_factory() as session:
                result = await fn(repo_factory(session))
                await session.commit()
            return result
        except IntegrityError as e:
            # 영구 장애 — 즉시 raise -> 핸들러 nack -> DLQ. F8: 진단 메타만 로깅.
            _hide_bound_params(e)
            logger.error("db integrity error (non-retryable) {}", _format_db_err(e))
            raise
        except DBAPIError as e:
            _hide_bound_params(e)
            if not _is_retryable_db_exc(e):
                # 영구 장애 (ProgrammingError·DataError 등) — 즉시 raise -> nack -> DLQ (F6).
                logger.error("db error (non-retryable) {}", _format_db_err(e))
                raise
            if attempt == _RETRY_MAX_ATTEMPTS - 1:
                logger.error("db error after {} attempts {}", _RETRY_MAX_ATTEMPTS, _format_db_err(e))
                raise
            logger.warning("db error attempt={} {}", attempt + 1, _format_db_err(e))
        except TimeoutError:
            # asyncpg 의 connect/command timeout 은 asyncio.TimeoutError 로 나오고 dialect 예외 번역표에
            # 없어 DBAPIError 로 감싸이지 않는다 — 위 분기가 못 잡으므로 여기서 일시 장애로 받는다.
            if attempt == _RETRY_MAX_ATTEMPTS - 1:
                logger.error("db timeout after {} attempts", _RETRY_MAX_ATTEMPTS)
                raise
            logger.warning("db timeout attempt={}", attempt + 1)
        # full jitter: [0, base^(attempt+1)] 균등 — 동시 재연결 쏠림 방지.
        await asyncio.sleep(random.uniform(0, _RETRY_BACKOFF_BASE_SEC ** (attempt + 1)))
    raise AssertionError("unreachable")


async def _check_idempotent(redis: Redis, message_id: UUID) -> bool:
    """SET NX 멱등성 체크. 첫 처리면 True, 중복이면 False.

    Redis 장애 시 fail-open (True 반환) — 처리 진행. DB UNIQUE 제약(2단)이 중복 INSERT를 흡수.
    CLAUDE.md #D2 (멱등성 2단 방어) 참조.
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
    """시계·systemd 시작 순서 invariant 검증. warning 로그만, 처리는 그대로 진행.

    - boot_time > agent_started_at: systemd 시작 순서 비정상 또는 시계 동기화 문제
    - agent_started_at > collected_at: VM 시계 동기화 문제 (VM resume 직후 흔함)
    DLQ 미전송 — 시계 문제는 reject 의미 없고 운영자 인지가 목적.

    F7: 같은 서버 지속 시 매 메시지 warning 방지 위해 1h 쿨다운. Redis 장애 시 fail-open (매번 출력).
    boot_time·agent_started_at 은 판독 불가 시 null (계약 값 의미론) — null 축은 해당 순서 검증을 건너뛴다.
    """
    if data.agent_started_at is None:
        return  # 발행 기동시각 미상 — 순서 검증 불가 (task.result 등)
    boot_ok = data.boot_time is None or data.boot_time <= data.agent_started_at
    if boot_ok and data.agent_started_at <= data.collected_at:
        return
    if not await _time_warn_allowed(redis, data.agent_id):
        return  # 쿨다운 윈도우 안
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


async def _track_agent_restart(
    redis: Redis, server_id: int, agent_id: str, agent_started_at: datetime | None
) -> None:
    """직전 agent_started_at 과 비교 -> 변경 시 1h 슬라이딩 윈도우 카운터 INCR.

    threshold 도달 시 warning (agent crash loop 인지). 시스템 재부팅도 agent_started_at 변경이라
    같은 카운터 포함 (1h 내 3회면 그것도 alert 적정).

    agent_started_at None(발행 기동시각 미상)이면 skip. fail-open — Redis 장애 시 silent skip.
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
                "agent restart frequency alert agent_id={} server_id={} count={}/h threshold={}",
                agent_id,
                server_id,
                count,
                get_consumer_settings().agent_restart_alert_threshold,
            )

    await safe_set(redis, last_key, current_iso, ex=get_consumer_settings().redis_ttl_last_agent_start)
