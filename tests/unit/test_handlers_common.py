"""에이전트 재시작 빈도와 멱등성 fail-open 단위 테스트."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

from assessment_engine.consumer.handlers import _common
from assessment_engine.consumer.handlers._common import _check_idempotent, _track_agent_restart
from assessment_engine.consumer.settings import get_consumer_settings

_STARTED = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)


async def test_track_restart_none_started_skips_all_redis():
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
    assert mset.await_args is not None
    assert mset.await_args.args[2] == _STARTED.isoformat()
    mlog.warning.assert_not_called()


async def test_track_restart_same_start_no_incr_no_alert():
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
    redis = AsyncMock()
    below = get_consumer_settings().agent_restart_alert_threshold - 1
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
    redis = AsyncMock()
    at = get_consumer_settings().agent_restart_alert_threshold
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


_MSG_ID = UUID("12345678-1234-5678-1234-567812345678")


async def test_check_idempotent_first_returns_true():
    redis = AsyncMock()
    with patch.object(_common, "safe_set_nx", AsyncMock(return_value=True)) as msnx:
        result = await _check_idempotent(redis, _MSG_ID)
    assert result is True
    assert msnx.await_args is not None
    assert msnx.await_args.args[1] == get_consumer_settings().redis_key_idempotent.format(_MSG_ID.hex)


async def test_check_idempotent_duplicate_returns_false():
    redis = AsyncMock()
    with patch.object(_common, "safe_set_nx", AsyncMock(return_value=False)):
        result = await _check_idempotent(redis, _MSG_ID)
    assert result is False


async def test_check_idempotent_redis_failure_fails_open_true():
    redis = AsyncMock()
    with patch.object(_common, "safe_set_nx", AsyncMock(return_value=None)):
        result = await _check_idempotent(redis, _MSG_ID)
    assert result is True
