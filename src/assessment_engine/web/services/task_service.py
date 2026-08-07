"""원격 작업 발행 service — 운영자가 등록 호스트에 task.install 명령을 발행.

DB INSERT 와 메시지 publish 를 한 트랜잭션 경계 안에 묶는다 (서버별 독립 commit). 발행 흐름 전체는
`docs/explanation/products/install-task.md`.
"""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

import aio_pika
import httpx
from loguru import logger
from sqlalchemy.exc import IntegrityError

from assessment_engine.cache.redis import safe_get, safe_mget, safe_set
from assessment_engine.contract import AGENT_CONTRACT_VERSION
from assessment_engine.db.dtos.inbound import TaskCreate
from assessment_engine.web.settings import get_diagnostic_settings, get_web_settings

if TYPE_CHECKING:
    from collections.abc import Callable

    from aio_pika.abc import AbstractChannel
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from assessment_engine.db.repositories.collect import CollectRepository
    from assessment_engine.db.repositories.query import QueryRepository
    from assessment_engine.json_types import JsonObject

_TASK_TYPE_INSTALL = "zconverter_install"
_TASK_QUEUE_MAX_LEN = 100


def _resolve_install_dispatch(os_family: str) -> tuple[str, str, str | None]:
    """os_family -> (package_path, install_type, install_script).

    install_type 은 agent 측 실행 방식 계약 — shell 은 archive 를 풀고 install_script 를 실행하고,
    direct_exec 는 받은 파일을 그대로 실행한다(script null). 모르는 type 은 agent 가 거부한다.
    """
    if os_family == "linux":
        return (get_web_settings().zdm_package_path, "shell", get_web_settings().zdm_package_script)
    if os_family == "windows":
        return (get_web_settings().zdm_package_path_windows, "direct_exec", None)
    raise TaskNotConfiguredError(f"unsupported os_family={os_family!r}")


# 운영자가 URL 을 통째로 넣어도 host(:port)만 남긴다 — download.url 은 `http://{host}{package_path}` 로 조립된다.
_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def _extract_zdm_host(zdm_ip: str) -> str:
    s = _URL_SCHEME_RE.sub("", zdm_ip).strip("/")
    slash = s.find("/")
    return s if slash < 0 else s[:slash]


# cache key 에 ETag 를 넣는 이유는 그것이 곧 invalidation 키라서다 — 패키지가 바뀌면 ETag 가 바뀌므로
# TTL 을 길게 잡아도 stale 을 내주지 않는다. package_path 를 키에 안 넣는 것도 같은 이유 — path 가 다르면
# ETag 도 다르다. 메타 조회 자체는 fail-close(sha256 없이 발행하면 agent 가 검증 없이 설치한다),
# Redis 는 fail-open.


class ZdmPackageMetaError(Exception):
    """router 가 HTTPException(503) 로 변환 — ZDM 메타 조회 실패."""


class BaseZdmPackageResolver(Protocol):
    async def resolve(self, zdm_host: str, package_path: str) -> tuple[str, int]:
        """ZDM 패키지의 (sha256_hex, size_bytes) 반환. 실패 시 raise."""
        ...


class HttpZdmPackageResolver:
    def __init__(self, http_client: httpx.AsyncClient, redis: Redis) -> None:
        self.http = http_client
        self.redis = redis

    async def resolve(self, zdm_host: str, package_path: str) -> tuple[str, int]:
        url = f"http://{zdm_host}{package_path}"

        try:
            head_resp = await self.http.head(url)
        except httpx.HTTPError as e:
            raise ZdmPackageMetaError(f"HEAD failed: {type(e).__name__}: {e}") from e
        if head_resp.status_code != 200:
            raise ZdmPackageMetaError(f"HEAD status={head_resp.status_code}")
        content_length_raw = head_resp.headers.get("Content-Length")
        if content_length_raw is None:
            raise ZdmPackageMetaError("HEAD missing Content-Length")
        try:
            size_bytes = int(content_length_raw)
        except ValueError as e:
            raise ZdmPackageMetaError(f"HEAD Content-Length not int: {content_length_raw!r}") from e
        if size_bytes <= 0:
            raise ZdmPackageMetaError(f"HEAD Content-Length non-positive: {size_bytes}")

        # 둘 다 없으면 캐시를 건너뛴다 — 매 publish 마다 GET 으로 sha256 을 다시 내므로 느릴 뿐 결과는 정확.
        etag = head_resp.headers.get("ETag") or head_resp.headers.get("Last-Modified") or ""
        cache_key = get_web_settings().redis_key_zdm_package_sha256.format(zdm_host, etag) if etag else ""

        if cache_key:
            cached = await safe_get(self.redis, cache_key)
            if cached:
                logger.info("zdm package meta cache hit host={} etag={}", zdm_host, etag)
                return cached, size_bytes

        sha = hashlib.sha256()
        bytes_read = 0
        try:
            async with self.http.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise ZdmPackageMetaError(f"GET status={resp.status_code}")
                async for chunk in resp.aiter_bytes():
                    sha.update(chunk)
                    bytes_read += len(chunk)
        except httpx.HTTPError as e:
            raise ZdmPackageMetaError(f"GET failed: {type(e).__name__}: {e}") from e

        if bytes_read != size_bytes:
            raise ZdmPackageMetaError(f"size mismatch: HEAD={size_bytes} GET={bytes_read} — ZDM 측 정합성 깨짐")

        sha256_hex = sha.hexdigest()
        logger.info(
            "zdm package meta computed host={} etag={} sha256={} size={}",
            zdm_host,
            etag,
            sha256_hex[:16] + "...",
            size_bytes,
        )

        if cache_key:
            await safe_set(self.redis, cache_key, sha256_hex, ex=get_web_settings().redis_ttl_zdm_package_sha256)

        return sha256_hex, size_bytes


@dataclass
class TaskCreated:
    target_public_id: str
    task_id: str
    # 발행 시점 online 여부 — 운영자 advisory 이고 발행을 막지 않는다(오프라인이면 큐에 적재된 채 대기).
    target_online: bool


class TaskService:
    def __init__(
        self,
        query_repo: QueryRepository,
        session_factory: async_sessionmaker[AsyncSession],
        collect_repo_factory: Callable[[AsyncSession], CollectRepository],
        broker_channel: AbstractChannel,
        zdm_resolver: BaseZdmPackageResolver,
        redis: Redis,
    ):
        self.query_repo = query_repo
        self.session_factory = session_factory
        self.collect_repo_factory = collect_repo_factory
        self.broker_channel = broker_channel
        self.zdm_resolver = zdm_resolver
        self.redis = redis

    async def create_install_tasks(
        self,
        target_public_ids: list[str],
        zdm_ip: str,
        zdm_user: str,
    ) -> list[TaskCreated]:
        """선택 호스트 N대에 install task 발행 — 서버별 독립 트랜잭션 + 독립 publish.

        zdm_ip / zdm_user 는 install 스크립트의 `-s` / `-u` 인자로 나가 ZDM 에서 setup 패키지를 받아 실행한다.
        미존재 public_id·진행 중 task·ZDM 메타 조회 실패는 부분 발행 없이 전체를 raise 로 취소한다.
        """
        sid_map = await self.query_repo.resolve_server_ids(target_public_ids)
        missing = [pid for pid in target_public_ids if pid not in sid_map]
        if missing:
            logger.warning("install target not found public_ids={}", missing[:5])
            raise TaskNotFoundError(
                "선택한 서버 중 일부를 찾을 수 없어 전체 발행을 취소했습니다 (이미 삭제됐을 수 있음)."
            )
        server_ids = [sid_map[pid] for pid in target_public_ids]
        details = await self.query_repo.get_servers(server_ids)
        detail_by_id = {d.id: d for d in details}

        # 마감 지난 pending 을 먼저 정리해야 이미 끝난 작업이 신규 발행을 막지 않는다.
        async with self.session_factory() as session:
            repo = self.collect_repo_factory(session)
            expired = await repo.expire_overdue_tasks(server_ids)
            busy_ids = await repo.find_pending_deadline_servers(server_ids)
            await session.commit()
        if expired:
            logger.info("expired overdue tasks count={}", expired)
        if busy_ids:
            busy_names = sorted(detail_by_id[sid].hostname for sid in busy_ids if sid in detail_by_id)
            raise TaskDuplicatePendingError(
                f"이미 설치 작업이 진행 중인 서버가 있어 전체 발행을 취소했습니다: {', '.join(busy_names)}"
            )
        # 아래 큐 x-message-ttl 과 같은 창이라 엔진 timeout 선언 시점 == broker 미배달 메시지 만료 시점.
        # 오프라인 호스트가 돌아와 큐에 쌓인 명령을 집어갈 수 있는 유예도 같은 창이다.
        deadline_at = datetime.now(UTC) + timedelta(seconds=get_web_settings().install_task_deadline_sec)

        online_by_id = await self._online_targets(server_ids)

        zdm_host = _extract_zdm_host(zdm_ip)
        # 메타를 받아오는 호스트는 agent 가 실제로 받으러 갈 호스트와 같아야 한다 (download.url·install args 와 동일).
        resolve_host = zdm_host

        # 패키지 path 별 1 회만 메타 조회 — batch 에 OS 가 섞여도 OS 수만큼만 fetch.
        # os_family 를 아직 안 싣는 구 minor agent 는 linux 로 본다.
        dispatch_by_host: dict[int, tuple[str, str, str | None]] = {}
        meta_by_path: dict[str, tuple[str, int]] = {}
        for server_id, detail in detail_by_id.items():
            os_family = detail.os_family or "linux"
            package_path, install_type, install_script = _resolve_install_dispatch(os_family)
            dispatch_by_host[server_id] = (package_path, install_type, install_script)
            if package_path not in meta_by_path:
                try:
                    meta_by_path[package_path] = await self.zdm_resolver.resolve(resolve_host, package_path)
                except ZdmPackageMetaError as e:
                    logger.error("ZDM package meta fetch failed path={} err={}", package_path, e)
                    raise TaskNotConfiguredError(
                        "ZDM 패키지 정보를 가져오지 못해 발행을 취소했습니다. ZDM 서버 연결을 확인하세요."
                    ) from e

        exchange = await self.broker_channel.get_exchange(get_diagnostic_settings().rabbitmq_task_exchange)

        created: list[TaskCreated] = []
        for public_id in target_public_ids:
            server_id = sid_map[public_id]
            detail = detail_by_id.get(server_id)
            if detail is None:
                logger.warning("install server detail missing public_id={}", public_id)
                raise TaskNotFoundError("서버 정보를 불러오지 못해 발행을 취소했습니다.")

            package_path, install_type, install_script = dispatch_by_host[server_id]
            sha256_hex, size_bytes = meta_by_path[package_path]

            async with self.session_factory() as session:
                repo = self.collect_repo_factory(session)
                try:
                    task_id = await repo.create_task(
                        TaskCreate(
                            target_server_id=server_id,
                            target_agent_id=detail.agent_id,
                            task_type=_TASK_TYPE_INSTALL,
                            params={
                                "zdm_ip": zdm_ip,
                                "zdm_user": zdm_user,
                            },
                            deadline_at=deadline_at,
                        )
                    )
                except IntegrityError as e:
                    # 사전 검증과 INSERT 사이에 다른 발행이 끼어든 경우 — 부분 발행 한계는 tradeoffs T1.
                    logger.warning("install race conflict server_id={} public_id={}", server_id, public_id)
                    raise TaskDuplicatePendingError(
                        f"발행 도중 다른 작업과 충돌했습니다: {detail.hostname}. 잠시 후 다시 시도하세요."
                    ) from e

                # commit 은 publish 성공 뒤여야 한다 — 순서를 뒤집으면 발행 실패한 task 가 유령 pending 으로 남는다.
                try:
                    await self._ensure_machine_queue(detail.agent_id)
                    await self._publish_install(
                        exchange,
                        task_id,
                        detail.agent_id,
                        zdm_host,
                        zdm_user,
                        sha256_hex,
                        size_bytes,
                        package_path,
                        install_type,
                        install_script,
                    )
                except (aio_pika.exceptions.AMQPError, TimeoutError) as e:
                    logger.error("task.install publish failed server_id={} public_id={}", server_id, public_id)
                    raise TaskPublishFailedError(
                        f"작업 발행 중 broker 오류로 취소했습니다: {detail.hostname}. 잠시 후 다시 시도하세요."
                    ) from e
                await session.commit()

            logger.info(
                "task.install published task_id={} agent_id={} target={}",
                task_id,
                detail.agent_id,
                public_id,
            )
            created.append(
                TaskCreated(
                    target_public_id=public_id,
                    task_id=task_id,
                    target_online=online_by_id.get(server_id, True),
                )
            )

        return created

    async def _online_targets(self, server_ids: list[int]) -> dict[int, bool]:
        """server_id -> 발행 시점 online 여부. Redis 장애 시 전부 online 취급 — 허위 오프라인 경보를 내지 않는다."""
        if not server_ids:
            return {}
        keys = [get_web_settings().redis_key_online.format(sid) for sid in server_ids]
        flags = await safe_mget(self.redis, keys)
        if flags is None:
            return dict.fromkeys(server_ids, True)
        return {sid: flags[i] is not None for i, sid in enumerate(server_ids)}

    async def _ensure_machine_queue(self, agent_id: str) -> None:
        """원격 호스트 전용 큐 declare (동일 인자 재선언은 무해) — worker 측에 declare 권한이 없어 engine 이 진다."""
        queue_name = get_diagnostic_settings().agent_task_queue(agent_id)
        routing_key = get_diagnostic_settings().task_install_routing_key(agent_id)
        queue = await self.broker_channel.declare_queue(
            queue_name,
            durable=True,
            arguments={
                # tasks.deadline_at 과 같은 창 — 엔진이 timeout 을 선언하는 시점에 미배달 메시지도 만료된다.
                "x-message-ttl": get_web_settings().install_task_deadline_sec * 1000,
                "x-max-length": _TASK_QUEUE_MAX_LEN,
                "x-overflow": "reject-publish",
            },
        )
        exchange = await self.broker_channel.get_exchange(get_diagnostic_settings().rabbitmq_task_exchange)
        await queue.bind(exchange, routing_key=routing_key)

    async def _publish_install(
        self,
        exchange: aio_pika.abc.AbstractExchange,
        task_id: str,
        agent_id: str,
        zdm_host: str,
        zdm_user: str,
        sha256_hex: str,
        size_bytes: int,
        package_path: str,
        install_type: str,
        install_script: str | None,
    ) -> None:
        download_url = f"http://{zdm_host}{package_path}"
        payload: JsonObject = {
            "message_type": "task.install",
            # 특권 실행 경로라 agent 가 다운로드 전에 major 를 본다 — 모르는 major·부재면 실행 거부.
            "schema_version": AGENT_CONTRACT_VERSION,
            "task_id": task_id,
            "agent_id": agent_id,
            "issued_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "download": {
                "url": download_url,
                "sha256": sha256_hex,
                "size_bytes": size_bytes,
            },
            "install": {
                "type": install_type,
                "script": install_script,  # shell 이면 archive 안 path, direct_exec/msi 면 null
                "args": ["-s", zdm_host, "-u", zdm_user],
                "timeout_sec": get_web_settings().install_timeout_sec,
            },
        }
        message = aio_pika.Message(
            body=json.dumps(payload).encode("utf-8"),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
            message_id=str(uuid.uuid4()),
        )
        routing_key = get_diagnostic_settings().task_install_routing_key(agent_id)
        await exchange.publish(message, routing_key=routing_key)


class TaskNotFoundError(Exception):
    """router 가 HTTPException(404) 로 변환."""


class TaskDuplicatePendingError(Exception):
    """router 가 HTTPException(409) 로 변환 — pending task 이미 존재."""


class TaskNotConfiguredError(Exception):
    """router 가 HTTPException(503) 로 변환 — ZDM 패키지 contract 미설정."""


class TaskPublishFailedError(Exception):
    """router 가 HTTPException(503) 로 변환 — broker publish 실패 (task INSERT 는 rollback, 유령 pending 없음)."""
