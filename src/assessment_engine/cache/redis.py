"""Redis 연결 풀 + `safe_*` helper — 모든 Redis 호출의 유일한 통로 (#C3).

helper 를 거치는 이유는 fail-open 이다. `RedisError` 를 여기서 흡수하고 호출자에게 None/False 를 주면
캐시 장애가 요청 실패로 번지지 않는다. 직접 client 를 부르면 그 보장이 그 지점에서만 사라진다.
"""

from functools import cache
from typing import cast

from loguru import logger
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import RedisError

from assessment_engine.config import WebSettings

# 캐시 계층은 모든 컴포넌트 공통이라 자체 WebSettings 를 만든다 (session.py 와 동일).
# 첫 get_pool 호출 시점에 만든다 — import 만으로 설정을 요구하지 않는다.


@cache
def get_pool() -> ConnectionPool:
    # redis 는 from_url 의 **kwargs 를 타입 없이 선언한다 — 인자 이름별 검증이 성립하지 않는다.
    return ConnectionPool.from_url(  # pyright: ignore[reportUnknownMemberType]
        WebSettings().redis_url,  # pyright: ignore[reportCallIssue]
        decode_responses=True,
        # 명령 timeout 이 없으면 서버 무응답이 RedisError 로 바뀌지 않아 fail-open 이 걸리지 않는다.
        socket_timeout=5,
        socket_connect_timeout=3,
        # 방화벽·서버가 idle 소켓을 끊는다 — 사용 직전 PING 으로 죽은 소켓을 걸러 spurious
        # ConnectionResetError 가 fail-open 캐시미스로 새는 것을 막는다. keepalive 는 TCP dead-peer 감지.
        health_check_interval=30,
        socket_keepalive=True,
        max_connections=50,  # 소켓 고갈 상한 (기본 무제한)
    )


def get_redis() -> Redis:
    return Redis(connection_pool=get_pool())


async def close_pool() -> None:
    if get_pool.cache_info().currsize:
        await get_pool().disconnect()
        get_pool.cache_clear()


# 장애 시 동작 매트릭스는 docs/reference/redis.md "장애 시 동작".


async def safe_get(redis: Redis, key: str) -> str | None:
    try:
        # 풀이 decode_responses=True 라 응답은 str 이다. redis 8 은 그 설정을 타입에 반영하지 않는다.
        return cast("str | None", await redis.get(key))
    except RedisError as e:
        logger.warning("redis get failed key={} err={}", key, e)
        return None


async def safe_set(redis: Redis, key: str, value: str, ex: int | None = None) -> bool:
    try:
        await redis.set(key, value, ex=ex)
    except RedisError as e:
        logger.warning("redis set failed key={} err={}", key, e)
        return False
    else:
        return True


async def safe_set_nx(redis: Redis, key: str, value: str, ex: int) -> bool | None:
    """원자적 SET NX — True=첫 처리, False=중복, None=Redis 장애.

    None 이면 호출자가 처리를 진행하고 DB UNIQUE 제약(2단)이 중복 INSERT 를 흡수한다.
    """
    try:
        return bool(await redis.set(key, value, ex=ex, nx=True))
    except RedisError as e:
        logger.warning("redis setnx failed key={} err={}", key, e)
        return None


async def safe_delete(redis: Redis, key: str) -> bool:
    try:
        await redis.delete(key)
    except RedisError as e:
        logger.warning("redis delete failed key={} err={}", key, e)
        return False
    else:
        return True


async def safe_mget(redis: Redis, keys: list[str]) -> list[str | None] | None:
    """정상 시 결과 리스트, RedisError 시 None (호출자가 fallback 경로 선택)."""
    if not keys:
        return []
    try:
        return cast("list[str | None]", await redis.mget(keys))
    except RedisError as e:
        logger.warning("redis mget failed count={} err={}", len(keys), e)
        return None


async def safe_incr_with_ttl(redis: Redis, key: str, ttl: int) -> int | None:
    """마지막 이벤트부터 TTL 안에 이어진 이벤트 수를 반환한다.

    TTL 은 매번 갱신된다. MULTI/EXEC 는 INCR 뒤 EXPIRE 전 장애로 TTL 없는 키가 남는 것을 막는다.
    Redis 장애는 None 이며 호출자가 이번 판정을 건너뛴다.
    """
    try:
        # redis-py pipeline 명령은 동기적으로 큐에 쌓이며 execute()만 I/O를 수행한다.
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl)
            results = await pipe.execute()
        return int(results[0])
    except RedisError as e:
        logger.warning("redis incr_with_ttl failed key={} err={}", key, e)
        return None
