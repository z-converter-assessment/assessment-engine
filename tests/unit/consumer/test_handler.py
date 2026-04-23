import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from consumer.handler import make_handler

VALID_PAYLOAD = {
    "hostname": "server01",
    "nproc": 4,
    "mem_total_mb": 16384,
    "disks": [{"name": "vda", "size": "30G"}],
    "ip": {"internal": ["10.0.0.1"], "external": ["1.2.3.4"]},
}


def make_message(body: dict | None = None, raw: bytes | None = None) -> MagicMock:
    message = MagicMock()
    message.body = raw if raw is not None else json.dumps(body or VALID_PAYLOAD).encode()
    message.process.return_value = AsyncMock()
    return message


def make_session_factory(session: AsyncMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


class TestMakeHandler:
    async def test_existing_server_commits(self):
        session = AsyncMock()
        repo = AsyncMock()
        repo.find_server = AsyncMock(return_value=1)
        repo.insert_metric = AsyncMock()

        handler = make_handler(make_session_factory(session), MagicMock(return_value=repo))
        await handler(make_message())

        repo.create_server.assert_not_called()
        session.commit.assert_called_once()

    async def test_new_server_creates_then_commits(self):
        session = AsyncMock()
        repo = AsyncMock()
        repo.find_server = AsyncMock(return_value=None)
        repo.create_server = AsyncMock(return_value=42)
        repo.insert_metric = AsyncMock()

        handler = make_handler(make_session_factory(session), MagicMock(return_value=repo))
        await handler(make_message())

        repo.create_server.assert_called_once_with("server01")
        session.commit.assert_called_once()

    async def test_invalid_json_does_not_save(self):
        session = AsyncMock()
        repo_factory = MagicMock()

        handler = make_handler(make_session_factory(session), repo_factory)
        await handler(make_message(raw=b"not-json"))

        repo_factory.assert_not_called()
        session.commit.assert_not_called()

    async def test_invalid_schema_does_not_save(self):
        session = AsyncMock()
        repo_factory = MagicMock()

        handler = make_handler(make_session_factory(session), repo_factory)
        await handler(make_message(body={"hostname": ""}))

        repo_factory.assert_not_called()

    async def test_db_error_retries_3_times(self, mocker):
        mocker.patch("consumer.handler.asyncio.sleep")

        session = AsyncMock()
        session.commit = AsyncMock(side_effect=Exception("db error"))

        repo = AsyncMock()
        repo.find_server = AsyncMock(return_value=1)
        repo.insert_metric = AsyncMock()

        session_factory = make_session_factory(session)
        handler = make_handler(session_factory, MagicMock(return_value=repo))

        with pytest.raises(Exception, match="db error"):
            await handler(make_message())

        assert session_factory.call_count == 3

    async def test_db_error_succeeds_on_third_attempt(self, mocker):
        mocker.patch("consumer.handler.asyncio.sleep")

        session = AsyncMock()
        session.commit = AsyncMock(side_effect=[Exception("err"), Exception("err"), None])

        repo = AsyncMock()
        repo.find_server = AsyncMock(return_value=1)
        repo.insert_metric = AsyncMock()

        session_factory = make_session_factory(session)
        handler = make_handler(session_factory, MagicMock(return_value=repo))

        await handler(make_message())

        assert session_factory.call_count == 3
        assert session.commit.call_count == 3

    async def test_sleep_uses_exponential_backoff(self, mocker):
        sleep_mock = mocker.patch("consumer.handler.asyncio.sleep")

        session = AsyncMock()
        session.commit = AsyncMock(side_effect=[Exception("e"), Exception("e"), None])

        repo = AsyncMock()
        repo.find_server = AsyncMock(return_value=1)
        repo.insert_metric = AsyncMock()

        handler = make_handler(make_session_factory(session), MagicMock(return_value=repo))
        await handler(make_message())

        calls = [c.args[0] for c in sleep_mock.call_args_list]
        assert calls == [1, 2]
