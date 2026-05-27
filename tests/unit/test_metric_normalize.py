"""메트릭 canonical 정규화 경계 — clamp_ceiling 불변식 + 매퍼 적용 (Windows 음수 used 방지)."""

from datetime import UTC, datetime

from assessment_engine.consumer.mappers import to_metric_create
from assessment_engine.consumer.metric_normalize import clamp_ceiling
from assessment_engine.consumer.schemas import MetricsInput


def test_clamp_ceiling_within_bound_passthrough():
    assert clamp_ceiling(100, 200, field="swap_free_kb", composite_id="c") == 100
    assert clamp_ceiling(200, 200, field="swap_free_kb", composite_id="c") == 200  # 동일은 허용


def test_clamp_ceiling_over_bound_clamps():
    # swap_free(512) > swap_total(256) -> 256 으로 클램프 (used = total - free = 0, 음수 방지)
    assert clamp_ceiling(512, 256, field="swap_free_kb", composite_id="c") == 256


def test_clamp_ceiling_none_passthrough():
    # 한쪽이라도 None 이면 미수집(NULL) 보존 — 0 으로 날조하지 않음
    assert clamp_ceiling(None, 256, field="swap_free_kb", composite_id="c") is None
    assert clamp_ceiling(512, None, field="swap_free_kb", composite_id="c") == 512


def _metrics(**over) -> MetricsInput:
    base = {
        "message_type": "metrics",
        "message_id": "00000000-0000-0000-0000-000000000001",
        "agent_version": "4.0.0",
        "hostname": "win-1",
        "composite_id": "z" + "0" * 63,
        "collected_at": datetime(2026, 5, 27, tzinfo=UTC),
        "boot_time": datetime(2026, 5, 1, tzinfo=UTC),
        "agent_started_at": datetime(2026, 5, 1, tzinfo=UTC),
    }
    base.update(over)
    return MetricsInput(**base)


def test_to_metric_create_clamps_negative_swap_source():
    """수집 진입에서 swap_free > swap_total 클램프 — 다운스트림 used 음수 원천 차단 (Windows pagefile 방어)."""
    m = _metrics(swap_total_kb=256, swap_free_kb=512)
    out = to_metric_create(m)
    assert out.swap_total_kb == 256
    assert out.swap_free_kb == 256  # 클램프됨 -> used(total-free)=0
    assert out.swap_total_kb - out.swap_free_kb == 0


def test_to_metric_create_clamps_mem_available_over_total():
    m = _metrics(mem_total_kb=1000, mem_available_kb=4000)
    out = to_metric_create(m)
    assert out.mem_available_kb == 1000  # 클램프 -> used >= 0


def test_to_metric_create_normal_values_unchanged():
    m = _metrics(swap_total_kb=2048, swap_free_kb=1024, mem_total_kb=8000, mem_available_kb=3000)
    out = to_metric_create(m)
    assert out.swap_free_kb == 1024
    assert out.mem_available_kb == 3000
