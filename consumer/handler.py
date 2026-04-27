import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Any

from aio_pika.abc import AbstractIncomingMessage
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import consumer_settings
from consumer.schemas import ErrorInput, InventoryInput, MetricsInput
from db.repositories.base_collect_repository import BaseCollectRepository
from db.repositories.inbound import ServerInventoryCreate, ServerMetricCreate


def _to_inventory_create(data: InventoryInput) -> ServerInventoryCreate:
    return ServerInventoryCreate(
        machine_id=data.machine_id,
        hostname=data.hostname,
        agent_version=data.agent_version,
        collected_at=data.collected_at,
        os_id=data.os_id,
        os_version=data.os_version,
        os_codename=data.os_codename,
        kernel_version=data.kernel_version,
        cpu_cores=data.cpu_cores,
        cpu_model=data.cpu_model,
        mem_total_kb=data.mem_total_kb,
        swap_total_kb=data.swap_total_kb,
        boot_time=data.boot_time,
        ip_internal=data.ip_internal,
        ip_external=data.ip_external,
        disks=[{"name": d.name, "size_bytes": d.size_bytes, "type": d.type} for d in data.disks],
        mounts=[{"mount": m.mount, "fstype": m.fstype, "total_bytes": m.total_bytes} for m in data.mounts],
    )


def _to_metric_create(data: MetricsInput) -> ServerMetricCreate:
    cpu = data.cpu_stat
    return ServerMetricCreate(
        collected_at=data.collected_at,
        cpu_user=cpu.user if cpu else None,
        cpu_nice=cpu.nice if cpu else None,
        cpu_system=cpu.system if cpu else None,
        cpu_idle=cpu.idle if cpu else None,
        cpu_iowait=cpu.iowait if cpu else None,
        cpu_irq=cpu.irq if cpu else None,
        cpu_softirq=cpu.softirq if cpu else None,
        cpu_steal=cpu.steal if cpu else None,
        mem_total_kb=data.mem_total_kb,
        mem_free_kb=data.mem_free_kb,
        mem_available_kb=data.mem_available_kb,
        mem_buffers_kb=data.mem_buffers_kb,
        mem_cached_kb=data.mem_cached_kb,
        swap_total_kb=data.swap_total_kb,
        swap_free_kb=data.swap_free_kb,
        load_1m=data.load_1m,
        load_5m=data.load_5m,
        load_15m=data.load_15m,
        disk_io=[
            {
                "device": d.device,
                "reads_completed": d.reads_completed,
                "writes_completed": d.writes_completed,
                "sectors_read": d.sectors_read,
                "sectors_written": d.sectors_written,
            }
            for d in data.disk_io
        ],
        mounts=[
            {
                "mount": m.mount,
                "total_bytes": m.total_bytes,
                "free_bytes": m.free_bytes,
                "avail_bytes": m.avail_bytes,
            }
            for m in data.mounts
        ],
        net_io=[
            {
                "interface": n.interface,
                "rx_bytes": n.rx_bytes,
                "tx_bytes": n.tx_bytes,
                "rx_packets": n.rx_packets,
                "tx_packets": n.tx_packets,
                "rx_errors": n.rx_errors,
                "tx_errors": n.tx_errors,
            }
            for n in data.net_io
        ],
    )


async def _db_retry(
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], BaseCollectRepository],
    fn: Callable[[BaseCollectRepository], Coroutine[Any, Any, None]],
) -> None:
    for attempt in range(3):
        try:
            async with session_factory() as session:
                await fn(repo_factory(session))
                await session.commit()
            return
        except Exception as e:
            if attempt == 2:
                logger.error("db error after 3 attempts: {}", e)
                raise
            logger.warning("db error attempt={} error={}", attempt + 1, e)
            await asyncio.sleep(2 ** attempt)


async def _check_idempotent(redis: Redis, message_id: str) -> bool:
    """SET NX로 원자적으로 처리 여부 확인. 처음 처리면 True, 중복이면 False."""
    key = consumer_settings.redis_key_idempotent.format(message_id)
    return bool(await redis.set(key, 1, ex=consumer_settings.redis_ttl_idempotent, nx=True))


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

            if not await _check_idempotent(redis, str(data.message_id)):
                logger.info("inventory duplicate skipped message_id={}", data.message_id)
                return

            dto = _to_inventory_create(data)
            await _db_retry(
                session_factory,
                repo_factory,
                lambda repo: repo.upsert_server(dto),
            )
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

            if not await _check_idempotent(redis, str(data.message_id)):
                logger.info("metrics duplicate skipped message_id={}", data.message_id)
                return

            dto = _to_metric_create(data)
            resolved_server_id: int | None = None

            async def _save(repo: BaseCollectRepository) -> None:
                nonlocal resolved_server_id
                server_id = await repo.find_server_id(data.machine_id)
                if server_id is None:
                    logger.warning(
                        "metrics dropped — server not registered machine_id={}",
                        data.machine_id,
                    )
                    return
                await repo.insert_metric(server_id, dto)
                resolved_server_id = server_id

            await _db_retry(session_factory, repo_factory, _save)

            if resolved_server_id is not None:
                online_key = consumer_settings.redis_key_online.format(resolved_server_id)
                cache_key = consumer_settings.redis_key_cache_metrics.format(resolved_server_id)
                await redis.set(online_key, 1, ex=consumer_settings.redis_ttl_online)
                await redis.delete(cache_key)
                await redis.publish(
                    consumer_settings.redis_channel_metrics,
                    json.dumps({"server_id": resolved_server_id, "machine_id": data.machine_id}),
                )
                logger.info("metrics stored machine_id={}", data.machine_id)

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

            if not await _check_idempotent(redis, str(data.message_id)):
                logger.info("error duplicate skipped message_id={}", data.message_id)
                return

            logger.warning(
                "agent error machine_id={} component={} code={} msg={}",
                data.machine_id,
                data.failed_component,
                data.error_code,
                data.error_message,
            )

    return _handle