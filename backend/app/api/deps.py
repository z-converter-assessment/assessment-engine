from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.repositories.interface import IServerRepository
from app.repositories.server import ServerRepository
from app.services.server import ServerService


def get_repo(db: AsyncSession = Depends(get_db)) -> IServerRepository:
    return ServerRepository(db)


def get_service(repo: IServerRepository = Depends(get_repo)) -> ServerService:
    return ServerService(repo)