"""Tasks router — 운영자 task 발행 + 조회 endpoint.

책임: HTTP I/O 만. 비즈니스 로직(DB·broker publish·트랜잭션)은 TaskService / QueryService 에 위임 (F4).
"""

import ipaddress
import re
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from assessment_engine.web.deps import get_service, get_task_service
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.services.task_service import (
    TaskCreated,
    TaskDuplicatePendingError,
    TaskNotConfiguredError,
    TaskNotFoundError,
    TaskPublishFailedError,
    TaskService,
)
from assessment_engine.web.settings import get_web_settings
from assessment_engine.web.templating import templates
from assessment_engine.web.view_models.task import TaskDetailItem, TaskSummaryItem

tasks_router = APIRouter(prefix="/api/tasks", tags=["tasks"])


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


def _is_valid_host_or_host_port(v: str) -> bool:
    """IPv4 / hostname / FQDN (옵션 ":port") 검증.

    IPv6 (raw `::1` / bracket `[::1]:8000`) 는 reject — agent `download_url_extract_host`
    (download.c) 가 `':'` 를 host 종료 문자로 처리해 IPv6 bracket 형식을 못 다룸.
    IPv6 ZDM 좌표 운영은 agent 측 변경 필요 — 별도 결정.
    """
    if not v or v.startswith("["):
        return False
    # IPv6 raw — `:` 2회 이상 (IPv4:port 는 1회만)
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
        # 허용 형식:
        #   - IPv4               192.168.3.94
        #   - IPv4:port          192.168.3.94:8080
        #   - hostname / FQDN    zdm.example.com / zdm.example.com.
        #   - hostname:port      zdm.example.com:8000
        #   - http/https URL     http://zdm.example.com:8443/download/x.tar.gz
        # IPv6 (raw / bracket) 는 reject — agent download.c 한계.
        if v.lower().startswith(("http://", "https://")):
            return v
        if _is_valid_host_or_host_port(v):
            return v
        raise ValueError(f"invalid IP, hostname, host:port, or URL: {v}")


@tasks_router.post("/install", response_model=list[TaskCreated])
async def install(
    req: InstallRequest,
    service: TaskService = Depends(get_task_service),
):
    """N대 ZConverter 설치 발행 — 서버당 task 1건. zdm_ip/zdm_user 미지정 시 서버 기본값(ZDM_DEFAULT_*) 사용.

    이미 pending 인 서버가 있으면 409, 대상 서버 미존재는 404, ZDM 좌표 미해결(메타 조회 실패 등)은 503.
    """
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


@tasks_router.get("/{task_id}", response_model=TaskDetailItem)
async def get_task(
    task_id: UUID,
    service: QueryService = Depends(get_service),
):
    """단일 task 상세 — JSON. polling / list cell 갱신 callback 용."""
    detail = await service.get_task(str(task_id))
    if detail is None:
        raise HTTPException(status_code=404, detail="task not found")
    return detail


@tasks_router.get("/{task_id}/detail", response_class=HTMLResponse)
async def get_task_detail_fragment(
    task_id: UUID,
    request: Request,
    service: QueryService = Depends(get_service),
):
    """단일 task 상세 — HTML fragment. task-modal.js 가 fetch + innerHTML 교체 (P3 정공).

    JS HTML 합성 폐기 — server 가 template fragment render, JS 는 DOM 교체만.
    polling 흐름은 GET /api/tasks/{id} (JSON) 사용 — modal body 만 fragment.
    """
    detail = await service.get_task(str(task_id))
    if detail is None:
        raise HTTPException(status_code=404, detail="task not found")
    return templates.TemplateResponse(
        request=request,
        name="tasks/_detail.html",
        context={"detail": detail},
    )


@tasks_router.get("", response_model=list[TaskSummaryItem])
async def list_recent_tasks(
    server_public_id: UUID = Query(..., description="대상 서버 public_id (UUID)"),
    limit: int = Query(20, ge=1, le=100),
    cursor: datetime | None = Query(None, description="created_at < cursor 시간 역순 pagination (E2)"),
    service: QueryService = Depends(get_service),
) -> list[TaskSummaryItem]:
    """서버별 task 이력 — 시간 역순. cursor 기반 pagination (E2). 마지막 row의 created_at 을 다음 cursor로 사용."""
    return await service.list_recent_tasks(str(server_public_id), limit, cursor)
