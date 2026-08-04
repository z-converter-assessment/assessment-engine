"""_log_time_invariants 쿨다운 분기 검증 (CLAUDE.md F11).

쿨다운 룰:
- invariant 정상 → silent return (Redis 호출 0건)
- 첫 위반 → SET NX True → 로그 발생
- 같은 서버 두 번째 위반 (쿨다운 안) → SET NX False → silent skip
- Redis 장애 (set_result None) → 로그 발생 (fail-open)
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from assessment_engine.consumer.handlers._common import _log_time_invariants
from assessment_engine.consumer.schemas import AgentMessageBase
from tests.factories import make_metrics

pytestmark = pytest.mark.asyncio


def _normal_msg():
    """invariant 정상: boot_time <= agent_started_at <= collected_at."""
    boot = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    started = boot + timedelta(seconds=10)
    collected = started + timedelta(minutes=1)
    return make_metrics(collected_at=collected, boot_time=boot, agent_started_at=started)


def _violated_msg():
    """invariant 위반: agent_started_at > collected_at (가장 흔한 시계 동기화 문제)."""
    boot = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    collected = boot + timedelta(minutes=1)
    started = collected + timedelta(minutes=5)  # collected보다 미래
    return make_metrics(collected_at=collected, boot_time=boot, agent_started_at=started)


async def test_invariant_normal_no_redis_call_no_log():
    """정상 메시지 → 즉시 return, Redis 호출도 로그도 없음."""
    redis = AsyncMock()
    msg = _attach_identity(_normal_msg(), "m-normal")
    with patch("assessment_engine.consumer.handlers._common.logger") as mock_logger:
        await _log_time_invariants(redis, msg)
    redis.set.assert_not_awaited()
    mock_logger.warning.assert_not_called()


def _attach_identity(msg: object, agent_id: str, hostname: str = "test-host-01") -> AgentMessageBase:
    """수집 DTO 대역에 identity 를 붙여 시간축 입력으로 넘긴다.

    _log_time_invariants 가 읽는 축(agent_id·hostname·boot_time·agent_started_at·collected_at)이
    factory 산출물에 같은 이름으로 있어 그대로 통한다. cooldown_key 가 (agent_id, hostname) 복합이라 둘 다 필요.
    """
    stub = cast(Any, msg)
    stub.agent_id = agent_id
    stub.hostname = hostname
    return cast(AgentMessageBase, msg)


async def test_first_violation_sets_cooldown_and_logs():
    """첫 위반 → SET NX True → 로그."""
    redis = AsyncMock()
    redis.set.return_value = True  # SET NX 성공 (첫 위반)
    msg = _violated_msg()
    msg = _attach_identity(msg, "m-first")
    with patch("assessment_engine.consumer.handlers._common.logger") as mock_logger:
        await _log_time_invariants(redis, msg)
    redis.set.assert_awaited_once()
    assert mock_logger.warning.called


async def test_second_violation_within_cooldown_silent_skip():
    """쿨다운 안 두 번째 위반 → SET NX False → 로그 발생 안 함."""
    redis = AsyncMock()
    redis.set.return_value = False  # SET NX 실패 (이미 쿨다운 키 존재)
    msg = _violated_msg()
    msg = _attach_identity(msg, "m-cooldown")
    with patch("assessment_engine.consumer.handlers._common.logger") as mock_logger:
        await _log_time_invariants(redis, msg)
    redis.set.assert_awaited_once()
    mock_logger.warning.assert_not_called()


async def test_redis_failure_fails_open_and_logs():
    """Redis 장애 (safe_set_nx None) → 쿨다운 적용 못 하고 로그 발생 (fail-open)."""
    from redis.exceptions import RedisError

    redis = AsyncMock()
    redis.set.side_effect = RedisError("connection lost")
    msg = _violated_msg()
    msg = _attach_identity(msg, "m-redisdown")
    with patch("assessment_engine.consumer.handlers._common.logger") as mock_logger:
        await _log_time_invariants(redis, msg)
    assert mock_logger.warning.called, "Redis 장애 시 fail-open으로 로그는 그대로 출력해야 함"


async def test_boot_time_after_agent_started_logs_specific_message():
    """boot_time > agent_started_at 변종 — systemd 시작 순서 비정상 케이스 분기."""
    redis = AsyncMock()
    redis.set.return_value = True
    boot = datetime(2026, 5, 1, 0, 5, tzinfo=UTC)
    started = boot - timedelta(seconds=10)  # boot 전에 agent 시작 (비정상)
    collected = boot + timedelta(minutes=1)
    msg = make_metrics(collected_at=collected, boot_time=boot, agent_started_at=started)
    msg = _attach_identity(msg, "m-boot-after")
    with patch("assessment_engine.consumer.handlers._common.logger") as mock_logger:
        await _log_time_invariants(redis, msg)
    # 두 케이스 중 boot_time>agent_started_at 분기만 발생 (agent_started_at < collected_at)
    assert mock_logger.warning.call_count == 1
    call_args = mock_logger.warning.call_args
    assert "boot_time>agent_started_at" in call_args.args[0]
