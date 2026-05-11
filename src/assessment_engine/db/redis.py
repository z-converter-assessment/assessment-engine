from loguru import logger
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import RedisError

from assessment_engine.config import web_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    pool = _pool
    if pool is None:
        pool = ConnectionPool.from_url(
            web_settings.redis_url,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=3,
        )
        _pool = pool
    return pool


def get_redis() -> Redis:
    return Redis(connection_pool=get_pool())


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.disconnect()
        _pool = None


# ─── fail-open helpers ──────────────────────────────────────────────────────
# Redis 장애 시 silent fallback. 정확성은 2단 안전망(DB UNIQUE / DB query)에 위임.
# 정책 근거: CLAUDE.md #C3 + docs/adr/0001-redis-decoupling.md + docs/architecture/redis.md "장애 시 동작".

async def safe_get(redis: Redis, key: str) -> str | None:
    try:
        return await redis.get(key)
    except RedisError as e:
        logger.warning("redis get failed key={} err={}", key, e)
        return None


async def safe_set(redis: Redis, key: str, value: str, ex: int | None = None) -> bool:
    try:
        await redis.set(key, value, ex=ex)
        return True
    except RedisError as e:
        logger.warning("redis set failed key={} err={}", key, e)
        return False


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
        return True
    except RedisError as e:
        logger.warning("redis delete failed key={} err={}", key, e)
        return False


async def safe_mget(redis: Redis, keys: list[str]) -> list[str | None] | None:
    """정상 시 결과 리스트, RedisError 시 None (호출자가 fallback 경로 선택)."""
    if not keys:
        return []
    try:
        return await redis.mget(keys)
    except RedisError as e:
        logger.warning("redis mget failed count={} err={}", len(keys), e)
        return None


async def safe_publish(redis: Redis, channel: str, message: str) -> bool:
    try:
        await redis.publish(channel, message)
        return True
    except RedisError as e:
        logger.warning("redis publish failed channel={} err={}", channel, e)
        return False


async def safe_incr_with_ttl(redis: Redis, key: str, ttl: int) -> int | None:
    """슬라이딩 윈도우 카운터 — INCR + EXPIRE를 pipeline으로 묶어 1 RTT.

    EXPIRE를 매번 reset하므로 "마지막 INCR 후 ttl초 내 N회"를 추적 (fixed window 아님).
    실패 시 None — 호출자는 카운터를 못 읽었다고 간주 (alert는 다음 호출 기회에).
    """
    try:
        async with redis.pipeline(transaction=False) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl)
            results = await pipe.execute()
        return int(results[0])
    except RedisError as e:
        logger.warning("redis incr_with_ttl failed key={} err={}", key, e)
        return None