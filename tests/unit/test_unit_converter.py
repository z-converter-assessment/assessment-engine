import pytest

from assessment_engine.web.services.unit_converter import (
    bytes_to_gb,
    bytes_to_gib,
    usage_pct,
)


@pytest.mark.parametrize(
    ("b", "expected"),
    [
        (None, None),
        (0, 0.0),
        (10**9, 0.93),
        (50 * 10**9, 46.57),
        (1_073_741_824, 1.0),
    ],
)
def test_bytes_to_gb(b: int | None, expected: float | None):
    assert bytes_to_gb(b) == expected


@pytest.mark.parametrize(
    ("b", "expected"),
    [
        (None, None),
        (0, None),
        (1024**3, 1.0),
        (8 * 1024**3, 8.0),
    ],
)
def test_bytes_to_gib(b: int | None, expected: float | None):
    assert bytes_to_gib(b) == expected


@pytest.mark.parametrize(
    ("used", "total", "expected"),
    [
        (None, 100, None),
        (50, None, None),
        (50, 0, None),
        (50, 100, 50.0),
        (75, 100, 75.0),
        (110, 100, 110.0),
        (-10, 100, 0.0),
    ],
)
def test_usage_pct(used: int | None, total: int | None, expected: float | None):
    assert usage_pct(used, total) == expected
