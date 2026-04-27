"""
handler 행동 테스트.
페이로드 필드 값은 검증하지 않는다 — 스키마 변경 시 _*_MSG 상수만 수정.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from consumer.handler import make_error_handler, make_inventory_handler, make_metrics_handler

# ---------------------------------------------------------------------------
# 최소 유효 페이로드 — 필수 필드만 포함
# ---------------------------------------------------------------------------

_BASE = {
    "agent_version": "1.0.0",
    "collected_at": "2026-01-01T00:00:00Z",
    "hostname": "host01",
    "message_id": "00000000-0000-0000-0000-000000000001",
}

_INVENTORY_MSG: bytes = json.dumps({
    **_BASE,
    "message_type": "inventory",
    "machine_id": "abc123",
    "kernel_version": "5.15.0",
    "cpu_cores": 4,
    "mem_total_mb": 8192,
}).encode()

_METRICS_MSG: bytes = json.dumps({
    **_BASE,
    "message_type": "metrics",
    "cpu_user_pct": 20.0,
    "cpu_system_pct": 5.0,
    "cpu_iowait_pct": 1.0,
    "mem_used_mb": 4096,
    "swap_used_mb": 0,
    "load_1m": 1.0,
}).encode()

_ERROR_MSG: bytes = json.dumps({
    **_BASE,
    "message_type": "error",
    "error_code": "TEST_ERR",
    "error_message": "something failed",
    "failed_component": "collect",
    "timestamp": "2026-01-01T00:00:00Z",
}).encode()


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _make_message(body: bytes) -> MagicMock:
    msg = MagicMock()
    msg.body = body
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=None)
    ctx.__aexit__ = AsyncMock(return_value=False)
    msg.process = MagicMock(return_value=ctx)
    return msg


def _make_session_factory(session: AsyncMock) -> MagicMock:
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


# ---------------------------------------------------------------------------
# inventory handler
# ---------------------------------------------------------------------------

class TestInventoryHandler:
    async def test_valid_message_calls_upsert_and_commits(self):
        session = AsyncMock()
        repo = AsyncMock()
        handler = make_inventory_handler(
            _make_session_factory(session), MagicMock(return_value=repo)
        )
        await handler(_make_message(_INVENTORY_MSG))

        repo.upsert_server.assert_called_once()
        session.commit.assert_called_once()

    async def test_invalid_json_raises_without_db_call(self):
        repo_factory = MagicMock()
        handler = make_inventory_handler(
            _make_session_factory(AsyncMock()), repo_factory
        )
        with pytest.raises(Exception):
            await handler(_make_message(b"not-json"))

        repo_factory.assert_not_called()

    async def test_db_error_retries_3_times(self, mocker):
        mocker.patch("consumer.handler.asyncio.sleep")
        session = AsyncMock()
        session.commit = AsyncMock(side_effect=Exception("db error"))
        session_factory = _make_session_factory(session)

        handler = make_inventory_handler(session_factory, MagicMock(return_value=AsyncMock()))
        with pytest.raises(Exception, match="db error"):
            await handler(_make_message(_INVENTORY_MSG))

        assert session_factory.call_count == 3

    async def test_db_error_exponential_backoff(self, mocker):
        sleep_mock = mocker.patch("consumer.handler.asyncio.sleep")
        session = AsyncMock()
        session.commit = AsyncMock(side_effect=[Exception("e"), Exception("e"), None])

        handler = make_inventory_handler(
            _make_session_factory(session), MagicMock(return_value=AsyncMock())
        )
        await handler(_make_message(_INVENTORY_MSG))

        assert [c.args[0] for c in sleep_mock.call_args_list] == [1, 2]

    async def test_succeeds_on_third_attempt(self, mocker):
        mocker.patch("consumer.handler.asyncio.sleep")
        session = AsyncMock()
        session.commit = AsyncMock(side_effect=[Exception("e"), Exception("e"), None])
        session_factory = _make_session_factory(session)

        handler = make_inventory_handler(session_factory, MagicMock(return_value=AsyncMock()))
        await handler(_make_message(_INVENTORY_MSG))

        assert session_factory.call_count == 3


# ---------------------------------------------------------------------------
# metrics handler
# ---------------------------------------------------------------------------

class TestMetricsHandler:
    async def test_known_server_inserts_and_commits(self):
        session = AsyncMock()
        repo = AsyncMock()
        repo.find_server_id = AsyncMock(return_value=42)
        handler = make_metrics_handler(
            _make_session_factory(session), MagicMock(return_value=repo)
        )
        await handler(_make_message(_METRICS_MSG))

        repo.insert_metric.assert_called_once()
        session.commit.assert_called_once()

    async def test_unknown_server_drops_silently(self):
        repo = AsyncMock()
        repo.find_server_id = AsyncMock(return_value=None)
        handler = make_metrics_handler(
            _make_session_factory(AsyncMock()), MagicMock(return_value=repo)
        )
        await handler(_make_message(_METRICS_MSG))

        repo.insert_metric.assert_not_called()

    async def test_invalid_json_raises_without_db_call(self):
        repo_factory = MagicMock()
        handler = make_metrics_handler(
            _make_session_factory(AsyncMock()), repo_factory
        )
        with pytest.raises(Exception):
            await handler(_make_message(b"not-json"))

        repo_factory.assert_not_called()

    async def test_db_error_retries_3_times(self, mocker):
        mocker.patch("consumer.handler.asyncio.sleep")
        session = AsyncMock()
        session.commit = AsyncMock(side_effect=Exception("db error"))
        session_factory = _make_session_factory(session)

        repo = AsyncMock()
        repo.find_server_id = AsyncMock(return_value=1)
        handler = make_metrics_handler(session_factory, MagicMock(return_value=repo))

        with pytest.raises(Exception, match="db error"):
            await handler(_make_message(_METRICS_MSG))

        assert session_factory.call_count == 3


# ---------------------------------------------------------------------------
# error handler
# ---------------------------------------------------------------------------

class TestErrorHandler:
    async def test_valid_message_does_not_touch_db(self):
        repo_factory = MagicMock()
        handler = make_error_handler()
        await handler(_make_message(_ERROR_MSG))

        repo_factory.assert_not_called()

    async def test_invalid_json_raises(self):
        handler = make_error_handler()
        with pytest.raises(Exception):
            await handler(_make_message(b"not-json"))