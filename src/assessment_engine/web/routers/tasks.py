"""Tasks router — 운영자가 등록 서버에 원격 작업 명령 발행.

책임: HTTP I/O만. 비즈니스 로직(DB·Redis·트랜잭션)은 TaskService에 위임 (F4).
정의·근거: CLAUDE.md B5 (RPC piggyback) + docs/architecture/agent.md "Task RPC piggyback" 절.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from assessment_engine.web.deps import get_task_service
from assessment_engine.web.services.task_service import (
    TaskCreated,
    TaskService,
    _DuplicatePending,
    _NotFound,
)

tasks_router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class InstallRequest(BaseModel):
    target_public_ids: list[str] = Field(min_length=1, max_length=1000)
    # agent가 `curl {source_url}`로 그대로 fetch하는 전체 URL — scheme·host·port·path 자유.
    # 본 엔진 self-host 예: "http://host.lima.internal:8000/zconverter.tar.gz".
    # 외부 mirror 예: "https://mirror.example.com/dist/zconverter-v1.tar.gz".
    source_url: str = Field(min_length=1, max_length=2048)


@tasks_router.post("/install", response_model=list[TaskCreated])
async def install(
    req: InstallRequest,
    service: TaskService = Depends(get_task_service),
):
    try:
        return await service.create_install_tasks(req.target_public_ids, req.source_url)
    except _NotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except _DuplicatePending as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
