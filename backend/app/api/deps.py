from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.session import get_db
from app.repositories.i_read_repository import IReadRepository
from app.repositories.read_repository import ReadRepository
from app.services.query_service import QueryService


def get_read_repo(db: AsyncSession = Depends(get_db)) -> IReadRepository:
    return ReadRepository(db)


def get_service(repo: IReadRepository = Depends(get_read_repo)) -> QueryService:
    return QueryService(repo)