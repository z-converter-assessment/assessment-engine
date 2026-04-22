import asyncio
import json
from typing import Callable

from aio_pika.abc import AbstractIncomingMessage
from loguru import logger

from consumer.schemas import ServerMetricInput
from db.repositories.i_collect_repository import ICollectRepository


async def _save(repo: ICollectRepository, payload: ServerMetricInput) -> None:
    server_id = await repo.find_server(payload.hostname)
    if server_id is None:
        server_id = await repo.create_server(payload.hostname)
    await repo.insert_metric(
        server_id=server_id,
        nproc=payload.nproc,
        mem_total_mb=payload.mem_total_mb,
        disks=[d.model_dump() for d in payload.disks],
        ip_internal=[str(ip) for ip in payload.ip.internal],
        ip_external=[str(ip) for ip in payload.ip.external],
    )


def make_handler(session_factory, repo_cls) -> Callable:
    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process():
            try:
                payload = ServerMetricInput(**json.loads(message.body))
            except Exception as e:
                # TODO: 파싱 실패 처리 전략 검토 (DLQ 등)
                logger.error("invalid message body: {}", e)
                return

            for attempt in range(3):
                try:
                    async with session_factory() as session:
                        repo = repo_cls(session)
                        await _save(repo, payload)
                        await session.commit()
                    break
                except Exception as e:
                    if attempt == 2:
                        logger.error("db error after 3 attempts: {}", e)
                        raise
                    logger.warning("db error attempt={} error={}", attempt + 1, e)
                    await asyncio.sleep(2 ** attempt)

            logger.info("stored hostname={}", payload.hostname)

    return _handle