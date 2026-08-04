"""back chain link 헬퍼 — 표시 규약은 `docs/reference/web/static-assets.md` "네비게이션 규약" 절."""

from typing import Annotated
from urllib.parse import quote

from fastapi import Query, Request
from pydantic import AfterValidator

# 브라우저는 URL 을 파싱할 때 tab·LF·CR 을 지우고 백슬래시를 슬래시로 바꾼다. 판정 전에 같은 정규화를
# 거치지 않으면 `/<TAB>/evil.com` 이 검사를 통과한 뒤 `//evil.com` 으로 해석된다.
_URL_STRIPPED = {0x09: None, 0x0A: None, 0x0D: None}


def _sanitize(
    back: str | None,
) -> str | None:
    """back Query 정규화 — 사이트 내부 경로가 아니면 None. 값이 그대로 href 에 들어간다."""
    if not back:
        return None
    normalized = back.translate(_URL_STRIPPED).replace("\\", "/")
    if not normalized.startswith("/") or normalized.startswith("//"):
        return None
    return normalized


BackUrl = Annotated[
    str | None,
    Query(description="이전 link referrer. 미명시 시 라우터별 기본 목적지"),
    AfterValidator(_sanitize),
]
"""back Query 파라미터 타입 — 라우터가 이것을 쓰면 검증이 시그니처에서 강제된다."""


def safe_back(
    back: str | None,
    fallback: str,
) -> str:
    """BackUrl 이 걸러낸 값에 기본 목적지를 씌운다."""
    return _sanitize(back) or fallback


def self_back(
    request: Request,
) -> str:
    """본 페이지 URL — 자식 link 의 back chain 전달용 (URL-encoded)."""
    return quote(f"{request.url.path}?{request.url.query}", safe="")
