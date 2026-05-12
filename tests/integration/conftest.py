"""integration 테스트용 function-scope fixture.

- collect_repo: CollectRepository(session) — Consumer 측 인터페이스
- query_repo: QueryRepository(session) — Web 측 인터페이스
- diagnostic_repo: DiagnosticRepository(session) — 진단 워커·web 공통
"""
import pytest_asyncio

from assessment_engine.db.repositories.collect_repository import CollectRepository
from assessment_engine.db.repositories.diagnostic_repository import DiagnosticRepository
from assessment_engine.db.repositories.query_repository import QueryRepository


@pytest_asyncio.fixture
async def collect_repo(db_session) -> CollectRepository:
    return CollectRepository(db_session)


@pytest_asyncio.fixture
async def query_repo(db_session) -> QueryRepository:
    return QueryRepository(db_session)


@pytest_asyncio.fixture
async def diagnostic_repo(db_session) -> DiagnosticRepository:
    return DiagnosticRepository(db_session)