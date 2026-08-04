"""Composition root — FastAPI 의존성 주입 진입점.

라우터는 이 모듈의 helper만 import. 구체 구현체(QueryRepository) 직접 import 금지 (F4).
"""

from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request

from assessment_engine.cache.redis import get_redis
from assessment_engine.db.repositories.collect_repository import CollectRepository
from assessment_engine.db.repositories.diagnostic_repository import DiagnosticRepository
from assessment_engine.db.repositories.query.query_repository import QueryRepository
from assessment_engine.db.session import get_db, get_session_factory
from assessment_engine.web.services.diagnostic_service import DiagnosticService
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.services.task_service import HttpZdmPackageResolver, TaskService

if TYPE_CHECKING:
    from uuid import UUID

    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession


def get_service(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> QueryService:
    return QueryService(QueryRepository(db), redis)


def get_task_service(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> TaskService:
    """TaskService DI — router 는 추상만 본다 (F4).

    query_repo 는 request-scoped(get_db) 지만 collect_repo 는 task INSERT 용 별도 트랜잭션
    필요(서버별 독립 commit) 이라 session_factory + factory 패턴.
    broker_channel·http_client 는 lifespan 에서 app.state 에 저장한 영속 객체 재사용.
    zdm_resolver 는 request-scoped (redis 가 request-scoped) — 상태 없는 가벼운 wrapper.
    """
    return TaskService(
        query_repo=QueryRepository(db),
        session_factory=get_session_factory(),
        collect_repo_factory=CollectRepository,
        broker_channel=request.app.state.broker_channel,
        zdm_resolver=HttpZdmPackageResolver(
            http_client=request.app.state.http_client,
            redis=redis,
        ),
        redis=redis,
    )


def get_diagnostic_service() -> DiagnosticService:
    """DiagnosticService DI — 보고서 발행(enqueue)·이력·워커 lifecycle.

    diagnostic_repo 는 모두 별도 session(session_factory)로 트랜잭션 분리 — 보고서 생성은 워커가
    독립 세션으로 수행하므로 request-scoped 세션 의존 없음(워커가 DI 없이도 동일 인스턴스 구성 가능).
    """
    return DiagnosticService(
        session_factory=get_session_factory(),
        diagnostic_repo_factory=DiagnosticRepository,
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
