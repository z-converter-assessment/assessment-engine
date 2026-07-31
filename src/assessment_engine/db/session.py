"""DB 세션 진입점. web·consumer·worker 공통이라 자체 WebSettings 를 쓴다 (circular import 회피, #F4).

엔진과 세션 팩토리를 import 시점이 아니라 첫 호출에서 만든다 — 모듈을 읽는 것만으로 접속 문자열이
필요해지면 설정 없이는 import 조차 못 한다.
"""

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from assessment_engine.config import WebSettings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = WebSettings()
    return create_async_engine(
        settings.database_url,
        echo=settings.sqlalchemy_echo,
        connect_args={"command_timeout": 30, "timeout": 10},
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_session_factory()() as session:
        yield session
