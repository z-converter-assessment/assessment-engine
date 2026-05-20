"""Redis safe_* helper 단위 테스트.

평시: 정상 redis 응답을 그대로 또는 True/False로 반환.
장애: RedisError 발생 시 silent fallback (None/False/[]).

특별 케이스:
- safe_set_nx: True/False/None — None은 호출자(_check_idempotent)가 fail-open 판단
- safe_mget: keys=[]면 redis 호출 없이 즉시 [] (short-circuit)
"""

from unittest.mock import AsyncMock

import pytest
from redis.exceptions import RedisError

from assessment_engine.cache.redis import (
    safe_delete,
    safe_get,
    safe_mget,
    safe_publish,
    safe_set,
    safe_set_nx,
)

pytestmark = pytest.mark.asyncio


# ─── safe_get ─────────────────────────────────────────────────────────────


async def test_safe_get_returns_value_on_hit():
    redis = AsyncMock()
    redis.get.return_value = "v"
    assert await safe_get(redis, "k") == "v"
    redis.get.assert_awaited_once_with("k")


async def test_safe_get_returns_none_on_miss():
    redis = AsyncMock()
    redis.get.return_value = None
    assert await safe_get(redis, "k") is None


async def test_safe_get_returns_none_on_redis_error():
    """fail-open: RedisError → None. 호출자는 cache miss와 동일하게 DB로 fallback."""
    redis = AsyncMock()
    redis.get.side_effect = RedisError("connection lost")
    assert await safe_get(redis, "k") is None


# ─── safe_set ─────────────────────────────────────────────────────────────


async def test_safe_set_returns_true_on_success():
    redis = AsyncMock()
    assert await safe_set(redis, "k", "v", ex=60) is True
    redis.set.assert_awaited_once_with("k", "v", ex=60)


async def test_safe_set_returns_false_on_redis_error():
    redis = AsyncMock()
    redis.set.side_effect = RedisError("oops")
    assert await safe_set(redis, "k", "v") is False


# ─── safe_set_nx — 멱등성 1단, fail-open은 호출자 결정 ─────────────────────


async def test_safe_set_nx_returns_true_on_first_write():
    redis = AsyncMock()
    redis.set.return_value = True  # NX 성공 (첫 처리)
    assert await safe_set_nx(redis, "k", "1", 86400) is True
    redis.set.assert_awaited_once_with("k", "1", ex=86400, nx=True)


async def test_safe_set_nx_returns_false_on_duplicate():
    redis = AsyncMock()
    redis.set.return_value = None  # NX 실패 (이미 존재)
    assert await safe_set_nx(redis, "k", "1", 86400) is False


async def test_safe_set_nx_returns_none_on_redis_error_for_failopen_decision():
    """RedisError → None. _check_idempotent가 True로 간주(처리 진행) → DB UNIQUE(2단)이 흡수."""
    redis = AsyncMock()
    redis.set.side_effect = RedisError("redis down")
    assert await safe_set_nx(redis, "k", "1", 86400) is None


# ─── safe_delete ──────────────────────────────────────────────────────────


async def test_safe_delete_success():
    redis = AsyncMock()
    assert await safe_delete(redis, "k") is True


async def test_safe_delete_failopen():
    redis = AsyncMock()
    redis.delete.side_effect = RedisError("oops")
    assert await safe_delete(redis, "k") is False


# ─── safe_mget ────────────────────────────────────────────────────────────


async def test_safe_mget_empty_keys_short_circuit():
    """keys=[]면 redis 호출 없이 즉시 [] — 라운드트립 절감."""
    redis = AsyncMock()
    assert await safe_mget(redis, []) == []
    redis.mget.assert_not_awaited()


async def test_safe_mget_returns_list_on_success():
    redis = AsyncMock()
    redis.mget.return_value = ["v1", None, "v3"]
    assert await safe_mget(redis, ["a", "b", "c"]) == ["v1", None, "v3"]
    redis.mget.assert_awaited_once_with(["a", "b", "c"])


async def test_safe_mget_returns_none_on_redis_error_for_fallback():
    """RedisError → None. list_servers가 last_seen_at fallback 경로 선택."""
    redis = AsyncMock()
    redis.mget.side_effect = RedisError("oops")
    assert await safe_mget(redis, ["a", "b"]) is None


# ─── safe_publish ─────────────────────────────────────────────────────────


async def test_safe_publish_success():
    redis = AsyncMock()
    assert await safe_publish(redis, "ch", "msg") is True
    redis.publish.assert_awaited_once_with("ch", "msg")


async def test_safe_publish_failopen():
    redis = AsyncMock()
    redis.publish.side_effect = RedisError("oops")
    assert await safe_publish(redis, "ch", "msg") is False
