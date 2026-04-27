from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from db.redis import get_redis
from db.session import get_db
from db.repositories.query_repository import QueryRepository
from web.services.query_service import QueryService


def get_service(db: AsyncSession = Depends(get_db)) -> QueryService:
    return QueryService(QueryRepository(db))


def get_redis_client() -> Redis:
    return get_redis()