"""boot_time 지터 허용 경계와 카운터 리셋 판정을 검증한다."""

from datetime import UTC, datetime, timedelta

from assessment_engine.domain.boot_time import (
    BOOT_TIME_JITTER_TOLERANCE,
    boot_time_changed,
    is_counter_reset,
)

_BASE = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)


def test_tolerance_is_five_seconds():
    assert timedelta(seconds=5) == BOOT_TIME_JITTER_TOLERANCE


def test_changed_identical_boot_false():
    assert boot_time_changed(_BASE, _BASE) is False


def test_changed_within_jitter_false():
    assert boot_time_changed(_BASE, _BASE + timedelta(seconds=1)) is False
    assert boot_time_changed(_BASE, _BASE - timedelta(seconds=1)) is False


def test_changed_exactly_at_tolerance_false():
    assert boot_time_changed(_BASE, _BASE + timedelta(seconds=5)) is False


def test_changed_just_over_tolerance_true():
    over = _BASE + timedelta(seconds=5, microseconds=1)
    assert boot_time_changed(_BASE, over) is True


def test_changed_reboot_minutes_jump_true():
    assert boot_time_changed(_BASE, _BASE + timedelta(minutes=10)) is True


def test_changed_order_independent():
    later = _BASE + timedelta(minutes=10)
    assert boot_time_changed(_BASE, later) is True
    assert boot_time_changed(later, _BASE) is True


def test_changed_both_none_false():
    assert boot_time_changed(None, None) is False


def test_changed_value_to_null_true():
    assert boot_time_changed(_BASE, None) is True


def test_changed_null_to_value_true():
    assert boot_time_changed(None, _BASE) is True


def test_reset_identical_false():
    assert is_counter_reset(_BASE, _BASE) is False


def test_reset_within_jitter_false():
    assert is_counter_reset(_BASE, _BASE + timedelta(seconds=2)) is False


def test_reset_exactly_at_tolerance_false():
    assert is_counter_reset(_BASE + timedelta(seconds=5), _BASE) is False


def test_reset_just_over_tolerance_true():
    cur = _BASE + timedelta(seconds=5, microseconds=1)
    assert is_counter_reset(cur, _BASE) is True


def test_reset_order_independent():
    later = _BASE + timedelta(minutes=10)
    assert is_counter_reset(later, _BASE) is True
    assert is_counter_reset(_BASE, later) is True


def test_reset_cur_none_false():
    assert is_counter_reset(None, _BASE) is False


def test_reset_prev_none_false():
    assert is_counter_reset(_BASE, None) is False


def test_reset_both_none_false():
    assert is_counter_reset(None, None) is False
