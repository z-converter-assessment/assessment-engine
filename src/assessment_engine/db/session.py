from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from assessment_engine.config import web_settings

engine = create_async_engine(
    web_settings.database_url,
    echo=web_settings.sqlalchemy_echo,
    connect_args={"command_timeout": 30, "timeout": 10},
    pool_pre_ping=True,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session