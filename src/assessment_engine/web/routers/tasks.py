"""Tasks router — 운영자 task 발행 + 조회 endpoint.

책임: HTTP I/O 만. 비즈니스 로직(DB·broker publish·트랜잭션)은 TaskService / QueryService 에 위임 (F4).
"""

import ipaddress
import re
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from assessment_engine.web.deps import get_service, get_task_service
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.services.task_service import (
    TaskCreated,
    TaskDuplicatePending,
    TaskNotConfigured,
    TaskNotFound,
    TaskService,
)
from assessment_engine.web.settings import web_settings
from assessment_engine.web.view_models.task import TaskDetailItem, TaskSummaryItem

tasks_router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


_HOSTNAME_LABEL_RE = re.compile(r"^(?=.{1,63}$)[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$")


def _is_valid_hostname(v: str) -> bool:
    """RFC 952·1123 hostname/FQDN 검증 — label 1~63자 영숫자+hyphen, 전체 253자 이내."""
    if len(v) > 253:
        return False
    # trailing dot 허용 (FQDN canonical) — strip 후 label 검증.
    v = v.rstrip(".")
    if not v:
        return False
    return all(_HOSTNAME_LABEL_RE.match(label) for label in v.split("."))


class InstallRequest(BaseModel):
    target_public_ids: list[str] = Field(min_length=1, max_length=1000)
    # ZDM 좌표 — IP / hostname / HTTP(S) URL 모두 허용. RFC 3986 URL 권장 max=2048 cap.
    # 운영 환경에 따라 ZDM 을 IP·FQDN·HTTP endpoint 어느 형태로도 운영 가능 — 형식 강제 없음.
    zdm_ip: str | None = Field(default=None, max_length=2048)
    zdm_user: str | None = Field(
        default=None,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )

    @field_validator("zdm_ip")
    @classmethod
    def _validate_zdm_ip(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        # shell metachar / 공백 / 제어문자 무조건 차단 (#F8 — task params 그대로 task.install body 전파).
        if any(c in v for c in " \t\n\r;|&`$<>"):
            raise ValueError(f"invalid character in zdm target: {v!r}")
        # IP / hostname / URL 어느 형식이든 허용. 셋 다 실패 시 reject.
        try:
            ipaddress.ip_address(v)
            return v
        except ValueError:
            pass
        if _is_valid_hostname(v):
            return v
        if v.lower().startswith(("http://", "https://")):
            return v
        raise ValueError(f"invalid IP, hostname, or URL: {v}")


@tasks_router.post("/install", response_model=list[TaskCreated])
async def install(
    req: InstallRequest,
    service: TaskService = Depends(get_task_service),
):
    try:
        return await service.create_install_tasks(
            req.target_public_ids,
            zdm_ip=req.zdm_ip or web_settings.zdm_default_ip,
            zdm_user=req.zdm_user or web_settings.zdm_default_user,
        )
    except TaskNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except TaskDuplicatePending as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except TaskNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


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
