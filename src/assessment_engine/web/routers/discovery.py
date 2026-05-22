"""Discovery router — 에이전트 자동 배포(추후) 워크플로우의 1단계 도달성 검사.

본 router의 역할: 대상 host(IP 또는 hostname) 입력 → HTTP probe로 "이 대상이 네트워크상 살아있나" 확인.
추후 SSH credential 등록 + ansible-playbook 실행 흐름이 이 위에 얹힘.

한계: HTTP 도달 ≠ SSH 도달. 1차 필터일 뿐. 폐쇄망에서 HTTP 80/443은 보통 열려있어
가벼운 도달성 검사로 적합. ICMP는 raw socket 권한 필요라 회피.
"""

import re
import time
from ipaddress import ip_address
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field, field_validator

discovery_router = APIRouter(prefix="/api/discovery", tags=["discovery"])

# probe 자체가 외부 네트워크 호출이므로 짧게. 폐쇄망 LAN 가정 — 5s면 충분.
_PROBE_TIMEOUT_S = 5.0


_HOSTNAME_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$",
)


class ProbeRequest(BaseModel):
    target: str = Field(min_length=1, max_length=253)  # RFC 1035 hostname max + IPv6 여유
    port: int = Field(default=80, ge=1, le=65535)
    scheme: Literal["http", "https"] = "http"

    @field_validator("target")
    @classmethod
    def validate_target(cls, v: str) -> str:
        # IPv4/IPv6 우선 시도 — 정석 IP면 그대로 통과.
        # 실패 시 hostname RFC 1035 패턴 매칭 (FQDN 포함, `host.docker.internal`·`engine.internal` 등 운영 시나리오).
        # SSRF 방지(localhost·metadata 차단)는 폐쇄망 가정상 추가 안 함 — 운영자 의도 입력.
        try:
            ip_address(v)
            return v
        except ValueError:
            pass
        if _HOSTNAME_RE.match(v):
            return v
        raise ValueError("target must be a valid IPv4/IPv6 or hostname")


class ProbeResponse(BaseModel):
    reachable: bool
    status_code: int | None = None
    elapsed_ms: int
    error: str | None = None


@discovery_router.post("/probe", response_model=ProbeResponse)
async def probe_http(req: ProbeRequest) -> ProbeResponse:
    """주어진 IP:port에 HTTP HEAD 요청 → 도달성 + 응답시간 + status code 반환.

    HEAD를 쓰는 이유: body 미수신으로 빠름 + 서버에 부하 작음. 단 일부 서버는 HEAD 미지원
    (405) — 그 경우 reachable=True (서버 응답 자체는 받음)로 분류, status_code=405 노출.

    실패 분류:
    - asyncio.TimeoutError: timeout
    - httpx.ConnectError: connection refused / host unreachable / DNS failure
    - 기타 네트워크 예외: 일반 error string

    DLQ 같은 후속 처리 없음 — 운영자가 즉시 결과 확인.
    """
    # IPv6는 URL에서 [...]로 감싸야 함. hostname·IPv4는 그대로.
    host = f"[{req.target}]" if ":" in req.target else req.target
    url = f"{req.scheme}://{host}:{req.port}/"

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S, follow_redirects=False) as client:
            resp = await client.head(url)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "probe success target={} port={} status={} elapsed_ms={}",
            req.target,
            req.port,
            resp.status_code,
            elapsed_ms,
        )
        return ProbeResponse(reachable=True, status_code=resp.status_code, elapsed_ms=elapsed_ms)
    except httpx.ConnectError as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info("probe unreachable target={} port={} err={}", req.target, req.port, e)
        return ProbeResponse(reachable=False, elapsed_ms=elapsed_ms, error="connection refused or host unreachable")
    except httpx.TimeoutException:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info("probe timeout target={} port={} timeout_s={}", req.target, req.port, _PROBE_TIMEOUT_S)
        return ProbeResponse(reachable=False, elapsed_ms=elapsed_ms, error=f"timeout after {_PROBE_TIMEOUT_S}s")
    except httpx.RequestError as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.warning("probe error target={} port={} err_type={}", req.target, req.port, type(e).__name__)
        raise HTTPException(status_code=500, detail="probe failed") from e
