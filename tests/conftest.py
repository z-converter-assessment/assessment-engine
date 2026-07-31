"""세션 전체에서 단 1회 spawn하는 TimescaleDB 컨테이너 + async engine.

정석 fixture 계층:
- session scope: 컨테이너 + Alembic migration 적용 + engine (한 번 띄우고 모든 테스트 공유)
- function scope: session + repository + transaction rollback (각 테스트 격리)

schema는 alembic upgrade head로 적용 — 운영(dev/staging/prod)과 동일 경로.
TimescaleDB extension·hypertable·partial index 등 모두 마이그레이션에 포함되어 자동 적용.

session-scope async fixture를 쓰려면 pyproject의
`asyncio_default_fixture_loop_scope = "session"` 설정이 필수.
"""

import os
import subprocess
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

_REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="session")
def _postgres_container() -> PostgresContainer:
    """세션 전체 1회 spawn. TimescaleDB all-in-one 이미지."""
    container = PostgresContainer(
        image="timescale/timescaledb-ha:pg16",
        username="test",
        password="test",
        dbname="assessment_test",
        driver=None,
    )
    with container as pg:
        yield pg


@pytest_asyncio.fixture(scope="session")
async def engine(_postgres_container: PostgresContainer) -> AsyncIterator[AsyncEngine]:
    """asyncpg 드라이버 + Alembic 마이그레이션 적용 (dev/staging/prod와 동일 경로)."""
    host = _postgres_container.get_container_host_ip()
    port = _postgres_container.get_exposed_port(5432)
    async_url = f"postgresql+asyncpg://test:test@{host}:{port}/assessment_test"

    # alembic upgrade head — subprocess로 호출 (async fixture 내 nested asyncio 회피).
    # get_web_settings().database_url을 env var 기반으로 만들도록 POSTGRES_* 주입.
    env = os.environ.copy()
    env["POSTGRES_HOST"] = host
    env["POSTGRES_PORT"] = str(port)
    env["POSTGRES_USER"] = "test"
    env["POSTGRES_PASSWORD"] = "test"
    env["POSTGRES_DB"] = "assessment_test"
    env["APP_ENV"] = "dev"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(_REPO_ROOT / "src" / "assessment_engine" / "_alembic.ini"),
            "upgrade",
            "head",
        ],
        env=env,
        check=True,
        capture_output=True,
    )

    eng = create_async_engine(async_url, echo=False, future=True)
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
