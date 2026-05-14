"""Composition root — FastAPI 의존성 주입 진입점.

라우터는 이 모듈의 helper만 import. 구체 구현체(QueryRepository) 직접 import 금지 (F4).
"""
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from assessment_engine.db.redis import get_redis
from assessment_engine.db.repositories.collect_repository import CollectRepository
from assessment_engine.db.repositories.diagnostic_repository import DiagnosticRepository
from assessment_engine.db.repositories.query_repository import QueryRepository
from assessment_engine.db.session import AsyncSessionLocal, get_db
from assessment_engine.web.services.diagnostic_service import DiagnosticService
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.services.task_service import TaskService


def get_service(
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> QueryService:
    return QueryService(QueryRepository(db), redis)


def get_task_service(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TaskService:
    """TaskService DI — router 는 추상만 본다 (F4).

    query_repo 는 request-scoped(get_db) 지만 collect_repo 는 task INSERT 용 별도 트랜잭션
    필요(서버별 독립 commit) 이라 session_factory + factory 패턴.
    broker_channel 은 lifespan 에서 app.state 에 저장한 영속 channel 재사용.
    """
    return TaskService(
        query_repo=QueryRepository(db),
        session_factory=AsyncSessionLocal,
        collect_repo_factory=CollectRepository,
        broker_channel=request.app.state.broker_channel,
    )


def get_diagnostic_service(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> DiagnosticService:
    """DiagnosticService DI (ADR 0004).

    query_repo는 request-scoped(get_db)로 resolve_server_ids 1회. diagnostic_repo는 별도
    session(session_factory)로 enqueue/조회 트랜잭션 분리 — task_service 동일 패턴.
    broker_channel은 lifespan에서 app.state에 저장한 영속 channel 재사용.
    """
    return DiagnosticService(
        query_repo=QueryRepository(db),
        session_factory=AsyncSessionLocal,
        diagnostic_repo_factory=DiagnosticRepository,
        broker_channel=request.app.state.broker_channel,
        redis=redis,
    )


async def resolve_internal_id(
    server_id: UUID,
    service: QueryService = Depends(get_service),
) -> int:
    """path param `{server_id}` (public_id UUID) → 내부 정수 PK.

    라우터에서 `internal_id: int = Depends(resolve_internal_id)`로 주입.
    - invalid UUID 형식 → FastAPI가 422 자동 변환
    - 형식 OK이지만 DB 미존재 → 404
    resolve 자체는 service에 위임 (Redis 캐시 활용).
    """
    sid = await service.resolve_server_id(str(server_id))
    if sid is None:
        raise HTTPException(status_code=404)
    return sid