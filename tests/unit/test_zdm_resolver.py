"""HttpZdmPackageResolver 단위 테스트.

httpx mock + redis mock 으로 cache hit/miss/HEAD 실패/size mismatch 경로 검증.
"""

import hashlib
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from assessment_engine.web.services.task_service import (
    HttpZdmPackageResolver,
    ZdmPackageMetaError,
)


def _head_response(status: int, headers: dict[str, str]) -> httpx.Response:
    return httpx.Response(status_code=status, headers=headers, request=httpx.Request("HEAD", "http://x/y"))


class _FakeStream:
    """async context manager mock for http_client.stream(method, url)."""

    def __init__(self, status: int, chunks: list[bytes]) -> None:
        self.status_code = status
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


def _make_resolver(http: MagicMock, redis: MagicMock) -> HttpZdmPackageResolver:
    return HttpZdmPackageResolver(http_client=http, redis=redis)


async def test_cache_hit_uses_cached_sha_without_get():
    body = b"hello-zdm-package"
    expected_size = len(body)
    etag = '"abc123-mtime"'

    http = MagicMock()
    http.head = AsyncMock(
        return_value=_head_response(
            200,
            {"ETag": etag, "Content-Length": str(expected_size)},
        )
    )
    http.stream = MagicMock()  # 호출되지 않아야 함

    redis = AsyncMock()
    redis.get.return_value = "deadbeef" * 8  # 64자 cached sha256 (실측 무관)

    resolver = _make_resolver(http, redis)
    sha, size = await resolver.resolve("192.168.3.94", "/download/ZConverter_CloudSource_Setup_Linux.tar.gz")

    assert sha == "deadbeef" * 8
    assert size == expected_size
    http.head.assert_awaited_once()
    http.stream.assert_not_called()


async def test_cache_miss_streams_get_and_computes_sha():
    body_chunks = [b"abc", b"def", b"ghi"]
    body = b"".join(body_chunks)
    expected_sha = hashlib.sha256(body).hexdigest()
    expected_size = len(body)
    etag = '"new-etag"'

    http = MagicMock()
    http.head = AsyncMock(
        return_value=_head_response(
            200,
            {"ETag": etag, "Content-Length": str(expected_size)},
        )
    )
    http.stream = MagicMock(return_value=_FakeStream(200, body_chunks))

    redis = AsyncMock()
    redis.get.return_value = None  # cache miss

    resolver = _make_resolver(http, redis)
    sha, size = await resolver.resolve("192.168.3.94", "/download/ZConverter_CloudSource_Setup_Linux.tar.gz")

    assert sha == expected_sha
    assert size == expected_size
    # cache 저장도 호출
    redis.set.assert_awaited_once()


async def test_head_non_200_raises():
    http = MagicMock()
    http.head = AsyncMock(return_value=_head_response(404, {}))
    redis = AsyncMock()

    resolver = _make_resolver(http, redis)
    with pytest.raises(ZdmPackageMetaError, match="HEAD status=404"):
        await resolver.resolve("192.168.3.94", "/download/ZConverter_CloudSource_Setup_Linux.tar.gz")


async def test_head_transport_error_raises():
    http = MagicMock()
    http.head = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    redis = AsyncMock()

    resolver = _make_resolver(http, redis)
    with pytest.raises(ZdmPackageMetaError, match="HEAD failed"):
        await resolver.resolve("192.168.3.94", "/download/ZConverter_CloudSource_Setup_Linux.tar.gz")


async def test_head_missing_content_length_raises():
    http = MagicMock()
    http.head = AsyncMock(return_value=_head_response(200, {"ETag": '"x"'}))
    redis = AsyncMock()

    resolver = _make_resolver(http, redis)
    with pytest.raises(ZdmPackageMetaError, match="missing Content-Length"):
        await resolver.resolve("192.168.3.94", "/download/ZConverter_CloudSource_Setup_Linux.tar.gz")


async def test_size_mismatch_between_head_and_get_raises():
    body_chunks = [b"abcd"]  # 4 bytes
    declared_size = 10  # HEAD 에는 10 명시 → 불일치

    http = MagicMock()
    http.head = AsyncMock(
        return_value=_head_response(
            200,
            {"ETag": '"x"', "Content-Length": str(declared_size)},
        )
    )
    http.stream = MagicMock(return_value=_FakeStream(200, body_chunks))

    redis = AsyncMock()
    redis.get.return_value = None

    resolver = _make_resolver(http, redis)
    with pytest.raises(ZdmPackageMetaError, match="size mismatch"):
        await resolver.resolve("192.168.3.94", "/download/ZConverter_CloudSource_Setup_Linux.tar.gz")


async def test_etag_fallback_to_last_modified():
    body = b"x"
    http = MagicMock()
    http.head = AsyncMock(
        return_value=_head_response(
            200,
            {"Last-Modified": "Mon, 04 May 2026 00:58:29 GMT", "Content-Length": "1"},
        )
    )
    http.stream = MagicMock(return_value=_FakeStream(200, [body]))
    redis = AsyncMock()
    redis.get.return_value = None

    resolver = _make_resolver(http, redis)
    sha, size = await resolver.resolve("192.168.3.94", "/download/ZConverter_CloudSource_Setup_Linux.tar.gz")

    assert sha == hashlib.sha256(body).hexdigest()
    assert size == 1
    # Last-Modified 도 cache key 에 박혀 set 호출
    redis.set.assert_awaited_once()


async def test_no_etag_no_last_modified_skips_cache_but_still_works():
    """ETag·Last-Modified 둘 다 없으면 cache 키 비어 — get/set 모두 skip, GET 으로 계산만."""
    body = b"y"
    http = MagicMock()
    http.head = AsyncMock(
        return_value=_head_response(
            200,
            {"Content-Length": "1"},
        )
    )
    http.stream = MagicMock(return_value=_FakeStream(200, [body]))
    redis = AsyncMock()

    resolver = _make_resolver(http, redis)
    sha, size = await resolver.resolve("192.168.3.94", "/download/ZConverter_CloudSource_Setup_Linux.tar.gz")

    assert sha == hashlib.sha256(body).hexdigest()
    assert size == 1
    redis.get.assert_not_awaited()
    redis.set.assert_not_awaited()
