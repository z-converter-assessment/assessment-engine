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
    """TaskService DI.

    collect_repo 만 request 세션이 아닌 session_factory + factory — task INSERT 가 서버별 독립 commit 을 쓴다.
    zdm_resolver 만 app.state 가 아니라 매 요청 새로 만든다 — redis 의존이 request-scoped 이고 wrapper
    자체는 상태가 없다.
    """
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
    """DiagnosticService DI — 보고서 발행(enqueue)·이력·워커 lifecycle.

    request-scoped 세션에 의존하지 않는다 — 워커가 DI 없이 같은 인스턴스를 구성해 쓴다.
    """
    return DiagnosticService(
        session_factory=get_session_factory(),
        diagnostic_repo_factory=SqlDiagnosticRepository,
    )


type QueryServiceDep = Annotated[QueryService, Depends(get_service)]
type TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]
type DiagnosticServiceDep = Annotated[DiagnosticService, Depends(get_diagnostic_service)]


async def resolve_internal_id(server_id: UUID, service: QueryServiceDep) -> int:
    """path param `{server_id}` (public_id UUID) -> 내부 정수 PK. 미존재는 404, 형식 오류는 FastAPI 가 422."""
    sid = await service.resolve_server_id(str(server_id))
    if sid is None:
        raise HTTPException(status_code=404)
    return sid


type ServerIdDep = Annotated[int, Depends(resolve_internal_id)]
