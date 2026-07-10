"""unit_converter.py — 단위 변환 함수 단위 테스트 (v2 wire 계약).

공개 함수 = bytes_to_gb / bytes_to_gib / bytes_to_mib / usage_pct.
메모리/스왑은 By 단위 binary GiB(bytes_to_gib), disk IO rate 는 counter_agg 사전집계라
unit_converter 에 rate 변환 함수 없음.
"""

import pytest

from assessment_engine.web.services.unit_converter import (
    bytes_to_gb,
    bytes_to_gib,
    bytes_to_mib,
    usage_pct,
)

# ─── bytes_to_gb ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "b, expected",
    [
        (None, None),
        (0, 0.0),
        (10**9, 1.0),  # 디스크는 decimal GB(10^9) — 산업 표준. 메모리(bytes_to_gib)만 binary 유지.
        (50 * 10**9, 50.0),
        (1_073_741_824, 1.07),  # 1 GiB = 1.073e9 B -> 1.07 GB (round 2자리)
    ],
)
def test_bytes_to_gb(b, expected):
    assert bytes_to_gb(b) == expected


# ─── bytes_to_gib (v2: 메모리/스왑 binary GiB, By 단위) ──────────────────────


@pytest.mark.parametrize(
    "b, expected",
    [
        (None, None),
        (0, None),  # falsy → None (mem_total_bytes=0 같은 비정상)
        (1024**3, 1.0),  # 1 GiB = 1024^3 B -> 1.0
        (8 * 1024**3, 8.0),
    ],
)
def test_bytes_to_gib(b, expected):
    assert bytes_to_gib(b) == expected


# ─── bytes_to_mib (v2: export spec.memory_mb, binary MiB int) ────────────────


@pytest.mark.parametrize(
    "b, expected",
    [
        (None, None),
        (0, None),  # falsy → None
        (1024**2, 1),  # 1 MiB -> 1 (int 반환)
        (8 * 1024**2, 8),
        (1024**3, 1024),  # 1 GiB = 1024 MiB
    ],
)
def test_bytes_to_mib(b, expected):
    assert bytes_to_mib(b) == expected


# ─── usage_pct ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "used, total, expected",
    [
        (None, 100, None),
        (50, None, None),
        (50, 0, None),  # total=0 falsy → None (0으로 나누기 회피)
        (50, 100, 50.0),
        (75, 100, 75.0),
        (110, 100, 110.0),  # 100% 초과는 그대로 (clamping은 호출자 책임)
        (-10, 100, 0.0),  # 음수는 0으로 clamp (max 0.0)
    ],
)
def test_usage_pct(used, total, expected):
    assert usage_pct(used, total) == expected
