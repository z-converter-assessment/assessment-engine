"""_track_agent_restart 4 분기 + _check_idempotent fail-open coalescing (CLAUDE.md #D2·F7).

safe_* helper(safe_get·safe_incr_with_ttl·safe_set·safe_set_nx)를 _common 네임스페이스에서
patch — Redis 파이프라인 mock 없이 분기 로직만 검증 (unit).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from assessment_engine.consumer.handlers import _common
from assessment_engine.consumer.handlers._common import _check_idempotent, _track_agent_restart
from assessment_engine.consumer.settings import consumer_settings

pytestmark = pytest.mark.asyncio

_STARTED = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)


# ─── _track_agent_restart: 4 분기 ────────────────────────────────────────────


async def test_track_restart_none_started_skips_all_redis():
    """분기1: agent_started_at None(발행 기동시각 미상) → 즉시 return, Redis 호출 0."""
    redis = AsyncMock()
    with (
        patch.object(_common, "safe_get", AsyncMock()) as mget,
        patch.object(_common, "safe_incr_with_ttl", AsyncMock()) as mincr,
        patch.object(_common, "safe_set", AsyncMock()) as mset,
        patch.object(_common, "logger") as mlog,
    ):
        await _track_agent_restart(redis, server_id=1, agent_id="a-none", agent_started_at=None)
    mget.assert_not_awaited()
    mincr.assert_not_awaited()
    mset.assert_not_awaited()
    mlog.warning.assert_not_called()


async def test_track_restart_first_observation_no_incr_sets_last():
    """분기2: 직전 값 없음(safe_get None) → INCR 안 함, last_key 만 저장, alert 없음."""
    redis = AsyncMock()
    with (
        patch.object(_common, "safe_get", AsyncMock(return_value=None)),
        patch.object(_common, "safe_incr_with_ttl", AsyncMock()) as mincr,
        patch.object(_common, "safe_set", AsyncMock()) as mset,
        patch.object(_common, "logger") as mlog,
    ):
        await _track_agent_restart(redis, server_id=7, agent_id="a-first", agent_started_at=_STARTED)
    mincr.assert_not_awaited()
    mset.assert_awaited_once()
    # last_key 에 현재 iso 저장
    assert mset.await_args.args[2] == _STARTED.isoformat()
    mlog.warning.assert_not_called()


async def test_track_restart_same_start_no_incr_no_alert():
    """분기3: 직전 == 현재(재시작 아님) → INCR 안 함, alert 없음, last_key 갱신."""
    redis = AsyncMock()
    with (
        patch.object(_common, "safe_get", AsyncMock(return_value=_STARTED.isoformat())),
        patch.object(_common, "safe_incr_with_ttl", AsyncMock()) as mincr,
        patch.object(_common, "safe_set", AsyncMock()) as mset,
        patch.object(_common, "logger") as mlog,
    ):
        await _track_agent_restart(redis, server_id=7, agent_id="a-same", agent_started_at=_STARTED)
    mincr.assert_not_awaited()
    mset.assert_awaited_once()
    mlog.warning.assert_not_called()


async def test_track_restart_changed_below_threshold_no_alert():
    """분기4a: 직전 != 현재(재시작) but count < threshold → INCR 하되 alert 없음."""
    redis = AsyncMock()
    below = consumer_settings.agent_restart_alert_threshold - 1
    with (
        patch.object(_common, "safe_get", AsyncMock(return_value="2026-04-30T23:00:00+00:00")),
        patch.object(_common, "safe_incr_with_ttl", AsyncMock(return_value=below)) as mincr,
        patch.object(_common, "safe_set", AsyncMock()) as mset,
        patch.object(_common, "logger") as mlog,
    ):
        await _track_agent_restart(redis, server_id=9, agent_id="a-below", agent_started_at=_STARTED)
    mincr.assert_awaited_once()
    mlog.warning.assert_not_called()
    mset.assert_awaited_once()


async def test_track_restart_changed_at_threshold_alerts():
    """분기4b: 직전 != 현재 + count >= threshold → warning(crash loop alert)."""
    redis = AsyncMock()
    at = consumer_settings.agent_restart_alert_threshold
    with (
        patch.object(_common, "safe_get", AsyncMock(return_value="2026-04-30T23:00:00+00:00")),
        patch.object(_common, "safe_incr_with_ttl", AsyncMock(return_value=at)) as mincr,
        patch.object(_common, "safe_set", AsyncMock()) as mset,
        patch.object(_common, "logger") as mlog,
    ):
        await _track_agent_restart(redis, server_id=9, agent_id="a-alert", agent_started_at=_STARTED)
    mincr.assert_awaited_once()
    assert mlog.warning.call_count == 1
    assert "agent restart frequency alert" in mlog.warning.call_args.args[0]
    mset.assert_awaited_once()


async def test_track_restart_changed_counter_redis_failure_no_alert():
    """분기4c: 재시작 감지했으나 카운터 INCR 이 Redis 장애(None) → alert 없음, last_key 는 갱신(fail-open)."""
    redis = AsyncMock()
    with (
        patch.object(_common, "safe_get", AsyncMock(return_value="2026-04-30T23:00:00+00:00")),
        patch.object(_common, "safe_incr_with_ttl", AsyncMock(return_value=None)) as mincr,
        patch.object(_common, "safe_set", AsyncMock()) as mset,
        patch.object(_common, "logger") as mlog,
    ):
        await _track_agent_restart(redis, server_id=9, agent_id="a-cfail", agent_started_at=_STARTED)
    mincr.assert_awaited_once()
    mlog.warning.assert_not_called()
    mset.assert_awaited_once()


# ─── _check_idempotent: #D2 fail-open coalescing ─────────────────────────────

_MSG_ID = UUID("12345678-1234-5678-1234-567812345678")


async def test_check_idempotent_first_returns_true():
    """첫 처리: safe_set_nx True → True."""
    redis = AsyncMock()
    with patch.object(_common, "safe_set_nx", AsyncMock(return_value=True)) as msnx:
        result = await _check_idempotent(redis, _MSG_ID)
    assert result is True
    # message_id.hex 로 키 포맷
    assert msnx.await_args.args[1] == consumer_settings.redis_key_idempotent.format(_MSG_ID.hex)


async def test_check_idempotent_duplicate_returns_false():
    """중복: safe_set_nx False → False (재처리 차단)."""
    redis = AsyncMock()
    with patch.object(_common, "safe_set_nx", AsyncMock(return_value=False)):
        result = await _check_idempotent(redis, _MSG_ID)
    assert result is False


async def test_check_idempotent_redis_failure_fails_open_true():
    """Redis 장애: safe_set_nx None → True (fail-open, 2단 DB UNIQUE 가 중복 흡수)."""
    redis = AsyncMock()
    with patch.object(_common, "safe_set_nx", AsyncMock(return_value=None)):
        result = await _check_idempotent(redis, _MSG_ID)
    assert result is True
