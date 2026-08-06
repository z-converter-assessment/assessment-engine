"""Redis safe_* helper 단위 테스트.

평시: 정상 redis 응답을 그대로 또는 True/False로 반환.
장애: RedisError 발생 시 silent fallback (None/False/[]).

특별 케이스:
- safe_set_nx: True/False/None — None은 호출자(_check_idempotent)가 fail-open 판단
- safe_mget: keys=[]면 redis 호출 없이 즉시 [] (short-circuit)
"""

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

# --- safe_get -------------------------------------------------------------


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


# --- safe_set -------------------------------------------------------------


async def test_safe_set_returns_true_on_success():
    redis = AsyncMock()
    assert await safe_set(redis, "k", "v", ex=60) is True
    redis.set.assert_awaited_once_with("k", "v", ex=60)


async def test_safe_set_returns_false_on_redis_error():
    redis = AsyncMock()
    redis.set.side_effect = RedisError("oops")
    assert await safe_set(redis, "k", "v") is False


# --- safe_set_nx — 멱등성 1단, fail-open은 호출자 결정 ---------------------


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


# --- safe_delete ----------------------------------------------------------


async def test_safe_delete_success():
    redis = AsyncMock()
    assert await safe_delete(redis, "k") is True


async def test_safe_delete_failopen():
    redis = AsyncMock()
    redis.delete.side_effect = RedisError("oops")
    assert await safe_delete(redis, "k") is False


# --- safe_mget ------------------------------------------------------------


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


# --- safe_incr_with_ttl — 슬라이딩 윈도우 카운터 (INCR + EXPIRE 원자) --------


def _redis_with_pipe(
    *, execute_result: list[Any] | None = None, execute_error: BaseException | None = None
) -> tuple[AsyncMock, MagicMock]:
    """redis.pipeline(transaction=True) as pipe async context manager mock 구성.

    pipe.incr/expire는 명령 큐잉(동기), pipe.execute()는 await되어 results 반환.
    반환: (redis mock, pipe mock) — 호출 인자 검증용.
    """
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
    """정상: INCR 결과(results[0]) int 반환 + EXPIRE ttl 갱신. transaction=True 원자."""
    redis, pipe = _redis_with_pipe(execute_result=[3, True])
    assert await safe_incr_with_ttl(redis, "counter:agent", 900) == 3
    redis.pipeline.assert_called_once_with(transaction=True)
    pipe.incr.assert_called_once_with("counter:agent")
    pipe.expire.assert_called_once_with("counter:agent", 900)
    pipe.execute.assert_awaited_once_with()


async def test_safe_incr_with_ttl_coerces_result_to_int():
    """results[0]가 문자열이어도 int로 강제 변환해 반환."""
    redis, _pipe = _redis_with_pipe(execute_result=["7", True])
    result = await safe_incr_with_ttl(redis, "k", 60)
    assert result == 7
    assert isinstance(result, int)


async def test_safe_incr_with_ttl_returns_none_on_redis_error():
    """fail-open: RedisError → None. 호출자는 카운터 미판독으로 간주 (alert는 다음 기회)."""
    redis, _pipe = _redis_with_pipe(execute_error=RedisError("redis down"))
    assert await safe_incr_with_ttl(redis, "k", 60) is None
