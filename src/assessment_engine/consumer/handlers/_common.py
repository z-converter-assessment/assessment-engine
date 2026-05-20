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

# retry 가치가 있는 예외(connection lost, deadlock 등)와 영구 장애(스키마 위반·UNIQUE 등)를 분리.
# - OperationalError: connection·timeout·deadlock 등 일시 장애.
# - DBAPIError: asyncpg 드라이버 일시 오류. 단 IntegrityError는 DBAPIError를 상속하므로 별도 캐치.
# - IntegrityError: UNIQUE/FK 위반 — retry 무의미 (단 record_metrics는 ON CONFLICT DO NOTHING이라 도달 거의 없음).
_RETRYABLE_DB_EXC = (OperationalError, DBAPIError)


def _format_db_err(e: DBAPIError) -> str:
    """DB 예외에서 SQL·param·connection string 제외한 진단 메타만 추출 (F8).

    - SQLAlchemy 클래스명 (OperationalError·IntegrityError 등)
    - asyncpg origin 클래스 (`UniqueViolationError`·`ConnectionDoesNotExistError` 등) — `e.orig`
    - PostgreSQL SQLSTATE 5자 코드 — asyncpg `e.orig.sqlstate`
    """
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
            # 영구 장애 — retry 의미 없음. 즉시 raise → 핸들러가 nack → DLQ.
            # F8: e.orig 메시지엔 SQL·param·테이블 컬럼 노출 가능 — 진단용 메타만 로깅.
            logger.error("db integrity error (non-retryable) {}", _format_db_err(e))
            raise
        except _RETRYABLE_DB_EXC as e:
            # F8: connection string·param 노출 가능 — 진단용 메타만 로깅.
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

    - boot_time > agent_started_at: systemd 시작 순서 비정상 또는 시계 동기화 문제 (드뭄)
    - agent_started_at > collected_at: VM 시계 동기화 문제 (가장 흔함 — VM resume 직후)
    DLQ로 보내지 않음 — 시계 문제는 데이터 reject 의미 없고 운영자 인지가 목적.

    F7: 같은 서버 시계 문제 지속 시 매 메시지 warning → 1h 쿨다운 (Redis 키)으로 스팸 방지.
    Redis 장애 시 fail-open — 쿨다운 없이 매번 출력 (장애 자체가 시그널).
    """
    if data.boot_time <= data.agent_started_at and data.agent_started_at <= data.collected_at:
        return  # invariant 정상 — 즉시 종료
    cooldown_key = consumer_settings.redis_key_time_invariant_warned.format(
        data.machine_id,
        data.hostname,
    )
    set_result = await safe_set_nx(redis, cooldown_key, "1", consumer_settings.redis_ttl_time_invariant_warned)
    if set_result is False:
        return  # 쿨다운 윈도우 안 — silent skip
    if data.boot_time > data.agent_started_at:
        logger.warning(
            "time invariant violated boot_time>agent_started_at machine_id={} boot_time={} agent_started_at={}",
            data.machine_id,
            data.boot_time,
            data.agent_started_at,
        )
    if data.agent_started_at > data.collected_at:
        logger.warning(
            "time invariant violated agent_started_at>collected_at machine_id={} agent_started_at={} collected_at={}",
            data.machine_id,
            data.agent_started_at,
            data.collected_at,
        )


async def _track_agent_restart(redis: Redis, server_id: int, machine_id: str, agent_started_at: datetime) -> None:
    """직전 agent_started_at과 비교 → 변경 시 1h 슬라이딩 윈도우 카운터 INCR.

    threshold 도달 시 warning 로그 (운영자가 "에이전트 crash loop"으로 인지). 시스템 재부팅도
    agent_started_at이 자연히 변경되므로 같은 카운터에 포함 — 시스템 재부팅이 1h 내 3회면
    그것도 unusual이라 alert 적정.

    fail-open — Redis 장애 시 silent skip. 정확성 보장 안 됨 (옛 휴리스틱과 동일).
    """
    last_key = consumer_settings.redis_key_last_agent_start.format(server_id)
    counter_key = consumer_settings.redis_key_agent_restarts.format(server_id)
    current_iso = agent_started_at.isoformat()

    last_iso = await safe_get(redis, last_key)
    if last_iso and last_iso != current_iso:
        count = await safe_incr_with_ttl(redis, counter_key, consumer_settings.redis_ttl_agent_restarts)
        if count is not None and count >= consumer_settings.agent_restart_alert_threshold:
            logger.warning(
                "agent restart frequency alert machine_id={} server_id={} count={}/h threshold={}",
                machine_id,
                server_id,
                count,
                consumer_settings.agent_restart_alert_threshold,
            )

    await safe_set(redis, last_key, current_iso, ex=consumer_settings.redis_ttl_last_agent_start)
