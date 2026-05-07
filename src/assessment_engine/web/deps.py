"""Composition root — FastAPI 의존성 주입 진입점.

라우터는 이 모듈의 helper만 import. 구체 구현체(QueryRepository) 직접 import 금지 (F4).
"""
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from assessment_engine.db.redis import get_redis
from assessment_engine.db.session import get_db
from assessment_engine.db.repositories.query_repository import QueryRepository
from assessment_engine.web.services.query_service import QueryService


def get_service(
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> QueryService:
    return QueryService(QueryRepository(db), redis)


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