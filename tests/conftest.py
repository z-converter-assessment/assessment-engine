"""integration 테스트용 TimescaleDB 컨테이너와 세션.

스키마는 `alembic upgrade head` 로 만든다 — 운영과 같은 경로라 extension·hypertable·partial
index 가 실제 배포와 같은 순서로 적용된다. DDL 을 테스트용으로 따로 쓰지 않는 이유다.
"""

import os
import subprocess
import sys
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

_REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="session")
def _postgres_container() -> Iterator[PostgresContainer]:
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
    host = _postgres_container.get_container_host_ip()
    port = _postgres_container.get_exposed_port(5432)
    async_url = f"postgresql+asyncpg://test:test@{host}:{port}/assessment_test"

    # subprocess 로 부른다 — async fixture 안에서 alembic 을 직접 부르면 이벤트 루프가 중첩된다.
    # POSTGRES_* 는 alembic 이 자기 Settings 로 접속 문자열을 조립할 때 읽는다.
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
async def db_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """테스트마다 rollback 으로 격리한다 — hypertable 에 쓴 행도 함께 되돌아간다."""
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()
