"""Tasks router — 운영자 task 발행 + 조회 endpoint.

책임: HTTP I/O 만. 비즈니스 로직(DB·broker publish·트랜잭션)은 TaskService / QueryService 에 위임 (F4).
"""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from assessment_engine.web.deps import get_service, get_task_service
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.services.task_service import (
    TaskCreated,
    TaskDuplicatePending,
    TaskNotFound,
    TaskService,
)
from assessment_engine.web.view_models import TaskDetailItem, TaskSummaryItem

tasks_router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class InstallRequest(BaseModel):
    target_public_ids: list[str] = Field(min_length=1, max_length=1000)


@tasks_router.post("/install", response_model=list[TaskCreated])
async def install(
    req: InstallRequest,
    service: TaskService = Depends(get_task_service),
):
    try:
        return await service.create_install_tasks(req.target_public_ids)
    except TaskNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except TaskDuplicatePending as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@tasks_router.get("/{task_id}", response_model=TaskDetailItem)
async def get_task(
    task_id: UUID,
    service: QueryService = Depends(get_service),
):
    """단일 task 상세 — modal / 자동화. status / failure_reason / exit_code / tails."""
    detail = await service.get_task(str(task_id))
    if detail is None:
        raise HTTPException(status_code=404, detail="task not found")
    return detail


@tasks_router.get("", response_model=list[TaskSummaryItem])
async def list_recent_tasks(
    server_public_id: UUID = Query(..., description="대상 서버 public_id (UUID)"),
    limit: int = Query(20, ge=1, le=100),
    cursor: datetime | None = Query(None, description="created_at < cursor 시간 역순 pagination (E2)"),
    service: QueryService = Depends(get_service),
) -> list[TaskSummaryItem]:
    """서버별 task 이력 — 시간 역순. cursor 기반 pagination (E2). 마지막 row의 created_at 을 다음 cursor로 사용."""
    return await service.list_recent_tasks(str(server_public_id), limit, cursor)
