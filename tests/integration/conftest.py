"""integration 테스트용 function-scope fixture.

- collect_repo: SqlCollectRepository(session) — Consumer 측 인터페이스
- query_repo: SqlQueryRepository(session) — Web 측 인터페이스
- diagnostic_repo: SqlDiagnosticRepository(session) — 진단 워커·web 공통

진단 테스트는 명시 commit 패턴(상태 전이·get_latest 검증)이라 db_session rollback로 정리 안 됨.
TRUNCATE로 setup·teardown 양쪽에서 격리 강제 — 이전·이후 테스트의 누적 commit 데이터 차단.
"""

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
