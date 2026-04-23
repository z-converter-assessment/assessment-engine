from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_db
from db.repositories.query_repository import QueryRepository
from web.services.query_service import QueryService


def get_service(db: AsyncSession = Depends(get_db)) -> QueryService:
    return QueryService(QueryRepository(db))