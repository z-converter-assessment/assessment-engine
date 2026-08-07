"""시간 불변식 경고의 쿨다운과 fail-open 단위 테스트."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch

from assessment_engine.consumer.handlers._common import _log_time_invariants
from tests.factories import make_metrics

if TYPE_CHECKING:
    from assessment_engine.consumer.schemas import AgentMessageBase


def _normal_msg():
    boot = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    started = boot + timedelta(seconds=10)
    collected = started + timedelta(minutes=1)
    return make_metrics(collected_at=collected, boot_time=boot, agent_started_at=started)


def _violated_msg():
    boot = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    collected = boot + timedelta(minutes=1)
    started = collected + timedelta(minutes=5)
    return make_metrics(collected_at=collected, boot_time=boot, agent_started_at=started)


async def test_invariant_normal_no_redis_call_no_log():
    redis = AsyncMock()
    msg = _attach_identity(_normal_msg(), "m-normal")
    with patch("assessment_engine.consumer.handlers._common.logger") as mock_logger:
        await _log_time_invariants(redis, msg)
    redis.set.assert_not_awaited()
    mock_logger.warning.assert_not_called()


def _attach_identity(msg: object, agent_id: str, hostname: str = "test-host-01") -> AgentMessageBase:
    stub = cast("Any", msg)
    stub.agent_id = agent_id
    stub.hostname = hostname
    return cast("AgentMessageBase", msg)


async def test_first_violation_sets_cooldown_and_logs():
    redis = AsyncMock()
    redis.set.return_value = True
    msg = _violated_msg()
    msg = _attach_identity(msg, "m-first")
    with patch("assessment_engine.consumer.handlers._common.logger") as mock_logger:
        await _log_time_invariants(redis, msg)
    redis.set.assert_awaited_once()
    assert mock_logger.warning.called


async def test_second_violation_within_cooldown_silent_skip():
    redis = AsyncMock()
    redis.set.return_value = False
    msg = _violated_msg()
    msg = _attach_identity(msg, "m-cooldown")
    with patch("assessment_engine.consumer.handlers._common.logger") as mock_logger:
        await _log_time_invariants(redis, msg)
    redis.set.assert_awaited_once()
    mock_logger.warning.assert_not_called()


async def test_redis_failure_fails_open_and_logs():
    from redis.exceptions import RedisError

    redis = AsyncMock()
    redis.set.side_effect = RedisError("connection lost")
    msg = _violated_msg()
    msg = _attach_identity(msg, "m-redisdown")
    with patch("assessment_engine.consumer.handlers._common.logger") as mock_logger:
        await _log_time_invariants(redis, msg)
    assert mock_logger.warning.called, "Redis 장애 시 fail-open으로 로그는 그대로 출력해야 함"


async def test_boot_time_after_agent_started_logs_specific_message():
    redis = AsyncMock()
    redis.set.return_value = True
    boot = datetime(2026, 5, 1, 0, 5, tzinfo=UTC)
    started = boot - timedelta(seconds=10)
    collected = boot + timedelta(minutes=1)
    msg = make_metrics(collected_at=collected, boot_time=boot, agent_started_at=started)
    msg = _attach_identity(msg, "m-boot-after")
    with patch("assessment_engine.consumer.handlers._common.logger") as mock_logger:
        await _log_time_invariants(redis, msg)
    assert mock_logger.warning.call_count == 1
    call_args = mock_logger.warning.call_args
    assert "boot_time>agent_started_at" in call_args.args[0]
