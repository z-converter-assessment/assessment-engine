"""integration 테스트용 function-scope fixture.

- collect_repo: CollectRepository(session) — Consumer 측 인터페이스
- query_repo: QueryRepository(session) — Web 측 인터페이스
- diagnostic_repo: DiagnosticRepository(session) — 진단 워커·web 공통

진단 테스트는 명시 commit 패턴(상태 전이·get_latest 검증)이라 db_session rollback로 정리 안 됨.
TRUNCATE로 setup·teardown 양쪽에서 격리 강제 — 이전·이후 테스트의 누적 commit 데이터 차단.
"""

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text

from assessment_engine.db.repositories.collect_repository import CollectRepository
from assessment_engine.db.repositories.diagnostic_repository import DiagnosticRepository
from assessment_engine.db.repositories.query.query_repository import QueryRepository


@pytest_asyncio.fixture
async def collect_repo(db_session) -> CollectRepository:
    return CollectRepository(db_session)


@pytest_asyncio.fixture
async def query_repo(db_session) -> QueryRepository:
    return QueryRepository(db_session)


@pytest_asyncio.fixture
async def diagnostic_repo(db_session) -> AsyncIterator[DiagnosticRepository]:
    await db_session.execute(text("TRUNCATE diagnostic_jobs"))
    await db_session.commit()
    yield DiagnosticRepository(db_session)
    await db_session.execute(text("TRUNCATE diagnostic_jobs"))
    await db_session.commit()
