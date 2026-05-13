"""Task 발행 service — 운영자가 등록 서버에 원격 작업 명령을 발행.

책임 경계:
- DB INSERT (이력) + Redis SET (hot path) 캡슐화 — router는 service만 호출
- 추상 `BaseCollectRepository`만 의존 (F4) — composition root에서 구체 주입
- 트랜잭션 경계는 service가 관리 (router는 transaction 모름)

흐름: web POST → service.create_install_tasks → DB INSERT + Redis SET → 응답.
agent는 다음 metrics 발행 시 RPC piggyback으로 명령 수신 (consumer/handler 측).
"""
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.config import web_settings
from assessment_engine.db.redis import safe_set
from assessment_engine.db.repositories.base_collect_repository import BaseCollectRepository
from assessment_engine.db.repositories.base_query_repository import BaseQueryRepository
from assessment_engine.db.repositories.inbound import TaskCreate


@dataclass
class TaskCreated:
    target_public_id: str
    task_public_id: str


class TaskService:
    def __init__(
        self,
        query_repo: BaseQueryRepository,
        session_factory: async_sessionmaker[AsyncSession],
        collect_repo_factory: Callable[[AsyncSession], BaseCollectRepository],
        redis: Redis,
    ):
        self.query_repo = query_repo
        self.session_factory = session_factory
        self.collect_repo_factory = collect_repo_factory
        self.redis = redis

    async def create_install_tasks(
        self,
        target_public_ids: list[str],
        source_url: str,
    ) -> list[TaskCreated]:
        """선택 서버 N대에 ZConverter Install task 발행.

        params.source_url은 agent가 `curl {source_url}`로 그대로 fetch하는 전체 URL.
        host:port 뿐 아니라 scheme(http/https)·path·외부 mirror 자유 (#B5 agent 계약).

        best-effort — 부분 실패는 응답 누락으로 운영자가 인지. 트랜잭션 단일이 아님 (서버별
        독립). 미존재 public_id는 즉시 raise (운영자가 stale UI 인지).
        """
        # C5 N+1 회피: resolve + get_server를 batch SQL 2회로. INSERT는 서버별 트랜잭션 분리 유지.
        sid_map = await self.query_repo.resolve_server_ids(target_public_ids)
        missing = [pid for pid in target_public_ids if pid not in sid_map]
        if missing:
            raise TaskNotFound(f"server not found: {','.join(missing[:5])}")
        server_ids = [sid_map[pid] for pid in target_public_ids]
        details = await self.query_repo.get_servers(server_ids)
        detail_by_id = {d.id: d for d in details}

        created: list[TaskCreated] = []
        for public_id in target_public_ids:
            server_id = sid_map[public_id]
            detail = detail_by_id.get(server_id)
            if detail is None:
                raise TaskNotFound(f"server detail missing: {public_id}")

            async with self.session_factory() as session:
                repo = self.collect_repo_factory(session)
                try:
                    task_public_id = await repo.create_task(TaskCreate(
                        target_server_id=server_id,
                        target_machine_id=detail.machine_id,
                        task_type="zconverter_install",
                        params={"source_url": source_url},
                    ))
                    await session.commit()
                except IntegrityError as e:
                    # 부분 UNIQUE(uq_tasks_pending_per_server_type) 위반 — 같은 server에
                    # 같은 task_type pending 이미 존재. 운영자 더블클릭 방어.
                    raise TaskDuplicatePending(
                        f"pending task already exists for {public_id} (zconverter_install)"
                    ) from e

            payload: dict[str, Any] = {
                "task_public_id": task_public_id,
                "task_type": "zconverter_install",
                "params": {"source_url": source_url},
            }
            await safe_set(
                self.redis,
                web_settings.redis_key_task_pending.format(detail.machine_id),
                json.dumps(payload),
                ex=web_settings.redis_ttl_task_pending,
            )
            logger.info("task installed public_id={} machine_id={} target={}",
                        task_public_id, detail.machine_id, public_id)
            created.append(TaskCreated(target_public_id=public_id, task_public_id=task_public_id))

        return created


class TaskNotFound(Exception):
    """router가 HTTPException(404)로 변환."""


class TaskDuplicatePending(Exception):
    """router가 HTTPException(409)로 변환 — pending task 이미 존재."""
