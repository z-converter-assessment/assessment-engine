"""boot_time 동일 부팅 판정 헬퍼 단위테스트 (CLAUDE.md #B counter reset 정밀 식별).

측정 지터(now - uptime 산출로 매 수집 +/-1초)를 재부팅으로 오판하지 않도록
BOOT_TIME_JITTER_TOLERANCE(=5초) 경계에서 동작을 encode.

- is_counter_reset: 보수적 — NULL 한쪽이면 False, 허용치 초과만 True.
- boot_time_changed: 적극적 — 값<->NULL 전환도 True, 둘 다 NULL 이면 False.
"""

from datetime import UTC, datetime, timedelta

from assessment_engine.domain.boot_time import (
    BOOT_TIME_JITTER_TOLERANCE,
    boot_time_changed,
    is_counter_reset,
)

_BASE = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)


def test_tolerance_is_five_seconds():
    """허용치 상수 계약 — SQL bound parameter 파생 단일 진실 (5초)."""
    assert timedelta(seconds=5) == BOOT_TIME_JITTER_TOLERANCE


# --- boot_time_changed ---


def test_changed_identical_boot_false():
    """정확히 같은 boot_time -> 변경 아님."""
    assert boot_time_changed(_BASE, _BASE) is False


def test_changed_within_jitter_false():
    """지터 범위(1초 흔들림) -> 변경 아님 (지터 흡수)."""
    assert boot_time_changed(_BASE, _BASE + timedelta(seconds=1)) is False
    assert boot_time_changed(_BASE, _BASE - timedelta(seconds=1)) is False


def test_changed_exactly_at_tolerance_false():
    """차이가 정확히 허용치(5초) -> 경계는 '> tolerance' 라 변경 아님."""
    assert boot_time_changed(_BASE, _BASE + timedelta(seconds=5)) is False


def test_changed_just_over_tolerance_true():
    """허용치 초과(5초 + 1마이크로초) -> 변경."""
    over = _BASE + timedelta(seconds=5, microseconds=1)
    assert boot_time_changed(_BASE, over) is True


def test_changed_reboot_minutes_jump_true():
    """분 단위 점프(실제 재부팅) -> 변경."""
    assert boot_time_changed(_BASE, _BASE + timedelta(minutes=10)) is True


def test_changed_order_independent():
    """prev/new 순서 무관 (절대값 비교)."""
    later = _BASE + timedelta(minutes=10)
    assert boot_time_changed(_BASE, later) is True
    assert boot_time_changed(later, _BASE) is True


def test_changed_both_none_false():
    """둘 다 NULL -> 동일(변경 아님)."""
    assert boot_time_changed(None, None) is False


def test_changed_value_to_null_true():
    """값 -> NULL 전환은 의미있는 변경 (True)."""
    assert boot_time_changed(_BASE, None) is True


def test_changed_null_to_value_true():
    """NULL -> 값 전환은 의미있는 변경 (True)."""
    assert boot_time_changed(None, _BASE) is True


# --- is_counter_reset ---


def test_reset_identical_false():
    """같은 boot_time -> 리셋 아님."""
    assert is_counter_reset(_BASE, _BASE) is False


def test_reset_within_jitter_false():
    """지터 범위 -> 리셋 아님 (delta 유효)."""
    assert is_counter_reset(_BASE, _BASE + timedelta(seconds=2)) is False


def test_reset_exactly_at_tolerance_false():
    """정확히 허용치(5초) -> 경계는 '> tolerance' 라 리셋 아님."""
    assert is_counter_reset(_BASE + timedelta(seconds=5), _BASE) is False


def test_reset_just_over_tolerance_true():
    """허용치 초과 -> 재부팅(counter reset)."""
    cur = _BASE + timedelta(seconds=5, microseconds=1)
    assert is_counter_reset(cur, _BASE) is True


def test_reset_order_independent():
    """cur/prev 순서 무관 (절대값 비교)."""
    later = _BASE + timedelta(minutes=10)
    assert is_counter_reset(later, _BASE) is True
    assert is_counter_reset(_BASE, later) is True


def test_reset_cur_none_false():
    """cur NULL -> 보수적으로 단정 못 함 (False, d<0 fallback)."""
    assert is_counter_reset(None, _BASE) is False


def test_reset_prev_none_false():
    """prev NULL -> 보수적으로 단정 못 함 (False)."""
    assert is_counter_reset(_BASE, None) is False


def test_reset_both_none_false():
    """둘 다 NULL -> False."""
    assert is_counter_reset(None, None) is False
