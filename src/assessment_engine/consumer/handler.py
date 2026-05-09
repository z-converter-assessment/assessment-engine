import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

from uuid import UUID

from aio_pika import DeliveryMode, Message
from aio_pika.abc import AbstractIncomingMessage
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.config import consumer_settings
from assessment_engine.consumer.mappers import placeholder_inventory_from_metrics, to_inventory_create, to_metric_create
from assessment_engine.consumer.schemas import ErrorInput, InventoryInput, MessageBase, MetricsInput, TaskResultInput
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


async def _db_retry(
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
            logger.error("db integrity error (non-retryable): {}", e)
            raise
        except _RETRYABLE_DB_EXC as e:
            if attempt == 2:
                logger.error("db error after 3 attempts: {}", e)
                raise
            logger.warning("db error attempt={} error={}", attempt + 1, e)
            await asyncio.sleep(5 ** (attempt + 1))
    raise AssertionError("unreachable")


async def _check_idempotent(redis: Redis, message_id: UUID) -> bool:
    """SET NX 멱등성 체크. 첫 처리면 True, 중복이면 False.

    Redis 장애 시 fail-open (True 반환) — 처리 진행. DB UNIQUE 제약(2단)이 중복 INSERT를 흡수.
    docs/decisions/redis-decoupling.md §6 단계 4 참조.
    """
    key = consumer_settings.redis_key_idempotent.format(message_id.hex)
    result = await safe_set_nx(redis, key, "1", consumer_settings.redis_ttl_idempotent)
    return True if result is None else result


def _log_time_invariants(data: MessageBase) -> None:
    """시계·systemd 시작 순서 invariant 검증. warning 로그만, 처리는 그대로 진행.

    - boot_time > agent_started_at: systemd 시작 순서 비정상 또는 시계 동기화 문제 (드뭄)
    - agent_started_at > collected_at: VM 시계 동기화 문제 (가장 흔함 — VM resume 직후)
    DLQ로 보내지 않음 — 시계 문제는 데이터 reject 의미 없고 운영자 인지가 목적.
    """
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


async def _reply_pending_task_if_any(
    message: AbstractIncomingMessage,
    redis: Redis,
    machine_id: str,
) -> None:
    """metrics 메시지의 reply_to에 pending task 1건을 응답.

    - reply_to 미지정 → no-op (옛 agent · piggyback 미지원)
    - Redis EXISTS task:pending:{machine_id} → 99% no-op이라 hot path는 ms 단위
    - 있으면 GET → reply publish (correlation_id 그대로 회신)
    Redis 장애 시 silent skip (다음 metrics 주기에 재시도). DB 직접 조회 안 함 — hot path 보호.
    """
    if not message.reply_to:
        return
    key = consumer_settings.redis_key_task_pending.format(machine_id)
    payload = await safe_get(redis, key)
    if not payload:
        return
    try:
        await message.channel.default_exchange.publish(
            Message(
                body=payload.encode(),
                content_type="application/json",
                correlation_id=message.correlation_id,
                delivery_mode=DeliveryMode.NOT_PERSISTENT,
            ),
            routing_key=message.reply_to,
        )
        logger.info("task piggyback replied machine_id={} correlation_id={}", machine_id, message.correlation_id)
    except Exception as e:
        logger.warning("task piggyback reply failed machine_id={} err={}", machine_id, e)


async def _track_agent_restart(redis: Redis, server_id: int, machine_id: str, agent_started_at) -> None:
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
            except Exception as e:
                logger.error("inventory parse error: {}", e)
                raise

            if not await _check_idempotent(redis, data.message_id):
                logger.info("inventory duplicate skipped message_id={}", data.message_id)
                return

            _log_time_invariants(data)

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
            except Exception as e:
                logger.error("metrics parse error: {}", e)
                raise

            if not await _check_idempotent(redis, data.message_id):
                logger.info("metrics duplicate skipped message_id={}", data.message_id)
                return

            _log_time_invariants(data)

            dto = to_metric_create(data)
            placeholder = placeholder_inventory_from_metrics(data)

            async def save(repo: BaseCollectRepository) -> tuple[int, bool, MetricInsertResult]:
                # find→upsert 흐름은 repo.ensure_server_id로 캡슐화. find 성공 시 placeholder 미사용.
                server_id, auto_registered = await repo.ensure_server_id(data.machine_id, placeholder)
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

            # RPC piggyback — agent가 reply_to를 명시한 경우 pending task 확인 후 reply.
            # latency 같지만 별도 polling endpoint·queue 불필요 (CLAUDE.md B6).
            await _reply_pending_task_if_any(message, redis, data.machine_id)
            logger.info(
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
    """agent → engine 작업 결과 수신.

    흐름: 멱등성 → DB UPDATE (status/completed_at/result_message) → Redis pending 키 DEL.
    public_id 미존재 시 silent ack (운영자가 task 삭제했을 가능성 — DLQ 부적합).
    """
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = TaskResultInput.model_validate_json(message.body)
            except Exception as e:
                logger.error("task_result parse error: {}", e)
                raise

            if not await _check_idempotent(redis, data.message_id):
                logger.info("task_result duplicate skipped message_id={}", data.message_id)
                return

            _log_time_invariants(data)

            update = TaskResultUpdate(
                public_id=str(data.task_public_id),
                status=data.status,
                result_message=data.result_message,
            )

            async def commit(repo: BaseCollectRepository) -> bool:
                return await repo.complete_task(update)

            updated = await _db_retry(session_factory, repo_factory, commit)
            if not updated:
                logger.warning("task_result for unknown task_public_id={} (silent ack)", data.task_public_id)
                return

            # Redis pending 키 정리 — agent가 같은 task를 재요청하지 않게.
            pending_key = consumer_settings.redis_key_task_pending.format(data.machine_id)
            await safe_delete(redis, pending_key)
            logger.info(
                "task_result stored machine_id={} task_public_id={} status={}",
                data.machine_id, data.task_public_id, data.status,
            )

    return _handle


def make_error_handler(
    redis: Redis,
) -> Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]:
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = ErrorInput.model_validate_json(message.body)
            except Exception as e:
                logger.error("error message parse error: {}", e)
                raise

            if not await _check_idempotent(redis, data.message_id):
                logger.info("error duplicate skipped message_id={}", data.message_id)
                return

            _log_time_invariants(data)

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