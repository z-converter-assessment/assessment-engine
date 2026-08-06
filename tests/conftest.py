"""전 계층 공통 픽스처.

비밀번호 주입·프로세스 전역 캐시 초기화·로그 수집, 그리고 integration 용 TimescaleDB 컨테이너와 세션.
스키마는 `alembic upgrade head` 로 만든다 — 운영과 같은 경로라 extension·hypertable·partial
index 가 실제 배포와 같은 순서로 적용된다. DDL 을 테스트용으로 따로 쓰지 않는 이유다.
"""

import asyncio
import importlib
import os
import pkgutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
import pytest_asyncio
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

import assessment_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator

_REPO_ROOT = Path(__file__).parent.parent

_UNIT_TEST_SECRETS = {
    "POSTGRES_PASSWORD": "unit-test-postgres-secret",
    "RABBITMQ_PASSWORD": "unit-test-rabbitmq-secret",
}


def _cache_clearers() -> list[Callable[[], None]]:
    """`lru_cache` 로 감싼 Composition Root 진입점의 `cache_clear` 를 패키지에서 찾아낸다.

    손으로 나열하면 진입점이 늘 때 추가를 강제하는 것이 없어 조용히 새 나간다 — 실제로
    `cache.redis.get_pool` 이 그렇게 빠져 있었다. `migrations` 는 module-level 에서 Settings 를
    만들고 `__main__` 은 import 만으로 앱을 띄우므로 제외한다.
    """
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
    """필수 비밀번호를 env 로 준다. 검증 자체를 다루는 테스트는 delenv 로 걷어낸다.

    비밀번호에 기본값이 없어(#F8) `WorkerSettings()` 류를 인자 없이 만드는 테스트는 값을 줄 채널이
    있어야 한다. CI 체크아웃에는 `.env` 도 secret 파일도 없고 job env 에도 넣지 않는다.
    """
    for key, value in _UNIT_TEST_SECRETS.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """`lru_cache` Composition Root 는 프로세스 전역이라 테스트 사이에 값이 새 나간다."""
    yield
    for clear in _cache_clearers():
        clear()


@pytest.fixture
def captured_logs() -> Iterator[list[str]]:
    """렌더된 로그 문자열을 모은다. `logger` 를 patch 하는 대신 sink 를 하나 더 단다.

    patch 는 format string 과 인자를 따로 보지만 sink 는 실제로 찍히는 문자열을 본다 —
    포맷 자리표시자가 어긋나는 회귀는 후자만 잡는다.
    """
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

    # subprocess 로 부른다 — async fixture 안에서 alembic 을 직접 부르면 이벤트 루프가 중첩된다.
    # POSTGRES_* 는 alembic 이 자기 Settings 로 접속 문자열을 조립할 때 읽는다.
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
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
