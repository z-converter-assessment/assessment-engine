import asyncio
import importlib
import os
import pkgutil
import subprocess
import sys
from typing import TYPE_CHECKING, cast

import pytest
import pytest_asyncio
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

import assessment_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator

_UNIT_TEST_SECRETS = {
    "POSTGRES_PASSWORD": "unit-test-postgres-secret",
    "RABBITMQ_PASSWORD": "unit-test-rabbitmq-secret",
}


def _cache_clearers() -> list[Callable[[], None]]:
    found: list[Callable[[], None]] = []
    for info in pkgutil.walk_packages(assessment_engine.__path__, f"{assessment_engine.__name__}."):
        if ".migrations" in info.name or info.name.endswith(".__main__"):
            continue
        module = importlib.import_module(info.name)
        for obj in cast("dict[str, object]", vars(module)).values():
            clear = getattr(obj, "cache_clear", None)
            if callable(clear) and getattr(obj, "__module__", None) == info.name:
                found.append(cast("Callable[[], None]", clear))
    if not found:
        raise RuntimeError("Composition Root 캐시를 하나도 못 찾았다 — 탐색이 깨졌다")
    return found


@pytest.fixture(autouse=True)
def unit_test_secrets(monkeypatch: pytest.MonkeyPatch):
    for key, value in _UNIT_TEST_SECRETS.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    yield
    for clear in _cache_clearers():
        clear()


@pytest.fixture
def captured_logs() -> Iterator[list[str]]:
    messages: list[str] = []
    sink_id = logger.add(messages.append, level="DEBUG", format="{message}")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


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

    env = os.environ.copy()
    env["POSTGRES_HOST"] = host
    env["POSTGRES_PORT"] = str(port)
    env["POSTGRES_USER"] = "test"
    env["POSTGRES_PASSWORD"] = "test"
    env["POSTGRES_DB"] = "assessment_test"
    env["APP_ENV"] = "dev"
    await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-m",
            "assessment_engine.migrate",
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
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
