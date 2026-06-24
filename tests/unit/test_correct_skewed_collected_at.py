"""_correct_skewed_collected_at — collected_at 시계오차 수신 경계 보정 (미래·과거 양방향, #F2).

정책:
- |collected_at - received_at| > threshold (미래 또는 과거) → collected_at = received_at 보정
- |skew| <= threshold (정상 전송 지연) → 무보정
- boot_time·agent_started_at 은 미보정 (부팅 내 불변 가정 보존)
- 보정 시 F7 쿨다운 로그. 쿨다운 안이면 보정은 유지하고 로그만 억제. Redis 장애 fail-open(로그 출력).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from assessment_engine.consumer.handlers._common import _correct_skewed_collected_at
from assessment_engine.consumer.settings import consumer_settings
from tests.factories import make_metrics

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
_THRESHOLD = consumer_settings.clock_skew_threshold_sec


def _msg(collected_at, composite_id="m-clk", hostname="host-01"):
    """수신 메시지 fixture — make_metrics 에 MessageBase identity 동적 부여."""
    msg = make_metrics(collected_at=collected_at)
    msg.composite_id = composite_id
    msg.hostname = hostname
    return msg


async def test_future_beyond_threshold_corrected_and_logs():
    """미래 > threshold → collected_at = received_at 보정, boot/agent 미보정, 로그."""
    redis = AsyncMock()
    redis.set.return_value = True
    msg = _msg(_NOW + timedelta(seconds=_THRESHOLD + 60))
    boot, started = msg.boot_time, msg.agent_started_at
    with patch("assessment_engine.consumer.handlers._common.logger") as mock_logger:
        await _correct_skewed_collected_at(redis, msg, _NOW)
    assert msg.collected_at == _NOW
    assert msg.boot_time == boot and msg.agent_started_at == started  # 미보정
    redis.set.assert_awaited_once()
    assert mock_logger.warning.called


async def test_past_beyond_threshold_corrected_and_logs():
    """과거 > threshold (게스트 시계 뒤처짐) → collected_at = received_at 보정, 로그."""
    redis = AsyncMock()
    redis.set.return_value = True
    msg = _msg(_NOW - timedelta(seconds=_THRESHOLD + 60), "m-past")
    with patch("assessment_engine.consumer.handlers._common.logger") as mock_logger:
        await _correct_skewed_collected_at(redis, msg, _NOW)
    assert msg.collected_at == _NOW
    redis.set.assert_awaited_once()
    assert mock_logger.warning.called


async def test_future_within_threshold_unchanged():
    """미래 < threshold → 무보정, Redis 호출·로그 0."""
    redis = AsyncMock()
    near = _NOW + timedelta(seconds=_THRESHOLD - 1)
    msg = _msg(near, "m-near")
    with patch("assessment_engine.consumer.handlers._common.logger") as mock_logger:
        await _correct_skewed_collected_at(redis, msg, _NOW)
    assert msg.collected_at == near
    redis.set.assert_not_awaited()
    mock_logger.warning.assert_not_called()


async def test_past_within_threshold_unchanged():
    """과거 < threshold (정상 전송 지연) → 무보정."""
    redis = AsyncMock()
    near = _NOW - timedelta(seconds=_THRESHOLD - 1)
    msg = _msg(near, "m-pastnear")
    await _correct_skewed_collected_at(redis, msg, _NOW)
    assert msg.collected_at == near
    redis.set.assert_not_awaited()


async def test_exactly_threshold_unchanged():
    """경계: |skew| == threshold → 보정 안 함 (조건은 strictly greater)."""
    redis = AsyncMock()
    boundary = _NOW + timedelta(seconds=_THRESHOLD)
    msg = _msg(boundary, "m-bound")
    await _correct_skewed_collected_at(redis, msg, _NOW)
    assert msg.collected_at == boundary
    redis.set.assert_not_awaited()


async def test_corrected_but_cooldown_suppresses_log():
    """쿨다운 안(SET NX False) → 보정은 유지, 로그만 억제."""
    redis = AsyncMock()
    redis.set.return_value = False
    msg = _msg(_NOW + timedelta(hours=5), "m-cool")
    with patch("assessment_engine.consumer.handlers._common.logger") as mock_logger:
        await _correct_skewed_collected_at(redis, msg, _NOW)
    assert msg.collected_at == _NOW  # 보정은 됨
    redis.set.assert_awaited_once()
    mock_logger.warning.assert_not_called()


async def test_redis_failure_fails_open_corrected_and_logs():
    """Redis 장애(safe_set_nx None) → 보정 + fail-open 로그."""
    from redis.exceptions import RedisError

    redis = AsyncMock()
    redis.set.side_effect = RedisError("connection lost")
    msg = _msg(_NOW + timedelta(hours=5), "m-rdown")
    with patch("assessment_engine.consumer.handlers._common.logger") as mock_logger:
        await _correct_skewed_collected_at(redis, msg, _NOW)
    assert msg.collected_at == _NOW
    assert mock_logger.warning.called, "Redis 장애 시 fail-open 으로 로그 출력"
