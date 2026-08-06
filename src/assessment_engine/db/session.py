"""DB 세션 진입점. web·consumer·worker 공통이라 자체 WebSettings 를 쓴다 (circular import 회피, #F4).

엔진과 세션 팩토리를 import 시점이 아니라 첫 호출에서 만든다 — 모듈을 읽는 것만으로 접속 문자열이
필요해지면 설정 없이는 import 조차 못 한다.
"""

from functools import cache
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from assessment_engine.config import WebSettings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@cache
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


@cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


async def dispose_engine() -> None:
    """풀의 커넥션을 닫는다. 각 프로세스의 종료 경로가 마지막에 부른다 (F11).

    빠뜨리면 asyncpg 커넥션이 루프 종료 후 GC 되면서 "Event loop is closed" 가 stdout 으로 샌다 —
    `LOG_FORMAT=json` 을 켜도 그 줄만 형식이 달라 aggregator 파싱이 깨진다.
    엔진을 만든 적이 없으면(설정만 읽고 끝난 프로세스) 만들지 않는다.
    """
    if get_engine.cache_info().currsize == 0:
        return
    await get_engine().dispose()
