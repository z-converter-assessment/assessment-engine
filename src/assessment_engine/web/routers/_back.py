"""back chain link 헬퍼 — 표시 규약은 `docs/reference/web/static-assets.md` "네비게이션 규약" 절."""

from typing import Annotated
from urllib.parse import quote

from fastapi import Query, Request
from pydantic import AfterValidator

_URL_STRIPPED = {0x09: None, 0x0A: None, 0x0D: None}


def _sanitize(
    back: str | None,
) -> str | None:
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
    return _sanitize(back) or fallback


def self_back_of(
    path: str,
    query: str = "",
) -> str:
    return quote(f"{path}?{query}" if query else path, safe="")


def self_back(
    request: Request,
) -> str:
    return quote(f"{request.url.path}?{request.url.query}", safe="")
