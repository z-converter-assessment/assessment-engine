import asyncio
import json
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any
from uuid import UUID

from aio_pika.abc import AbstractIncomingMessage
from loguru import logger
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.consumer.mappers import placeholder_inventory_from_metrics, to_inventory_create, to_metric_create
from assessment_engine.consumer.schemas import ErrorInput, InventoryInput, MessageBase, MetricsInput, TaskResultInput
from assessment_engine.consumer.settings import consumer_settings
from assessment_engine.db.redis import (
    safe_delete,
    safe_get,
    safe_incr_with_ttl,
    safe_publish,
    safe_set,
    safe_set_nx,
)
from assessment_engine.db.repositories.base_collect_repository import BaseCollectRepository, MetricInsertResult
from assessment_engine.db.repositories.inbound import TaskResultUpdate

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
        data.machine_id, data.hostname,
    )
    set_result = await safe_set_nx(redis, cooldown_key, "1", consumer_settings.redis_ttl_time_invariant_warned)
    if set_result is False:
        return  # 쿨다운 윈도우 안 — silent skip
    if data.boot_time > data.agent_started_at:
        logger.warning(
            "time invariant violated boot_time>agent_started_at machine_id={} boot_time={} agent_started_at={}",
            data.machine_id, data.boot_time, data.agent_started_at,
        )
    if data.agent_started_at > data.collected_at:
        logger.warning(
            "time invariant violated agent_started_at>collected_at machine_id={} agent_started_at={} collected_at={}",
            data.machine_id, data.agent_started_at, data.collected_at,
        )


async def _track_agent_restart(redis: Redis, server_id: int, machine_id: str, agent_started_at: datetime) -> None:
    """직전 agent_started_at과 비교 → 변경 시 1h 슬라이딩 윈도우 카운터 INCR.

    threshold 도달 시 warning 로그 (운영자가 "에이전트 crash loop"으로 인지). 시스템 재부팅도
    agent_started_at이 자연히 변경되므로 같은 카운터에 포함 — 시스템 재부팅이 1h 내 3회면
    그것도 unusual이라 alert 적정.

    fail-open — Redis 장애 시 silent skip. 정확성 보장 안 됨 (옛 휴리스틱과 동일).
    """
    last_key    = consumer_settings.redis_key_last_agent_start.format(server_id)
    counter_key = consumer_settings.redis_key_agent_restarts.format(server_id)
    current_iso = agent_started_at.isoformat()

    last_iso = await safe_get(redis, last_key)
    if last_iso and last_iso != current_iso:
        count = await safe_incr_with_ttl(redis, counter_key, consumer_settings.redis_ttl_agent_restarts)
        if count is not None and count >= consumer_settings.agent_restart_alert_threshold:
            logger.warning(
                "agent restart frequency alert machine_id={} server_id={} count={}/h threshold={}",
                machine_id, server_id, count, consumer_settings.agent_restart_alert_threshold,
            )

    await safe_set(redis, last_key, current_iso, ex=consumer_settings.redis_ttl_last_agent_start)


def make_inventory_handler(
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], BaseCollectRepository],
    redis: Redis,
) -> Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]:
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = InventoryInput.model_validate_json(message.body)
            except ValidationError as e:
                logger.error("inventory parse error count={}", len(e.errors()))
                raise

            if not await _check_idempotent(redis, data.message_id):
                logger.info("inventory duplicate skipped message_id={}", data.message_id)
                return

            await _log_time_invariants(redis, data)

            dto = to_inventory_create(data)

            async def upsert(repo: BaseCollectRepository) -> int:
                return await repo.upsert_server(dto)

            resolved_server_id = await _db_retry(session_factory, repo_factory, upsert)

            online_key    = consumer_settings.redis_key_online.format(resolved_server_id)
            inventory_key = consumer_settings.redis_key_cache_inventory.format(resolved_server_id)
            await safe_set(redis, online_key, "1", ex=consumer_settings.redis_ttl_online)
            # 인벤토리 변경(서비스/포트/디스크 등) 즉시 반영 — TTL 만료 대기 제거
            await safe_delete(redis, inventory_key)

            logger.info("inventory stored machine_id={}", data.machine_id)

    return _handle


def make_metrics_handler(
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], BaseCollectRepository],
    redis: Redis,
) -> Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]:
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = MetricsInput.model_validate_json(message.body)
            except ValidationError as e:
                logger.error("metrics parse error count={}", len(e.errors()))
                raise

            if not await _check_idempotent(redis, data.message_id):
                logger.info("metrics duplicate skipped message_id={}", data.message_id)
                return

            await _log_time_invariants(redis, data)

            dto = to_metric_create(data)
            placeholder = placeholder_inventory_from_metrics(data)

            async def save(repo: BaseCollectRepository) -> tuple[int, bool, MetricInsertResult]:
                # find→upsert 흐름은 repo.ensure_server_id로 캡슐화. find 성공 시 placeholder 미사용.
                server_id, auto_registered = await repo.ensure_server_id(
                    data.machine_id, data.hostname, placeholder,
                )
                result = await repo.record_metrics(server_id, dto)
                return server_id, auto_registered, result

            resolved_server_id, auto_registered, insert_result = await _db_retry(
                session_factory, repo_factory, save
            )

            if auto_registered:
                logger.info(
                    "auto-registered server from metrics machine_id={} hostname={} "
                    "(정적 정보는 다음 inventory 도착 시 채워짐)",
                    data.machine_id, data.hostname,
                )

            online_key = consumer_settings.redis_key_online.format(resolved_server_id)
            cache_key  = consumer_settings.redis_key_cache_metrics.format(resolved_server_id)
            await safe_set(redis, online_key, "1", ex=consumer_settings.redis_ttl_online)
            await safe_delete(redis, cache_key)
            await safe_publish(
                redis,
                consumer_settings.redis_channel_metrics,
                json.dumps({"server_id": resolved_server_id, "machine_id": data.machine_id}),
            )
            await _track_agent_restart(redis, resolved_server_id, data.machine_id, data.agent_started_at)

            # F7: 메시지별 처리 흐름은 DEBUG — 1만 서버 시 분당 1만 line 방지.
            logger.debug(
                "metrics stored machine_id={} rows metrics={} disk_io={} net_io={} mount_usage={}",
                data.machine_id,
                insert_result.metrics, insert_result.disk_io,
                insert_result.net_io, insert_result.mount_usage,
            )

    return _handle


def make_task_result_handler(
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], BaseCollectRepository],
    redis: Redis,
) -> Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]:
    """작업 결과 수신 핸들러.

    흐름: 멱등성 → DB UPDATE (status / completed_at / failure_reason / exit_code /
    duration_ms / stdout_tail / stderr_tail).
    task_id 미존재 시 silent ack (운영자가 task 삭제했을 가능성 — DLQ 부적합).
    boot_time / agent_started_at은 본 메시지에서 항상 null이라 time invariant 검증 생략.
    """
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = TaskResultInput.model_validate_json(message.body)
            except ValidationError as e:
                logger.error("task_result parse error count={}", len(e.errors()))
                raise

            if not await _check_idempotent(redis, data.message_id):
                logger.info("task_result duplicate skipped message_id={}", data.message_id)
                return

            update = TaskResultUpdate(
                public_id=str(data.task_id),
                status=data.status,
                failure_reason=data.failure_reason,
                exit_code=data.exit_code,
                duration_ms=data.duration_ms,
                stdout_tail=data.stdout_tail,
                stderr_tail=data.stderr_tail,
                completed_at=data.completed_at,
            )

            async def commit(repo: BaseCollectRepository) -> bool:
                return await repo.complete_task(update)

            updated = await _db_retry(session_factory, repo_factory, commit)
            if not updated:
                logger.warning("task_result for unknown task_id={} (silent ack)", data.task_id)
                return

            logger.info(
                "task_result stored machine_id={} task_id={} status={} failure_reason={}",
                data.machine_id, data.task_id, data.status, data.failure_reason,
            )

    return _handle


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
                "agent error machine_id={} component={} code={} msg={} "
                "retry_count={} first_failed_at={} recovered_at={}",
                data.machine_id,
                data.failed_component,
                data.error_code,
                data.error_message,
                data.retry_count,
                data.first_failed_at,
                data.recovered_at,
            )

    return _handle