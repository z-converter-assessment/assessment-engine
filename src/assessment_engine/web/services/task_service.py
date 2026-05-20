"""원격 작업 발행 service — 운영자가 등록 호스트에 task.install 명령을 발행.

흐름: web POST -> DB INSERT (이력) -> agent.tasks.<machine_id> 큐 declare (idempotent)
      -> assessment.tasks exchange 에 task.install.<machine_id> routing key 로 publish.
원격 호스트의 worker 가 본 큐를 consume 해 install bundle fetch + 실행 후 task.result 발행.

책임 경계:
- DB INSERT + 메시지 publish 캡슐화 — router 는 service 만 호출
- 추상 `BaseCollectRepository` 의존 (F4) — composition root 에서 구체 주입
- 트랜잭션 경계는 service 가 관리 (서버별 독립 commit + best-effort publish)
"""
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import aio_pika
from aio_pika.abc import AbstractChannel
from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.db.repositories.base_collect_repository import BaseCollectRepository
from assessment_engine.db.repositories.base_query_repository import BaseQueryRepository
from assessment_engine.db.repositories.inbound import TaskCreate
from assessment_engine.web.settings import diagnostic_settings, web_settings

_TASK_TYPE_INSTALL = "zconverter_install"
_TASK_QUEUE_TTL_MS = 60 * 60 * 1000  # 1h — 원격 호스트가 그 사이 consume 못 하면 만료
_TASK_QUEUE_MAX_LEN = 100

# 운영자 입력에서 scheme·path 제거 → host (또는 host:port) 만 추출.
# agent download.url 조립 시 host 만 사용 (https?://{host}{zdm_package_path} 형태).
_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def _extract_zdm_host(zdm_ip: str) -> str:
    s = _URL_SCHEME_RE.sub("", zdm_ip).strip("/")
    slash = s.find("/")
    return s if slash < 0 else s[:slash]


@dataclass
class TaskCreated:
    target_public_id: str
    task_id: str


class TaskService:
    def __init__(
        self,
        query_repo: BaseQueryRepository,
        session_factory: async_sessionmaker[AsyncSession],
        collect_repo_factory: Callable[[AsyncSession], BaseCollectRepository],
        broker_channel: AbstractChannel,
    ):
        self.query_repo = query_repo
        self.session_factory = session_factory
        self.collect_repo_factory = collect_repo_factory
        self.broker_channel = broker_channel

    async def create_install_tasks(
        self,
        target_public_ids: list[str],
        zdm_ip: str,
        zdm_user: str,
    ) -> list[TaskCreated]:
        """선택 호스트 N대에 install task 발행.

        best-effort — 서버별 독립 트랜잭션 + 독립 publish. 미존재 public_id 는 즉시 raise.
        zdm_ip / zdm_user 는 install 스크립트의 `-s` / `-u` 인자로 전달되어 ZDM 서버에서
        실제 setup 패키지 fetch + 실행에 사용. 본 엔진은 Linux 호스트만 발행 대상.

        ZDM 본체 패키지 sha256 / size_bytes 가 settings 에 미설정이면 publish 차단 — 운영자에게
        설정 누락을 명시 (TaskNotConfigured → 503).
        """
        if not web_settings.zdm_package_sha256 or web_settings.zdm_package_size_bytes <= 0:
            raise TaskNotConfigured(
                "ZDM package contract not configured — set ZDM_PACKAGE_SHA256 and ZDM_PACKAGE_SIZE_BYTES"
            )

        sid_map = await self.query_repo.resolve_server_ids(target_public_ids)
        missing = [pid for pid in target_public_ids if pid not in sid_map]
        if missing:
            raise TaskNotFound(f"server not found: {','.join(missing[:5])}")
        server_ids = [sid_map[pid] for pid in target_public_ids]
        details = await self.query_repo.get_servers(server_ids)
        detail_by_id = {d.id: d for d in details}

        zdm_host = _extract_zdm_host(zdm_ip)

        exchange = await self.broker_channel.get_exchange(diagnostic_settings.rabbitmq_task_exchange)

        created: list[TaskCreated] = []
        for public_id in target_public_ids:
            server_id = sid_map[public_id]
            detail = detail_by_id.get(server_id)
            if detail is None:
                raise TaskNotFound(f"server detail missing: {public_id}")

            async with self.session_factory() as session:
                repo = self.collect_repo_factory(session)
                try:
                    task_id = await repo.create_task(TaskCreate(
                        target_server_id=server_id,
                        target_machine_id=detail.machine_id,
                        task_type=_TASK_TYPE_INSTALL,
                        params={
                            "zdm_ip": zdm_ip,
                            "zdm_user": zdm_user,
                        },
                    ))
                    await session.commit()
                except IntegrityError as e:
                    raise TaskDuplicatePending(
                        f"pending task already exists for {public_id} ({_TASK_TYPE_INSTALL})"
                    ) from e

            await self._ensure_machine_queue(detail.machine_id)
            await self._publish_install(
                exchange, task_id, detail.machine_id, zdm_host, zdm_user,
            )

            logger.info(
                "task.install published task_id={} machine_id={} target={}",
                task_id, detail.machine_id, public_id,
            )
            created.append(TaskCreated(target_public_id=public_id, task_id=task_id))

        return created

    async def _ensure_machine_queue(self, machine_id: str) -> None:
        """원격 호스트 전용 큐 declare. idempotent — 동일 인자 재선언 안전.

        worker 측은 declare 권한이 없어 engine 이 책임진다.
        """
        queue_name = f"{diagnostic_settings.rabbitmq_task_queue_prefix}.{machine_id}"
        routing_key = f"{diagnostic_settings.rabbitmq_task_install_key_prefix}.{machine_id}"
        queue = await self.broker_channel.declare_queue(
            queue_name,
            durable=True,
            arguments={
                "x-message-ttl":  _TASK_QUEUE_TTL_MS,
                "x-max-length":   _TASK_QUEUE_MAX_LEN,
                "x-overflow":     "reject-publish",
            },
        )
        exchange = await self.broker_channel.get_exchange(diagnostic_settings.rabbitmq_task_exchange)
        await queue.bind(exchange, routing_key=routing_key)

    async def _publish_install(
        self,
        exchange: aio_pika.abc.AbstractExchange,
        task_id: str,
        machine_id: str,
        zdm_host: str,
        zdm_user: str,
    ) -> None:
        download_url = f"http://{zdm_host}{web_settings.zdm_package_path}"
        payload = {
            "message_type": "task.install",
            "task_id":      task_id,
            "machine_id":   machine_id,
            "issued_at":    datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "download": {
                "url":        download_url,
                "sha256":     web_settings.zdm_package_sha256,
                "size_bytes": web_settings.zdm_package_size_bytes,
            },
            "install": {
                "script":      web_settings.zdm_package_script,
                "args":        ["-s", zdm_host, "-u", zdm_user],
                "timeout_sec": web_settings.install_timeout_sec,
            },
        }
        message = aio_pika.Message(
            body=json.dumps(payload).encode("utf-8"),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
            message_id=str(uuid.uuid4()),
        )
        routing_key = f"{diagnostic_settings.rabbitmq_task_install_key_prefix}.{machine_id}"
        await exchange.publish(message, routing_key=routing_key)


class TaskNotFound(Exception):
    """router 가 HTTPException(404) 로 변환."""


class TaskDuplicatePending(Exception):
    """router 가 HTTPException(409) 로 변환 — pending task 이미 존재."""


class TaskNotConfigured(Exception):
    """router 가 HTTPException(503) 로 변환 — ZDM 패키지 contract 미설정."""
