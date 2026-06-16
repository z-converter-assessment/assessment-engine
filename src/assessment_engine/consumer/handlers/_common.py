"""Consumer 핸들러 공통 helper — DB 재시도 / 멱등성 / 시계 invariant / agent 재시작 추적.

4 routing key 핸들러 (inventory · metrics · task_result · error) 가 본 모듈을 sibling import.
"""

import asyncio
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any
from uuid import UUID

from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.cache.redis import safe_get, safe_incr_with_ttl, safe_set, safe_set_nx
from assessment_engine.consumer.schemas import MessageBase
from assessment_engine.consumer.settings import consumer_settings
from assessment_engine.db.repositories.base_collect_repository import BaseCollectRepository

# 일시 장애(connection·deadlock)만 retry, 영구 장애(IntegrityError = UNIQUE/FK 위반)는 즉시 raise.
# IntegrityError 는 DBAPIError 를 상속하므로 _db_retry 에서 별도 먼저 캐치.
_RETRYABLE_DB_EXC = (OperationalError, DBAPIError)


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


async def _db_retry[T](
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], BaseCollectRepository],
    fn: Callable[[BaseCollectRepository], Coroutine[Any, Any, T]],
) -> T:
    for attempt in range(3):
        try:
            async with session_factory() as session:
                result = await fn(repo_factory(session))
                await session.commit()
            return result
        except IntegrityError as e:
            # 영구 장애 — 즉시 raise -> 핸들러 nack -> DLQ. F8: 진단 메타만 로깅.
            logger.error("db integrity error (non-retryable) {}", _format_db_err(e))
            raise
        except _RETRYABLE_DB_EXC as e:
            if attempt == 2:
                logger.error("db error after 3 attempts {}", _format_db_err(e))
                raise
            logger.warning("db error attempt={} {}", attempt + 1, _format_db_err(e))
            await asyncio.sleep(5 ** (attempt + 1))
    raise AssertionError("unreachable")


async def _check_idempotent(redis: Redis, message_id: UUID) -> bool:
    """SET NX 멱등성 체크. 첫 처리면 True, 중복이면 False.

    Redis 장애 시 fail-open (True 반환) — 처리 진행. DB UNIQUE 제약(2단)이 중복 INSERT를 흡수.
    CLAUDE.md #D2 (멱등성 2단 방어) 참조.
    """
    key = consumer_settings.redis_key_idempotent.format(message_id.hex)
    result = await safe_set_nx(redis, key, "1", consumer_settings.redis_ttl_idempotent)
    return True if result is None else result


async def _log_time_invariants(redis: Redis, data: MessageBase) -> None:
    """시계·systemd 시작 순서 invariant 검증. warning 로그만, 처리는 그대로 진행.

    - boot_time > agent_started_at: systemd 시작 순서 비정상 또는 시계 동기화 문제
    - agent_started_at > collected_at: VM 시계 동기화 문제 (VM resume 직후 흔함)
    DLQ 미전송 — 시계 문제는 reject 의미 없고 운영자 인지가 목적.

    F7: 같은 서버 지속 시 매 메시지 warning 방지 위해 1h 쿨다운. Redis 장애 시 fail-open (매번 출력).
    """
    if data.boot_time <= data.agent_started_at and data.agent_started_at <= data.collected_at:
        return
    cooldown_key = consumer_settings.redis_key_time_invariant_warned.format(
        data.composite_id,
        data.hostname,
    )
    set_result = await safe_set_nx(redis, cooldown_key, "1", consumer_settings.redis_ttl_time_invariant_warned)
    if set_result is False:
        return  # 쿨다운 윈도우 안
    if data.boot_time > data.agent_started_at:
        logger.warning(
            "time invariant violated boot_time>agent_started_at composite_id={} boot_time={} agent_started_at={}",
            data.composite_id,
            data.boot_time,
            data.agent_started_at,
        )
    if data.agent_started_at > data.collected_at:
        logger.warning(
            "time invariant violated agent_started_at>collected_at composite_id={} agent_started_at={} collected_at={}",
            data.composite_id,
            data.agent_started_at,
            data.collected_at,
        )


async def _track_agent_restart(redis: Redis, server_id: int, composite_id: str, agent_started_at: datetime) -> None:
    """직전 agent_started_at 과 비교 -> 변경 시 1h 슬라이딩 윈도우 카운터 INCR.

    threshold 도달 시 warning (agent crash loop 인지). 시스템 재부팅도 agent_started_at 변경이라
    같은 카운터 포함 (1h 내 3회면 그것도 alert 적정).

    fail-open — Redis 장애 시 silent skip (정확성 미보장).
    """
    last_key = consumer_settings.redis_key_last_agent_start.format(server_id)
    counter_key = consumer_settings.redis_key_agent_restarts.format(server_id)
    current_iso = agent_started_at.isoformat()

    last_iso = await safe_get(redis, last_key)
    if last_iso and last_iso != current_iso:
        count = await safe_incr_with_ttl(redis, counter_key, consumer_settings.redis_ttl_agent_restarts)
        if count is not None and count >= consumer_settings.agent_restart_alert_threshold:
            logger.warning(
                "agent restart frequency alert composite_id={} server_id={} count={}/h threshold={}",
                composite_id,
                server_id,
                count,
                consumer_settings.agent_restart_alert_threshold,
            )

    await safe_set(redis, last_key, current_iso, ex=consumer_settings.redis_ttl_last_agent_start)
