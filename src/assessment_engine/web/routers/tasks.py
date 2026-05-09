"""Tasks router — 운영자가 등록 서버에 원격 작업 발행 (ZConverter Install 등).

흐름: web INSERT → DB(이력) + Redis(`task:pending:{machine_id}` hot path) →
agent가 다음 metrics 발행 시 reply_to에 piggyback으로 task 수신 → 실행 → 결과를
`task.result` queue로 보고 → consumer가 DB UPDATE + Redis DEL.

Redis는 source of truth가 아닌 hot path 캐시 — 장애 시 DB 복구 가능.
"""
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from assessment_engine.config import web_settings
from assessment_engine.db.redis import get_redis, safe_set
from assessment_engine.db.repositories.collect_repository import CollectRepository
from assessment_engine.db.repositories.inbound import TaskCreate
from assessment_engine.db.repositories.query_repository import QueryRepository
from assessment_engine.db.session import AsyncSessionLocal
from assessment_engine.web.deps import get_service
from assessment_engine.web.services.query_service import QueryService


tasks_router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

# 양쪽(engine·agent)이 합의한 task_type enum. 새 task_type 도입 시 양쪽 동시 갱신.
TaskType = Literal["zconverter_install"]


class InstallRequest(BaseModel):
    """ZConverter Install 발행 — 다중 서버 일괄."""
    target_public_ids: list[str] = Field(min_length=1, max_length=1000)
    zdm_ip: str = Field(min_length=1, max_length=45)


class TaskCreated(BaseModel):
    target_public_id: str
    task_public_id: str


@tasks_router.post("/install", response_model=list[TaskCreated])
async def install(
    req: InstallRequest,
    service: QueryService = Depends(get_service),
    redis: Redis = Depends(get_redis),
):
    """선택 서버 N대에 ZConverter Install task 발행.

    각 서버마다: DB INSERT → Redis SET. 단일 트랜잭션 아님 (best-effort) — 부분 실패는
    응답에서 누락된 항목으로 운영자 인지. agent에 즉시 푸시 안 함 — 다음 metrics 주기에
    piggyback으로 자연스럽게 전달.
    """
    created: list[TaskCreated] = []
    for public_id in req.target_public_ids:
        server_id = await service.repo.resolve_server_id(public_id)
        if server_id is None:
            raise HTTPException(status_code=404, detail=f"server not found: {public_id}")

        # machine_id는 piggyback routing key. server_inventory에서 직접 조회.
        detail = await service.repo.get_server(server_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"server detail missing: {public_id}")

        async with AsyncSessionLocal() as session:
            repo = CollectRepository(session)
            task_public_id = await repo.create_task(TaskCreate(
                target_server_id=server_id,
                target_machine_id=detail.machine_id,
                task_type="zconverter_install",
                params={"zdm_ip": req.zdm_ip},
            ))
            await session.commit()

        # Redis hot path payload — agent에게 reply할 명령서 그대로.
        payload: dict[str, Any] = {
            "task_public_id": task_public_id,
            "task_type": "zconverter_install",
            "params": {"zdm_ip": req.zdm_ip},
        }
        await safe_set(
            redis,
            web_settings.redis_key_task_pending.format(detail.machine_id),
            json.dumps(payload),
            ex=web_settings.redis_ttl_task_pending,
        )
        logger.info("task installed public_id={} machine_id={} target={}",
                    task_public_id, detail.machine_id, public_id)
        created.append(TaskCreated(target_public_id=public_id, task_public_id=task_public_id))

    return created
