from typing import TYPE_CHECKING

import pytest_asyncio
from sqlalchemy import text

from assessment_engine.db.repositories.collect_sql import SqlCollectRepository
from assessment_engine.db.repositories.diagnostic_sql import SqlDiagnosticRepository
from assessment_engine.db.repositories.query.repository_sql import SqlQueryRepository

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def collect_repo(db_session: AsyncSession) -> SqlCollectRepository:
    return SqlCollectRepository(db_session)


@pytest_asyncio.fixture
async def query_repo(db_session: AsyncSession) -> SqlQueryRepository:
    return SqlQueryRepository(db_session)


@pytest_asyncio.fixture
async def diagnostic_repo(db_session: AsyncSession) -> AsyncIterator[SqlDiagnosticRepository]:
    await db_session.execute(text("TRUNCATE diagnostic_jobs"))
    await db_session.commit()
    yield SqlDiagnosticRepository(db_session)
    await db_session.execute(text("TRUNCATE diagnostic_jobs"))
    await db_session.commit()
