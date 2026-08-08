from typing import Any
from unittest.mock import AsyncMock, MagicMock

from redis.exceptions import RedisError

from assessment_engine.cache.redis import (
    safe_delete,
    safe_get,
    safe_incr_with_ttl,
    safe_mget,
    safe_set,
    safe_set_nx,
)


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
    redis = AsyncMock()
    redis.get.side_effect = RedisError("connection lost")
    assert await safe_get(redis, "k") is None


async def test_safe_set_returns_true_on_success():
    redis = AsyncMock()
    assert await safe_set(redis, "k", "v", ex=60) is True
    redis.set.assert_awaited_once_with("k", "v", ex=60)


async def test_safe_set_returns_false_on_redis_error():
    redis = AsyncMock()
    redis.set.side_effect = RedisError("oops")
    assert await safe_set(redis, "k", "v") is False


async def test_safe_set_nx_returns_true_on_first_write():
    redis = AsyncMock()
    redis.set.return_value = True
    assert await safe_set_nx(redis, "k", "1", 86400) is True
    redis.set.assert_awaited_once_with("k", "1", ex=86400, nx=True)


async def test_safe_set_nx_returns_false_on_duplicate():
    redis = AsyncMock()
    redis.set.return_value = None
    assert await safe_set_nx(redis, "k", "1", 86400) is False


async def test_safe_set_nx_returns_none_on_redis_error_for_failopen_decision():
    redis = AsyncMock()
    redis.set.side_effect = RedisError("redis down")
    assert await safe_set_nx(redis, "k", "1", 86400) is None


async def test_safe_delete_success():
    redis = AsyncMock()
    assert await safe_delete(redis, "k") is True


async def test_safe_delete_failopen():
    redis = AsyncMock()
    redis.delete.side_effect = RedisError("oops")
    assert await safe_delete(redis, "k") is False


async def test_safe_mget_empty_keys_short_circuit():
    redis = AsyncMock()
    assert await safe_mget(redis, []) == []
    redis.mget.assert_not_awaited()


async def test_safe_mget_returns_list_on_success():
    redis = AsyncMock()
    redis.mget.return_value = ["v1", None, "v3"]
    assert await safe_mget(redis, ["a", "b", "c"]) == ["v1", None, "v3"]
    redis.mget.assert_awaited_once_with(["a", "b", "c"])


async def test_safe_mget_returns_none_on_redis_error_for_fallback():
    redis = AsyncMock()
    redis.mget.side_effect = RedisError("oops")
    assert await safe_mget(redis, ["a", "b"]) is None


def _redis_with_pipe(
    *, execute_result: list[Any] | None = None, execute_error: BaseException | None = None
) -> tuple[AsyncMock, MagicMock]:
    pipe = MagicMock()
    pipe.incr = MagicMock()
    pipe.expire = MagicMock()
    if execute_error is not None:
        pipe.execute = AsyncMock(side_effect=execute_error)
    else:
        pipe.execute = AsyncMock(return_value=execute_result)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=pipe)
    cm.__aexit__ = AsyncMock(return_value=False)
    redis = MagicMock()
    redis.pipeline = MagicMock(return_value=cm)
    return redis, pipe


async def test_safe_incr_with_ttl_returns_count_and_sets_ttl():
    redis, pipe = _redis_with_pipe(execute_result=[3, True])
    assert await safe_incr_with_ttl(redis, "counter:agent", 900) == 3
    redis.pipeline.assert_called_once_with(transaction=True)
    pipe.incr.assert_called_once_with("counter:agent")
    pipe.expire.assert_called_once_with("counter:agent", 900)
    pipe.execute.assert_awaited_once_with()


async def test_safe_incr_with_ttl_coerces_result_to_int():
    redis, _pipe = _redis_with_pipe(execute_result=["7", True])
    result = await safe_incr_with_ttl(redis, "k", 60)
    assert result == 7
    assert isinstance(result, int)


async def test_safe_incr_with_ttl_returns_none_on_redis_error():
    redis, _pipe = _redis_with_pipe(execute_error=RedisError("redis down"))
    assert await safe_incr_with_ttl(redis, "k", 60) is None
