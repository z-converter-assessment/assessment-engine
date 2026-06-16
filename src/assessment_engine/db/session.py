from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from assessment_engine.config import WebSettings

# db layer는 web·consumer 공통 — 자체 WebSettings 로 컴포넌트 sub-module circular import 회피 (#F4).
_settings = WebSettings()

engine = create_async_engine(
    _settings.database_url,
    echo=_settings.sqlalchemy_echo,
    connect_args={"command_timeout": 30, "timeout": 10},
    pool_pre_ping=True,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
