import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from consumer.handler import make_error_handler, make_inventory_handler, make_metrics_handler

pytestmark = pytest.mark.unit

_UUID = str(uuid4())
_NOW = "2024-01-01T00:00:00Z"

_BASE = dict(
    machine_id="abc123",
    agent_version="1.0.0",
    collected_at=_NOW,
    hostname="host-01",
    message_id=_UUID,
)

_INV_PAYLOAD = json.dumps({**_BASE, "message_type": "inventory"}).encode()
_MET_PAYLOAD = json.dumps({**_BASE, "message_type": "metrics"}).encode()
_ERR_PAYLOAD = json.dumps({
    **_BASE, "message_type": "error",
    "error_code": "E001", "error_message": "fail", "failed_component": "collect",
}).encode()


def _make_message(body: bytes) -> MagicMock:
    msg = MagicMock()
    msg.body = body
    msg.process = MagicMock(return_value=AsyncMock().__aenter__.return_value)
    msg.process.__aenter__ = AsyncMock(return_value=None)
    msg.process.__aexit__ = AsyncMock(return_value=False)
    # context manager protocol
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=None)
    cm.__aexit__ = AsyncMock(return_value=False)
    msg.process.return_value = cm
    return msg


def _make_repo(server_id: int | None = 1) -> MagicMock:
    repo = MagicMock()
    repo.upsert_server = AsyncMock(return_value=server_id)
    repo.find_server_id = AsyncMock(return_value=server_id)
    repo.insert_metric = AsyncMock(return_value=None)
    return repo


def _make_session_factory(repo: MagicMock) -> tuple[MagicMock, MagicMock]:
    session = MagicMock()
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock()
    factory.return_value = session

    repo_factory = MagicMock(return_value=repo)
    return factory, repo_factory


def _make_redis(is_new: bool = True) -> MagicMock:
    redis = MagicMock()
    redis.set = AsyncMock(return_value=is_new)
    redis.delete = AsyncMock()
    redis.publish = AsyncMock()
    return redis


class TestInventoryHandler:
    async def test_valid_message_upserts_and_commits(self):
        repo = _make_repo(server_id=1)
        sf, rf = _make_session_factory(repo)
        redis = _make_redis(is_new=True)
        handler = make_inventory_handler(sf, rf, redis)
        await handler(_make_message(_INV_PAYLOAD))
        repo.upsert_server.assert_called_once()

    async def test_inventory_success_sets_redis_online_key(self):
        repo = _make_repo(server_id=7)
        sf, rf = _make_session_factory(repo)
        redis = _make_redis(is_new=True)
        handler = make_inventory_handler(sf, rf, redis)
        await handler(_make_message(_INV_PAYLOAD))
        redis.set.assert_called()
        # 첫 번째 호출이 idempotent SET, 두 번째가 online SET
        calls = redis.set.call_args_list
        online_calls = [c for c in calls if "online:7" in str(c)]
        assert len(online_calls) == 1

    async def test_invalid_json_raises_without_db_call(self):
        repo = _make_repo()
        sf, rf = _make_session_factory(repo)
        redis = _make_redis()
        handler = make_inventory_handler(sf, rf, redis)
        with pytest.raises(Exception):
            await handler(_make_message(b"not-json"))
        repo.upsert_server.assert_not_called()

    async def test_duplicate_message_skips_upsert(self):
        repo = _make_repo()
        sf, rf = _make_session_factory(repo)
        redis = _make_redis(is_new=False)  # SET NX → False (이미 처리됨)
        handler = make_inventory_handler(sf, rf, redis)
        await handler(_make_message(_INV_PAYLOAD))
        repo.upsert_server.assert_not_called()

    async def test_db_error_retries_3_times(self):
        repo = _make_repo()
        repo.upsert_server = AsyncMock(side_effect=Exception("db error"))
        sf, rf = _make_session_factory(repo)
        redis = _make_redis(is_new=True)
        handler = make_inventory_handler(sf, rf, redis)
        with patch("consumer.handler.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(Exception):
                await handler(_make_message(_INV_PAYLOAD))
        assert repo.upsert_server.call_count == 3

    async def test_db_error_exponential_backoff(self):
        repo = _make_repo()
        repo.upsert_server = AsyncMock(side_effect=Exception("db error"))
        sf, rf = _make_session_factory(repo)
        redis = _make_redis(is_new=True)
        handler = make_inventory_handler(sf, rf, redis)
        sleep_mock = AsyncMock()
        with patch("consumer.handler.asyncio.sleep", sleep_mock):
            with pytest.raises(Exception):
                await handler(_make_message(_INV_PAYLOAD))
        # 3회 시도 → 2회 sleep (0,1 → 2^0=1s, 2^1=2s)
        assert sleep_mock.call_count == 2
        assert sleep_mock.call_args_list[0].args[0] == 1
        assert sleep_mock.call_args_list[1].args[0] == 2

    async def test_succeeds_on_third_attempt(self):
        repo = _make_repo()
        repo.upsert_server = AsyncMock(
            side_effect=[Exception("fail1"), Exception("fail2"), 1]
        )
        sf, rf = _make_session_factory(repo)
        redis = _make_redis(is_new=True)
        handler = make_inventory_handler(sf, rf, redis)
        with patch("consumer.handler.asyncio.sleep", new_callable=AsyncMock):
            await handler(_make_message(_INV_PAYLOAD))
        assert repo.upsert_server.call_count == 3


class TestMetricsHandler:
    async def test_known_server_inserts_metric(self):
        repo = _make_repo(server_id=1)
        sf, rf = _make_session_factory(repo)
        redis = _make_redis(is_new=True)
        handler = make_metrics_handler(sf, rf, redis)
        await handler(_make_message(_MET_PAYLOAD))
        repo.insert_metric.assert_called_once()

    async def test_known_server_sets_online_key(self):
        repo = _make_repo(server_id=5)
        sf, rf = _make_session_factory(repo)
        redis = _make_redis(is_new=True)
        handler = make_metrics_handler(sf, rf, redis)
        await handler(_make_message(_MET_PAYLOAD))
        online_calls = [c for c in redis.set.call_args_list if "online:5" in str(c)]
        assert len(online_calls) == 1

    async def test_known_server_invalidates_metrics_cache(self):
        repo = _make_repo(server_id=5)
        sf, rf = _make_session_factory(repo)
        redis = _make_redis(is_new=True)
        handler = make_metrics_handler(sf, rf, redis)
        await handler(_make_message(_MET_PAYLOAD))
        redis.delete.assert_called_once()
        assert "cache:metrics:5" in str(redis.delete.call_args)

    async def test_known_server_publishes_event(self):
        repo = _make_repo(server_id=5)
        sf, rf = _make_session_factory(repo)
        redis = _make_redis(is_new=True)
        handler = make_metrics_handler(sf, rf, redis)
        await handler(_make_message(_MET_PAYLOAD))
        redis.publish.assert_called_once()

    async def test_unknown_server_drops_silently(self):
        repo = _make_repo(server_id=None)
        sf, rf = _make_session_factory(repo)
        redis = _make_redis(is_new=True)
        handler = make_metrics_handler(sf, rf, redis)
        await handler(_make_message(_MET_PAYLOAD))
        repo.insert_metric.assert_not_called()
        redis.publish.assert_not_called()

    async def test_invalid_json_raises_without_db_call(self):
        repo = _make_repo()
        sf, rf = _make_session_factory(repo)
        handler = make_metrics_handler(sf, rf, _make_redis())
        with pytest.raises(Exception):
            await handler(_make_message(b"{bad}"))
        repo.insert_metric.assert_not_called()

    async def test_duplicate_message_skips_insert(self):
        repo = _make_repo()
        sf, rf = _make_session_factory(repo)
        redis = _make_redis(is_new=False)
        handler = make_metrics_handler(sf, rf, redis)
        await handler(_make_message(_MET_PAYLOAD))
        repo.insert_metric.assert_not_called()

    async def test_db_error_retries_3_times(self):
        repo = _make_repo(server_id=1)
        repo.find_server_id = AsyncMock(side_effect=Exception("db error"))
        sf, rf = _make_session_factory(repo)
        redis = _make_redis(is_new=True)
        handler = make_metrics_handler(sf, rf, redis)
        with patch("consumer.handler.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(Exception):
                await handler(_make_message(_MET_PAYLOAD))
        assert repo.find_server_id.call_count == 3


class TestErrorHandler:
    async def test_valid_message_only_logs_no_db(self):
        redis = _make_redis(is_new=True)
        handler = make_error_handler(redis)
        # DB 호출 없음, 예외 없음
        await handler(_make_message(_ERR_PAYLOAD))

    async def test_invalid_json_raises(self):
        handler = make_error_handler(_make_redis())
        with pytest.raises(Exception):
            await handler(_make_message(b"garbage"))

    async def test_duplicate_message_skips(self):
        redis = _make_redis(is_new=False)
        handler = make_error_handler(redis)
        # 예외 없이 조기 리턴
        await handler(_make_message(_ERR_PAYLOAD))