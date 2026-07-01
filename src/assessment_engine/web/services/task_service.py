"""원격 작업 발행 service — 운영자가 등록 호스트에 task.install 명령을 발행.

흐름: web POST -> DB INSERT (이력) -> agent.tasks.<agent_id> 큐 declare (idempotent)
      -> assessment.tasks exchange 에 task.install.<agent_id> routing key 로 publish.
원격 호스트의 worker 가 본 큐를 consume 해 install bundle fetch + 실행 후 task.result 발행.

책임 경계:
- DB INSERT + 메시지 publish 캡슐화 — router 는 service 만 호출
- 추상 `BaseCollectRepository` 의존 (F4) — composition root 에서 구체 주입
- 트랜잭션 경계는 service 가 관리 (서버별 독립 commit + best-effort publish)
- ZDM 패키지 메타 (sha256·size) 조회 헬퍼는 본 모듈 상단 `HttpZdmPackageResolver` — install 발행 의존성.
"""

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import aio_pika
import httpx
from aio_pika.abc import AbstractChannel
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.cache.redis import safe_get, safe_set
from assessment_engine.db.dtos.inbound import TaskCreate
from assessment_engine.db.repositories.base_collect_repository import BaseCollectRepository
from assessment_engine.db.repositories.query.base_query_repository import BaseQueryRepository
from assessment_engine.web.settings import diagnostic_settings, web_settings

_TASK_TYPE_INSTALL = "zconverter_install"
# engine 측 응답 마감 = install_timeout_sec(agent wall-clock) + 네트워크 margin.
# 경과 시 표시 timeout + 재발행 시 expire.
_DEADLINE_MARGIN_SEC = 60
_TASK_QUEUE_TTL_MS = 60 * 60 * 1000  # 1h — 원격 호스트가 그 사이 consume 못 하면 만료
_TASK_QUEUE_MAX_LEN = 100


def _resolve_install_dispatch(os_family: str) -> tuple[str, str, str | None]:
    """os_family -> (package_path, install_type, install_script). ADR 0019 / ADR 0020.

    Linux  = .tar.gz extract + install.sh exec.
    Windows = single .exe 직접 실행 (extract 없음, install.script null).
    그 외 = TaskNotConfigured raise (운영자 알림 — agent 미지원).
    """
    if os_family == "linux":
        return (web_settings.zdm_package_path, "shell", web_settings.zdm_package_script)
    if os_family == "windows":
        return (web_settings.zdm_package_path_windows, "direct_exec", None)
    raise TaskNotConfigured(f"unsupported os_family={os_family!r}")


# 운영자 입력에서 scheme·path 제거 → host (또는 host:port) 만 추출.
# agent download.url 조립 시 host 만 사용 (https?://{host}{zdm_package_path} 형태).
_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def _extract_zdm_host(zdm_ip: str) -> str:
    s = _URL_SCHEME_RE.sub("", zdm_ip).strip("/")
    slash = s.find("/")
    return s if slash < 0 else s[:slash]


# ─── ZDM 패키지 메타 (sha256·size_bytes) 동적 조회 ──────────────────────────
# install 발행 의존성 — TaskService 가 본 resolver 를 생성자 인자로 받음.
# 설계:
#   - HEAD `http://{zdm_host}{zdm_package_path}` — ETag + Content-Length 추출
#   - Redis cache key = (host, etag) → hit 이면 cached sha256 반환
#   - miss 이면 GET stream + sha256 계산 + Redis set + 반환
#   - ZDM 패키지가 자주 안 바뀜 + ETag 가 invalidation 키라 cache TTL 길게 (6h default)
#   - fail-close — meta fetch 실패 시 publish 차단 (`ZdmPackageMetaError`)
#   - HEAD Content-Length 와 GET 실측 byte count 일치 검증 (ZDM 측 정합성 보장)
#   - Redis 자체는 fail-open (#C3) — Redis 장애 시 매번 GET full 로 fallback


class ZdmPackageMetaError(Exception):
    """router 가 HTTPException(503) 로 변환 — ZDM 메타 조회 실패."""


class BaseZdmPackageResolver(Protocol):
    async def resolve(self, zdm_host: str, package_path: str) -> tuple[str, int]:
        """ZDM 패키지의 (sha256_hex, size_bytes) 반환. 실패 시 raise.

        package_path = OS 별 path (caller 가 os_family 보고 결정). cache key 는 ETag 기반이라
        path 별 자동 분리.
        """


class HttpZdmPackageResolver:
    def __init__(self, http_client: httpx.AsyncClient, redis: Redis) -> None:
        self.http = http_client
        self.redis = redis

    async def resolve(self, zdm_host: str, package_path: str) -> tuple[str, int]:
        url = f"http://{zdm_host}{package_path}"

        # 1. HEAD — ETag + Content-Length
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

        # ETag 우선, 없으면 Last-Modified fallback. 둘 다 없으면 cache 키 안정성 깨지지만
        # 그 경우라도 매 publish 마다 fresh GET 으로 sha256 산출 → 동작은 정확.
        etag = head_resp.headers.get("ETag") or head_resp.headers.get("Last-Modified") or ""
        cache_key = web_settings.redis_key_zdm_package_sha256.format(zdm_host, etag) if etag else ""

        # 2. cache hit?
        if cache_key:
            cached = await safe_get(self.redis, cache_key)
            if cached:
                logger.info("zdm package meta cache hit host={} etag={}", zdm_host, etag)
                return cached, size_bytes

        # 3. miss — GET stream + sha256
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

        # 4. cache set (fail-open — Redis 장애 시 다음 publish 에서 다시 계산)
        if cache_key:
            await safe_set(self.redis, cache_key, sha256_hex, ex=web_settings.redis_ttl_zdm_package_sha256)

        return sha256_hex, size_bytes


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
        zdm_resolver: BaseZdmPackageResolver,
    ):
        self.query_repo = query_repo
        self.session_factory = session_factory
        self.collect_repo_factory = collect_repo_factory
        self.broker_channel = broker_channel
        self.zdm_resolver = zdm_resolver

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

        sha256 / size_bytes 는 publish 직전 ZDM 에서 동적 fetch (ETag cache).
        실패 시 publish 차단 → 503 (TaskNotConfigured).
        """
        sid_map = await self.query_repo.resolve_server_ids(target_public_ids)
        missing = [pid for pid in target_public_ids if pid not in sid_map]
        if missing:
            logger.warning("install target not found public_ids={}", missing[:5])
            raise TaskNotFound("선택한 서버 중 일부를 찾을 수 없어 전체 발행을 취소했습니다 (이미 삭제됐을 수 있음).")
        server_ids = [sid_map[pid] for pid in target_public_ids]
        details = await self.query_repo.get_servers(server_ids)
        detail_by_id = {d.id: d for d in details}

        # 직전 발행분 중 deadline 경과 pending 을 failure(timeout) 로 전이 후, 남은 활성 pending 보유 서버 사전 검증.
        # All-or-nothing — 하나라도 진행 중 작업이 있으면 전체 발행 취소(부분 발행 방지).
        async with self.session_factory() as session:
            repo = self.collect_repo_factory(session)
            expired = await repo.expire_overdue_tasks(server_ids)
            busy_ids = await repo.find_pending_deadline_servers(server_ids)
            await session.commit()
        if expired:
            logger.info("expired overdue tasks count={}", expired)
        if busy_ids:
            busy_names = sorted(detail_by_id[sid].hostname for sid in busy_ids if sid in detail_by_id)
            raise TaskDuplicatePending(
                f"이미 설치 작업이 진행 중인 서버가 있어 전체 발행을 취소했습니다: {', '.join(busy_names)}"
            )
        # 응답 마감 — 발행 시점 확정 (agent wall-clock + 네트워크 margin). 경과 시 표시 timeout + 다음 발행 시 expire.
        deadline_at = datetime.now(UTC) + timedelta(seconds=web_settings.install_timeout_sec + _DEADLINE_MARGIN_SEC)

        zdm_host = _extract_zdm_host(zdm_ip)
        # 엔진이 sha256/size 산출하려고 fetch 하는 호스트 = download.url·install args 와 동일 (real ZDM 직접 도달).
        resolve_host = zdm_host

        # OS family 별 ZDM 메타 fetch — batch 안 OS 섞이면 OS 별 1 회씩 (캐시 효과 + 정합성).
        # detail.os_family None (Linux agent minor bump 전) → fallback "linux".
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
                    raise TaskNotConfigured(
                        "ZDM 패키지 정보를 가져오지 못해 발행을 취소했습니다. ZDM 서버 연결을 확인하세요."
                    ) from e

        exchange = await self.broker_channel.get_exchange(diagnostic_settings.rabbitmq_task_exchange)

        created: list[TaskCreated] = []
        for public_id in target_public_ids:
            server_id = sid_map[public_id]
            detail = detail_by_id.get(server_id)
            if detail is None:
                logger.warning("install server detail missing public_id={}", public_id)
                raise TaskNotFound("서버 정보를 불러오지 못해 발행을 취소했습니다.")

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
                    # 사전 검증 통과 후 race(동시 발행) — 극히 드묾. 부분 발행 가능성은 T1 한계.
                    logger.warning("install race conflict server_id={} public_id={}", server_id, public_id)
                    raise TaskDuplicatePending(
                        f"발행 도중 다른 작업과 충돌했습니다: {detail.hostname}. 잠시 후 다시 시도하세요."
                    ) from e

                # publish-then-commit — 발행 성공 후에만 commit. 발행 실패 시 commit 하지 않고 async with 종료가
                # task INSERT 를 rollback -> "메시지 없는 유령 pending" 방지 (dual-write 갭 축소).
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
                    raise TaskPublishFailed(
                        f"작업 발행 중 broker 오류로 취소했습니다: {detail.hostname}. 잠시 후 다시 시도하세요."
                    ) from e
                await session.commit()

            logger.info(
                "task.install published task_id={} agent_id={} target={}",
                task_id,
                detail.agent_id,
                public_id,
            )
            created.append(TaskCreated(target_public_id=public_id, task_id=task_id))

        return created

    async def _ensure_machine_queue(self, agent_id: str) -> None:
        """원격 호스트 전용 큐 declare. idempotent — 동일 인자 재선언 안전.

        worker 측은 declare 권한이 없어 engine 이 책임진다.
        """
        queue_name = diagnostic_settings.agent_task_queue(agent_id)
        routing_key = diagnostic_settings.task_install_routing_key(agent_id)
        queue = await self.broker_channel.declare_queue(
            queue_name,
            durable=True,
            arguments={
                "x-message-ttl": _TASK_QUEUE_TTL_MS,
                "x-max-length": _TASK_QUEUE_MAX_LEN,
                "x-overflow": "reject-publish",
            },
        )
        exchange = await self.broker_channel.get_exchange(diagnostic_settings.rabbitmq_task_exchange)
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
        payload = {
            "message_type": "task.install",
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
                "timeout_sec": web_settings.install_timeout_sec,
            },
        }
        message = aio_pika.Message(
            body=json.dumps(payload).encode("utf-8"),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
            message_id=str(uuid.uuid4()),
        )
        routing_key = diagnostic_settings.task_install_routing_key(agent_id)
        await exchange.publish(message, routing_key=routing_key)


class TaskNotFound(Exception):
    """router 가 HTTPException(404) 로 변환."""


class TaskDuplicatePending(Exception):
    """router 가 HTTPException(409) 로 변환 — pending task 이미 존재."""


class TaskNotConfigured(Exception):
    """router 가 HTTPException(503) 로 변환 — ZDM 패키지 contract 미설정."""


class TaskPublishFailed(Exception):
    """router 가 HTTPException(503) 로 변환 — broker publish 실패 (task INSERT 는 rollback, 유령 pending 없음)."""
