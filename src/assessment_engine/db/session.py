from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from assessment_engine.config import WebSettings

# db layer는 모든 컴포넌트(web·consumer) 공통 — 자체 WebSettings 인스턴스화로
# 컴포넌트별 sub-module(web/settings·consumer/settings)에 대한 circular import 회피.
# 같은 환경변수 read라 다른 instance와 결과 동일.
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
