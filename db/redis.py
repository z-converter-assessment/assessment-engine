from redis.asyncio import Redis, ConnectionPool

from config import web_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    pool = _pool
    if pool is None:
        pool = ConnectionPool.from_url(
            web_settings.redis_url,
            decode_responses=True,
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