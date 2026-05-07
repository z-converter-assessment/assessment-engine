"""units.py — 단위 변환 함수 단위 테스트."""
import pytest

from assessment_engine.web.services.units import (
    bytes_to_gb,
    kb_to_gb,
    sector_to_kbps,
    usage_pct,
)


# ─── bytes_to_gb ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("b, expected", [
    (None, None),
    (0, 0.0),
    (1024 ** 3, 1.0),
    (50 * 1024 ** 3, 50.0),
    (1500_000_000, 1.4),  # round 2자리
])
def test_bytes_to_gb(b, expected):
    assert bytes_to_gb(b) == expected


# ─── kb_to_gb ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kb, expected", [
    (None, None),
    (0, None),  # falsy → None (mem_total_kb=0 같은 비정상)
    (1024 ** 2, 1.0),
    (8 * 1024 ** 2, 8.0),
])
def test_kb_to_gb(kb, expected):
    assert kb_to_gb(kb) == expected


# ─── usage_pct ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("used, total, expected", [
    (None, 100, None),
    (50, None, None),
    (50, 0, None),  # total=0 falsy → None (0으로 나누기 회피)
    (50, 100, 50.0),
    (75, 100, 75.0),
    (110, 100, 110.0),  # 100% 초과는 그대로 (clamping은 호출자 책임)
    (-10, 100, 0.0),    # 음수는 0으로 clamp (max 0.0)
])
def test_usage_pct(used, total, expected):
    assert usage_pct(used, total) == expected


# ─── sector_to_kbps ───────────────────────────────────────────────────────

def test_sector_to_kbps_normal():
    # 1024 sectors * 512 bytes = 524288 bytes = 512 KB. dt=1s → 512 kBps
    assert sector_to_kbps(1024, 0, 1.0) == 512.0


def test_sector_to_kbps_counter_reset_returns_none():
    # cur < prev → counter reset → None
    assert sector_to_kbps(100, 200, 1.0) is None


def test_sector_to_kbps_zero_dt_handled_by_caller():
    # dt=0이면 ZeroDivisionError — 호출자가 dt>0 보장 (현재 구현 그대로)
    with pytest.raises(ZeroDivisionError):
        sector_to_kbps(1024, 0, 0)