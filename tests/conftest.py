"""세션 전체에서 단 1회 spawn하는 TimescaleDB 컨테이너 + async engine.

정석 fixture 계층:
- session scope: 컨테이너 + engine + schema (한 번 띄우고 모든 테스트 공유)
- function scope: session + repository + transaction rollback (각 테스트 격리)

session-scope async fixture를 쓰려면 pyproject의
`asyncio_default_fixture_loop_scope = "session"` 설정이 필수.
"""
from collections.abc import AsyncGenerator, AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from assessment_engine.db.models.base import Base
# ORM 모델을 Base.metadata에 등록 (side-effect import)
from assessment_engine.db.models import (  # noqa: F401
    server_disk_io,
    server_inventory,
    server_metrics,
    server_mount_usage,
    server_net_io,
)


_HYPERTABLES = (
    "server_metrics",
    "server_disk_io",
    "server_net_io",
    "server_mount_usage",
)


@pytest.fixture(scope="session")
def _postgres_container() -> PostgresContainer:
    """세션 전체 1회 spawn. TimescaleDB 이미지 사용."""
    container = PostgresContainer(
        image="timescale/timescaledb:latest-pg16",
        username="test",
        password="test",
        dbname="assessment_test",
        driver=None,  # asyncpg는 별도 URL 조립
    )
    with container as pg:
        yield pg


@pytest_asyncio.fixture(scope="session")
async def engine(_postgres_container: PostgresContainer) -> AsyncIterator[AsyncEngine]:
    """asyncpg 드라이버 + TimescaleDB extension + 모든 테이블 + hypertable 생성."""
    host = _postgres_container.get_container_host_ip()
    port = _postgres_container.get_exposed_port(5432)
    url = f"postgresql+asyncpg://test:test@{host}:{port}/assessment_test"

    eng = create_async_engine(url, echo=False, future=True)

    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        for table in _HYPERTABLES:
            await conn.execute(text(
                f"SELECT create_hypertable('{table}', 'collected_at', if_not_exists => true)"
            ))

    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine: AsyncEngine) -> AsyncGenerator:
    """function-scope. 각 테스트 시작 시 새 session, 끝에 rollback으로 격리.

    Hypertable 데이터도 transaction rollback으로 정리된다 (TimescaleDB 호환).
    """
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()