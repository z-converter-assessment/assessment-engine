"""back chain link 헬퍼 — 표시 규약은 `docs/reference/web/static-assets.md` "네비게이션 규약" 절."""

from urllib.parse import quote

from fastapi import Request


def safe_back(back: str | None, fallback: str) -> str:
    """back Query 검증 — 사이트 내부 경로만 허용. 값이 그대로 href 에 들어가므로 외부 유도를 막는다.

    두 번째 문자로 백슬래시도 막는 것은 브라우저가 URL 파싱에서 그것을 슬래시로 정규화하기 때문이다.
    """
    if back and back.startswith("/") and back[1:2] not in ("/", "\\"):
        return back
    return fallback


def self_back(request: Request) -> str:
    """본 페이지 URL — 자식 link 의 back chain 전달용 (URL-encoded)."""
    return quote(f"{request.url.path}?{request.url.query}", safe="")
