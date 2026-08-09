"""boot_time jitter를 흡수하는 재부팅 판정 규칙."""

from datetime import datetime, timedelta

BOOT_TIME_JITTER_TOLERANCE = timedelta(seconds=5)


def is_counter_reset(cur_boot: datetime | None, prev_boot: datetime | None) -> bool:
    """두 boot_time 차이가 재부팅으로 볼 허용치를 넘는지 반환한다."""
    if cur_boot is None or prev_boot is None:
        return False
    return abs(cur_boot - prev_boot) > BOOT_TIME_JITTER_TOLERANCE


def boot_time_changed(prev_boot: datetime | None, new_boot: datetime | None) -> bool:
    """inventory 이력용 boot_time 변경 여부를 반환한다. 한쪽만 None이면 변경이다."""
    if prev_boot is None and new_boot is None:
        return False
    if prev_boot is None or new_boot is None:
        return True
    return abs(prev_boot - new_boot) > BOOT_TIME_JITTER_TOLERANCE
