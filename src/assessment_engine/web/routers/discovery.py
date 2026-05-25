"""Discovery router — 에이전트 자동 배포(추후) 워크플로우의 1단계 SSH 도달성 검사.

본 router의 역할: 대상 host(IP 또는 hostname) 입력 → SSH 포트(기본 22)에 TCP connect로
"이 대상에 SSH 가 listen 하나" 확인. 추후 SSH credential 등록 + ansible-playbook 실행 흐름이
이 위에 얹힘 (install task 는 MQ 기반 별개 채널 — agent 가 이미 떠 있는 host 대상).

검사 수준:
- TCP connect 성공 = 포트 listen = SSH 도달 가능.
- 추가로 SSH 서버가 RFC 4253 대로 연결 직후 보내는 identification string(`SSH-2.0-...`)을
  best-effort 로 읽어 "22 에 진짜 SSH 가 떴는지" 식별 + 버전 노출. banner 부재여도 포트만 열렸으면 reachable.

한계: 포트 listen != 로그인 가능. credential 검증은 ansible ping module(다음 단계) 책임.
포트 노킹/fail2ban 앞단이면 banner 미수신. ICMP 는 raw socket 권한 필요라 회피 (TCP connect 는 불필요).
"""

import asyncio
import re
import time
from ipaddress import ip_address

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field, field_validator

discovery_router = APIRouter(prefix="/api/discovery", tags=["discovery"])

# probe 자체가 외부 네트워크 호출이므로 짧게. 폐쇄망 LAN 가정.
# connect = TCP 3-way handshake 도달성, banner = connect 성공 후 SSH 식별 문자열 read (best-effort, 더 짧게).
_CONNECT_TIMEOUT_S = 5.0
_BANNER_TIMEOUT_S = 2.0
_BANNER_MAX_BYTES = 255  # RFC 4253 — SSH identification string 한 줄 최대 길이.


_HOSTNAME_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$",
)


class ProbeRequest(BaseModel):
    target: str = Field(min_length=1, max_length=253)  # RFC 1035 hostname max + IPv6 여유
    port: int = Field(default=22, ge=1, le=65535)  # SSH 기본 포트. 비표준 SSH 포트 환경은 override.

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
    banner: str | None = None  # SSH identification string (`SSH-2.0-...`) — 식별 가능 시.
    elapsed_ms: int
    error: str | None = None


@discovery_router.post("/probe", response_model=ProbeResponse)
async def probe_ssh(req: ProbeRequest) -> ProbeResponse:
    """주어진 host:port에 TCP connect → SSH 도달성 + 응답시간 + SSH banner 반환.

    connect 성공 = 포트 listen = reachable=True. connect 직후 SSH banner 를 best-effort read
    (RFC 4253 — 서버가 먼저 전송). banner 미수신(포트 노킹·non-SSH 서비스)이어도 reachable 유지.

    실패 분류:
    - asyncio.TimeoutError: connect timeout (방화벽 drop·느린 네트워크)
    - ConnectionRefusedError: 포트 닫힘 (RST 응답)
    - OSError(그 외): DNS 실패·no route to host

    DLQ 같은 후속 처리 없음 — 운영자가 즉시 결과 확인.
    """
    started = time.monotonic()

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(req.target, req.port),
            timeout=_CONNECT_TIMEOUT_S,
        )
    except TimeoutError:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info("probe timeout target={} port={} timeout_s={}", req.target, req.port, _CONNECT_TIMEOUT_S)
        return ProbeResponse(reachable=False, elapsed_ms=elapsed_ms, error=f"timeout after {_CONNECT_TIMEOUT_S}s")
    except ConnectionRefusedError:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info("probe refused target={} port={}", req.target, req.port)
        return ProbeResponse(reachable=False, elapsed_ms=elapsed_ms, error="connection refused (port closed)")
    except OSError as e:
        # DNS 실패·no route to host 등. errno 로 세분화하지 않고 운영자에게 일반 사유만 노출.
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info("probe unreachable target={} port={} err_type={}", req.target, req.port, type(e).__name__)
        return ProbeResponse(reachable=False, elapsed_ms=elapsed_ms, error="host unreachable or DNS failure")

    # connect 성공 → 포트 listen 확정. SSH banner 는 best-effort (실패해도 reachable 유지).
    banner: str | None = None
    try:
        raw = await asyncio.wait_for(reader.read(_BANNER_MAX_BYTES), timeout=_BANNER_TIMEOUT_S)
        text = raw.decode("latin-1", errors="replace").strip()
        if text:
            banner = text
    except OSError:
        # banner read timeout(TimeoutError 는 OSError 하위) 또는 read 오류 —
        # non-SSH 서비스·포트 노킹·지연. 포트는 열렸으니 reachable 은 유지.
        pass
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=_BANNER_TIMEOUT_S)
        except OSError:
            pass

    elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "probe reachable target={} port={} ssh_banner={} elapsed_ms={}",
        req.target,
        req.port,
        bool(banner),
        elapsed_ms,
    )
    return ProbeResponse(reachable=True, banner=banner, elapsed_ms=elapsed_ms)
