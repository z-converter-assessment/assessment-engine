from functools import lru_cache
from typing import cast

from loguru import logger
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import RedisError

from assessment_engine.config import WebSettings

# db layer는 모든 컴포넌트 공통 — 자체 WebSettings 인스턴스화 (session.py와 동일 패턴).
# 첫 get_pool 호출에서 만든다 — import 만으로 설정을 요구하지 않는다.


@lru_cache(maxsize=1)
def get_pool() -> ConnectionPool:
    # redis 는 from_url 의 **kwargs 를 타입 없이 선언한다 — 인자 이름별 검증이 성립하지 않는다.
    return ConnectionPool.from_url(  # pyright: ignore[reportUnknownMemberType]
        WebSettings().redis_url,  # pyright: ignore[reportCallIssue]
        decode_responses=True,
        socket_timeout=5,  # F6 — 명령 timeout (fail-open 경계)
        socket_connect_timeout=3,
        # 장수 async 풀 정석: idle-cut(방화벽/서버) 로 죽은 소켓을 사용 직전 PING 검사해 spurious
        # ConnectionResetError -> fail-open 캐시미스(#C3)를 예방. keepalive 로 TCP dead-peer 감지.
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


# --- fail-open helpers ------------------------------------------------------
# Redis 장애 시 silent fallback. 정확성은 2단 안전망(DB UNIQUE / DB query)에 위임.
# 정책 근거: CLAUDE.md #C3 + docs/decisions/adr/0001-redis-decoupling.md + docs/reference/redis.md "장애 시 동작".


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
    """원자적 SET NX. True=첫 처리, False=중복, None=Redis 장애(호출자가 fail-open 결정).

    멱등성 체크 전용. None 시 호출자가 처리 진행 → DB UNIQUE 제약(2단)이 중복 INSERT를 흡수.
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
    """슬라이딩 윈도우 카운터 — INCR + EXPIRE를 MULTI/EXEC 트랜잭션으로 묶어 1 RTT·원자.

    EXPIRE를 매번 reset하므로 "마지막 INCR 후 ttl초 내 N회"를 추적 (fixed window 아님).
    transaction=True 로 INCR/EXPIRE 원자 보장 (동일 1 RTT, 두 명령 사이 크래시로 TTL 없는 키 잔류 방지).
    실패 시 None — 호출자는 카운터를 못 읽었다고 간주 (alert는 다음 호출 기회에).
    """
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl)
            results = await pipe.execute()
        return int(results[0])
    except RedisError as e:
        logger.warning("redis incr_with_ttl failed key={} err={}", key, e)
        return None
