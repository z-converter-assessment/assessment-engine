import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from db.models import server_inventory, server_metrics  # noqa: F401 — ORM 모델 등록
from db.models import server_disk_io, server_net_io, server_mount_usage  # noqa: F401
from db.models.base import Base


@pytest.fixture(scope="session")
def pg_url():
    # TimescaleDB 이미지 사용 — server_metrics 는 hypertable 로 관리된다
    with PostgresContainer("timescale/timescaledb:latest-pg16") as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2", "postgresql+asyncpg")


@pytest.fixture(scope="session")
def engine(pg_url):
    return create_async_engine(pg_url, poolclass=NullPool)


@pytest.fixture(scope="session")
def setup_schema(engine):
    async def _create():
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"))
            await conn.run_sync(Base.metadata.create_all)

    async def _drop():
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))

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