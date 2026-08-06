"""_db_retry — consumer DB 재시도 정책 (F6): 일시 장애만 retry, 영구는 즉시 raise.

대역 session_factory/repo/fn 로 재시도 횟수만 검증 (DB 불필요, unit). 백오프는 `sleep` 인자로
주입해 실제 대기를 지운다 — 기본값이 `asyncio.sleep` 이라 주입 없이는 한 케이스에 최대 6초가 붙는다.
"""

import asyncio
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, ProgrammingError

from assessment_engine.consumer.handlers._common import _RETRY_MAX_ATTEMPTS, _db_retry
from tests.fakes import FakeSessionFactory


class _RecordingSleep:
    """주입된 백오프 — 대기 시간만 기록하고 즉시 돌아온다."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _sa_error(cls: type[DBAPIError]) -> DBAPIError:
    """SQLAlchemy DBAPI 계열 예외 인스턴스 (statement, params, orig)."""
    return cls("stmt", {}, Exception("orig"))


class _SqlstateError(Exception):
    """asyncpg 예외 대역 — _db_retry 가 보는 것은 orig.sqlstate 하나다."""

    def __init__(self, message: str, sqlstate: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def _deadlock_error() -> DBAPIError:
    """asyncpg deadlock — base DBAPIError(OperationalError 아님) + orig sqlstate 40P01."""
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
    """OperationalError(일시 장애)는 _RETRY_MAX_ATTEMPTS 회 재시도 후 raise."""
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
    # 마지막 시도 뒤에는 자지 않는다 — 잘 자리는 시도 사이에만 있다.
    assert len(sleep.delays) == _RETRY_MAX_ATTEMPTS - 1


async def test_db_retry_integrity_error_no_retry():
    """IntegrityError(영구 — UNIQUE/FK 위반)는 즉시 raise, 재시도 0."""
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
    """ProgrammingError 는 DBAPIError 상속이지만 non-retryable — 즉시 raise (F6 회귀 가드).

    이전 정책(_RETRYABLE_DB_EXC 에 DBAPIError 광역 포함)에선 3회 헛재시도했다. 좁힌 뒤 1회여야 한다.
    """
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
    """deadlock(SQLSTATE 40P01)은 asyncpg 가 OperationalError 아닌 base DBAPIError 로 래핑하나 일시 장애라 재시도.

    회귀 가드: 동시 신규서버 insert 시 deadlock victim 이 재시도 없이 DLQ 로 가던 버그.
    victim rollback 후 재시도하면 경합 해소되어 흡수(다음 attempt 성공).
    """
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
    assert calls == 2  # deadlock 1회 후 재시도 성공


async def test_db_retry_retries_command_timeout():
    """asyncpg 의 connect/command timeout 은 DBAPIError 로 감싸이지 않는다 — 별도 분기가 받는다.

    dialect 예외 번역표에 없어 타입으로는 안 갈리고, 여기서 못 받으면 일시 장애가 곧장 DLQ 로 간다.
    """
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
    """주입 지점이 열려 있어도 기본 경로는 실제 백오프여야 한다 — 테스트 편의가 운영 동작을 바꾸면 안 된다.

    `inspect.signature` 는 쓰지 않는다 — 타입만 쓰는 import 가 TYPE_CHECKING 안에 있어 어노테이션을
    값으로 평가하는 순간 NameError 다. 여기서 볼 것은 기본값 하나뿐이다.
    """
    defaults = _db_retry.__defaults__

    assert defaults is not None
    assert defaults[-1] is asyncio.sleep
