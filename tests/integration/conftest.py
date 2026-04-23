import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from db.models import metric_snapshot, server_entity  # noqa: F401 — ORM 모델 등록
from db.models.base import Base


@pytest.fixture(scope="session")
def pg_url():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2", "postgresql+asyncpg")


@pytest.fixture(scope="session")
def engine(pg_url):
    # NullPool: 이벤트 루프 간 커넥션 재사용을 막는다
    return create_async_engine(pg_url, poolclass=NullPool)


@pytest.fixture(scope="session")
def setup_schema(engine):
    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)  # type: ignore[arg-type]

    async def _drop():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)  # type: ignore[arg-type]

    asyncio.run(_create())
    yield
    asyncio.run(_drop())


@pytest_asyncio.fixture
async def db_session(engine, setup_schema) -> AsyncIterator[AsyncSession]:
    conn = await engine.connect()
    await conn.begin()
    await conn.begin_nested()

    session = AsyncSession(bind=conn, expire_on_commit=False)

    @event.listens_for(session.sync_session, "after_transaction_end")
    def reopen_savepoint(_sess, _transaction):
        if conn.closed:
            return
        if not conn.in_nested_transaction():
            sync_conn = conn.sync_connection
            if sync_conn is not None:
                sync_conn.begin_nested()

    yield session

    await session.close()
    await conn.rollback()
    await conn.close()