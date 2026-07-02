"""_db_retry — consumer DB 재시도 정책 (F6): 일시 장애만 retry, 영구는 즉시 raise.

mock session_factory/repo/fn 로 재시도 횟수만 검증 (DB 불필요, unit).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from assessment_engine.consumer.handlers._common import _RETRY_MAX_ATTEMPTS, _db_retry


def _mock_session_factory():
    """`async with session_factory() as session` 을 흉내내는 mock (session.commit awaitable)."""
    session = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__.return_value = session
    cm.__aexit__.return_value = False
    return MagicMock(return_value=cm)


def _sa_error(cls):
    """SQLAlchemy DBAPI 계열 예외 인스턴스 (statement, params, orig)."""
    return cls("stmt", {}, Exception("orig"))


async def test_db_retry_success_commits_once():
    factory = _mock_session_factory()
    calls = 0

    async def fn(repo):
        nonlocal calls
        calls += 1
        return "ok"

    result = await _db_retry(factory, MagicMock(), fn)
    assert result == "ok"
    assert calls == 1


async def test_db_retry_retries_operational_error_then_raises():
    """OperationalError(일시 장애)는 _RETRY_MAX_ATTEMPTS 회 재시도 후 raise."""
    factory = _mock_session_factory()
    calls = 0

    async def fn(repo):
        nonlocal calls
        calls += 1
        raise _sa_error(OperationalError)

    with patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(OperationalError):
            await _db_retry(factory, MagicMock(), fn)
    assert calls == _RETRY_MAX_ATTEMPTS


async def test_db_retry_integrity_error_no_retry():
    """IntegrityError(영구 — UNIQUE/FK 위반)는 즉시 raise, 재시도 0."""
    factory = _mock_session_factory()
    calls = 0

    async def fn(repo):
        nonlocal calls
        calls += 1
        raise _sa_error(IntegrityError)

    with patch("asyncio.sleep", new=AsyncMock()) as sleep:
        with pytest.raises(IntegrityError):
            await _db_retry(factory, MagicMock(), fn)
    assert calls == 1
    sleep.assert_not_called()


async def test_db_retry_permanent_dbapi_error_no_retry():
    """ProgrammingError 는 DBAPIError 상속이지만 non-retryable — 즉시 raise (F6 회귀 가드).

    이전 정책(_RETRYABLE_DB_EXC 에 DBAPIError 광역 포함)에선 3회 헛재시도했다. 좁힌 뒤 1회여야 한다.
    """
    factory = _mock_session_factory()
    calls = 0

    async def fn(repo):
        nonlocal calls
        calls += 1
        raise _sa_error(ProgrammingError)

    with patch("asyncio.sleep", new=AsyncMock()) as sleep:
        with pytest.raises(ProgrammingError):
            await _db_retry(factory, MagicMock(), fn)
    assert calls == 1
    sleep.assert_not_called()
