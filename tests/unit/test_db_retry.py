import asyncio
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, ProgrammingError

from assessment_engine.consumer.handlers._common import _RETRY_MAX_ATTEMPTS, _db_retry
from tests.fakes import FakeSessionFactory


class _RecordingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _sa_error(cls: type[DBAPIError]) -> DBAPIError:
    return cls("stmt", {}, Exception("orig"))


class _SqlstateError(Exception):
    def __init__(self, message: str, sqlstate: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def _deadlock_error() -> DBAPIError:
    return DBAPIError("stmt", {}, _SqlstateError("deadlock detected", "40P01"))


async def test_db_retry_success_commits_once():
    factory = FakeSessionFactory()
    calls = 0

    async def fn(repo: object) -> str:
        nonlocal calls
        calls += 1
        return "ok"

    sleep = _RecordingSleep()

    result = await _db_retry(cast("Any", factory), MagicMock(), fn, sleep)

    assert result == "ok"
    assert calls == 1
    assert sleep.delays == []
    assert [s.commits for s in factory.sessions] == [1]


async def test_db_retry_retries_operational_error_then_raises():
    factory = FakeSessionFactory()
    calls = 0

    async def fn(repo: object) -> str:
        nonlocal calls
        calls += 1
        raise _sa_error(OperationalError)

    sleep = _RecordingSleep()

    with pytest.raises(OperationalError):
        await _db_retry(cast("Any", factory), MagicMock(), fn, sleep)

    assert calls == _RETRY_MAX_ATTEMPTS
    assert len(sleep.delays) == _RETRY_MAX_ATTEMPTS - 1


async def test_db_retry_integrity_error_no_retry():
    factory = FakeSessionFactory()
    calls = 0

    async def fn(repo: object) -> str:
        nonlocal calls
        calls += 1
        raise _sa_error(IntegrityError)

    sleep = _RecordingSleep()

    with pytest.raises(IntegrityError):
        await _db_retry(cast("Any", factory), MagicMock(), fn, sleep)

    assert calls == 1
    assert sleep.delays == []


async def test_db_retry_permanent_dbapi_error_no_retry():
    factory = FakeSessionFactory()
    calls = 0

    async def fn(repo: object) -> str:
        nonlocal calls
        calls += 1
        raise _sa_error(ProgrammingError)

    sleep = _RecordingSleep()

    with pytest.raises(ProgrammingError):
        await _db_retry(cast("Any", factory), MagicMock(), fn, sleep)

    assert calls == 1
    assert sleep.delays == []


async def test_db_retry_retries_deadlock():
    factory = FakeSessionFactory()
    calls = 0

    async def fn(repo: object) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _deadlock_error()
        return "ok"

    result = await _db_retry(cast("Any", factory), MagicMock(), fn, _RecordingSleep())

    assert result == "ok"
    assert calls == 2


async def test_db_retry_retries_command_timeout():
    factory = FakeSessionFactory()
    calls = 0

    async def fn(repo: object) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError
        return "ok"

    result = await _db_retry(cast("Any", factory), MagicMock(), fn, _RecordingSleep())

    assert result == "ok"
    assert calls == 2


def test_db_retry_default_sleep_is_the_real_one():
    defaults = _db_retry.__defaults__

    assert defaults is not None
    assert defaults[-1] is asyncio.sleep
