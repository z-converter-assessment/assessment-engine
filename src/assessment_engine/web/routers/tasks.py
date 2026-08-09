"""Tasks router — 운영자 task 발행 + 조회 endpoint. HTTP I/O 만 하고 나머지는 서비스 계층에 위임한다."""

import ipaddress
import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from assessment_engine.web.deps import QueryServiceDep, TaskServiceDep
from assessment_engine.web.services.task_service import (
    TaskCreated,
    TaskDuplicatePendingError,
    TaskNotConfiguredError,
    TaskNotFoundError,
    TaskPublishFailedError,
)
from assessment_engine.web.settings import get_web_settings
from assessment_engine.web.templating import templates
from assessment_engine.web.view_models.task import TaskDetailItem, TaskSummaryItem

tasks_router = APIRouter(prefix="/api/tasks", tags=["tasks"])


_HOSTNAME_LABEL_RE = re.compile(r"^(?=.{1,63}$)[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$")


def _is_valid_hostname(
    v: str,
) -> bool:
    if len(v) > 253:
        return False

    v = v.rstrip(".")
    if not v:
        return False
    return all(_HOSTNAME_LABEL_RE.match(label) for label in v.split("."))


def _is_valid_host_or_host_port(
    v: str,
) -> bool:
    """IPv4 / hostname / FQDN (옵션 ":port") 검증.

    IPv6 (raw `::1` / bracket `[::1]:8000`) 는 reject — agent `download_url_extract_host`
    (download.c) 가 `':'` 를 host 종료 문자로 처리해 IPv6 bracket 형식을 못 다룸.
    IPv6 ZDM 좌표 운영은 agent 측 변경 필요 — 별도 결정.
    """
    if not v or v.startswith("["):
        return False

    if v.count(":") >= 2:
        return False

    if ":" in v:
        host, _, port_s = v.rpartition(":")
        if not host or not port_s.isdigit():
            return False
        port = int(port_s)
        if not (1 <= port <= 65535):
            return False
    else:
        host = v

    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        return _is_valid_hostname(host)
    else:
        return True


class InstallRequest(BaseModel):
    target_public_ids: list[str] = Field(min_length=1, max_length=1000)
    # 허용 값 공간은 docs/reference/contracts/env.md "ZDM 좌표 값 공간". max=2048 은 RFC 3986 권장 URL 상한.
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

        if any(c in v for c in " \t\n\r;|&`$<>"):
            raise ValueError(f"invalid character in zdm target: {v!r}")
        if v.lower().startswith(("http://", "https://")):
            return v
        if _is_valid_host_or_host_port(v):
            return v
        raise ValueError(f"invalid IP, hostname, host:port, or URL: {v}")


@tasks_router.post("/install")
async def install(
    req: InstallRequest,
    service: TaskServiceDep,
) -> list[TaskCreated]:
    try:
        return await service.create_install_tasks(
            req.target_public_ids,
            zdm_ip=req.zdm_ip or get_web_settings().zdm_default_ip,
            zdm_user=req.zdm_user or get_web_settings().zdm_default_user,
        )
    except TaskNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except TaskDuplicatePendingError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except TaskNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except TaskPublishFailedError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@tasks_router.get("/{task_id}")
async def get_task(
    task_id: UUID,
    service: QueryServiceDep,
) -> TaskDetailItem:
    detail = await service.get_task(str(task_id))
    if detail is None:
        raise HTTPException(status_code=404, detail="task not found")
    return detail


@tasks_router.get("/{task_id}/detail", response_class=HTMLResponse)
async def get_task_detail_fragment(
    task_id: UUID,
    request: Request,
    service: QueryServiceDep,
):
    detail = await service.get_task(str(task_id))
    if detail is None:
        raise HTTPException(status_code=404, detail="task not found")
    return templates.TemplateResponse(
        request=request,
        name="tasks/_detail.html",
        context={"detail": detail},
    )


@tasks_router.get("")
async def list_recent_tasks(
    service: QueryServiceDep,
    server_public_id: Annotated[UUID, Query(description="대상 서버 public_id (UUID)")],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[datetime | None, Query(description="created_at < cursor 시간 역순 pagination (E2)")] = None,
) -> list[TaskSummaryItem]:
    return await service.list_recent_tasks(str(server_public_id), limit, cursor)
