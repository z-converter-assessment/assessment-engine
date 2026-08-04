"""DB 세션 진입점. web·consumer·worker 공통이라 자체 WebSettings 를 쓴다 (circular import 회피, #F4).

엔진과 세션 팩토리를 import 시점이 아니라 첫 호출에서 만든다 — 모듈을 읽는 것만으로 접속 문자열이
필요해지면 설정 없이는 import 조차 못 한다.
"""

from functools import lru_cache
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from assessment_engine.config import WebSettings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = WebSettings()  # pyright: ignore[reportCallIssue]
    return create_async_engine(
        settings.database_url,
        echo=settings.sqlalchemy_echo,
        connect_args={"command_timeout": 30, "timeout": 10},
        pool_pre_ping=True,
        # DBAPIError 문자열에 SQL 전문과 바인드 파라미터가 붙는다 — 재시도 로그와 DLQ 경로로 새어 나간다.
        hide_parameters=True,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session
