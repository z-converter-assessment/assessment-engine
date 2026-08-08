"""Composition root — FastAPI 의존성 주입 진입점."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from assessment_engine.cache.redis import get_redis
from assessment_engine.db.repositories.collect_sql import SqlCollectRepository
from assessment_engine.db.repositories.diagnostic_sql import SqlDiagnosticRepository
from assessment_engine.db.repositories.query.repository_sql import SqlQueryRepository
from assessment_engine.db.session import get_db, get_session_factory
from assessment_engine.web.services.diagnostic_service import DiagnosticService
from assessment_engine.web.services.query import QueryService
from assessment_engine.web.services.task_service import HttpZdmPackageResolver, TaskService

type DbSessionDep = Annotated[AsyncSession, Depends(get_db)]
type RedisDep = Annotated[Redis, Depends(get_redis)]


def get_service(db: DbSessionDep, redis: RedisDep) -> QueryService:
    return QueryService(SqlQueryRepository(db), redis)


def get_task_service(request: Request, db: DbSessionDep, redis: RedisDep) -> TaskService:
    return TaskService(
        query_repo=SqlQueryRepository(db),
        session_factory=get_session_factory(),
        collect_repo_factory=SqlCollectRepository,
        broker_channel=request.app.state.broker_channel,
        zdm_resolver=HttpZdmPackageResolver(
            http_client=request.app.state.http_client,
            redis=redis,
        ),
        redis=redis,
    )


def get_diagnostic_service() -> DiagnosticService:
    return DiagnosticService(
        session_factory=get_session_factory(),
        diagnostic_repo_factory=SqlDiagnosticRepository,
    )


type QueryServiceDep = Annotated[QueryService, Depends(get_service)]
type TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]
type DiagnosticServiceDep = Annotated[DiagnosticService, Depends(get_diagnostic_service)]


async def resolve_internal_id(server_id: UUID, service: QueryServiceDep) -> int:
    sid = await service.resolve_server_id(str(server_id))
    if sid is None:
        raise HTTPException(status_code=404)
    return sid


type ServerIdDep = Annotated[int, Depends(resolve_internal_id)]
