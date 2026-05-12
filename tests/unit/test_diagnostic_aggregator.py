"""diagnostic.aggregator — 순수 변환 함수 단위 테스트 (ADR 0004)."""
import pytest

from assessment_engine.diagnostic.aggregator import (
    _server_recommendation,
    _summary,
    _top_actions,
)


# ─── _server_recommendation ──────────────────────────────────────────────

@pytest.mark.parametrize("classification, expected_action", [
    ("over_provisioned",  "downsize_cpu"),
    ("under_provisioned", "upsize_memory"),
    ("idle",              "shutdown_idle"),
    ("shutdown",          "shutdown_review"),
    ("optimal",           "no_action"),
    ("insufficient_data", "no_action"),
    ("unknown_label",     "no_action"),
])
def test_server_recommendation_action_mapping(classification, expected_action):
    assert _server_recommendation(classification) == {"action": expected_action}


# ─── _summary ─────────────────────────────────────────────────────────────

def test_summary_empty_lists():
    assert _summary([], []) == {"avg_p95": None, "median_p95": None, "peak_max": None}


def test_summary_basic():
    p95s = [10.0, 20.0, 30.0]
    peaks = [50.0, 75.0, 90.0]
    result = _summary(p95s, peaks)
    assert result["avg_p95"] == 20.0
    # median = sorted[len//2] = sorted[1] = 20.0
    assert result["median_p95"] == 20.0
    assert result["peak_max"] == 90.0


def test_summary_unsorted_input_normalized():
    """median 계산은 내부 정렬 의무 — 입력 순서 무관."""
    result = _summary([30.0, 10.0, 20.0], [90.0, 50.0, 75.0])
    assert result["median_p95"] == 20.0
    assert result["peak_max"] == 90.0


def test_summary_round_one_decimal():
    """round 1자리 — UI 표시 정밀도."""
    result = _summary([10.123, 20.456], [50.789])
    assert result["avg_p95"] == 15.3
    assert result["peak_max"] == 50.8


def test_summary_one_value_only_peak_or_p95():
    """p95만 있고 peak 없는 경우, 또는 반대 — 부분 None."""
    only_p95 = _summary([42.5], [])
    assert only_p95["avg_p95"] == 42.5
    assert only_p95["peak_max"] is None

    only_peak = _summary([], [88.0])
    assert only_peak["avg_p95"] is None
    assert only_peak["peak_max"] == 88.0


# ─── _top_actions ─────────────────────────────────────────────────────────

def test_top_actions_count_zero_filtered():
    """모든 count=0 → 빈 리스트."""
    counts = {"over_provisioned": 0, "under_provisioned": 0, "idle": 0, "shutdown": 0}
    assert _top_actions(counts) == []


def test_top_actions_sorted_desc():
    counts = {
        "over_provisioned":  5,
        "under_provisioned": 12,
        "idle":              3,
        "shutdown":          0,  # filtered
    }
    result = _top_actions(counts)
    assert [a["action_type"] for a in result] == [
        "upsize_memory",   # 12
        "downsize_cpu",    # 5
        "shutdown_idle",   # 3
    ]
    assert [a["count"] for a in result] == [12, 5, 3]


def test_top_actions_partial_keys():
    """존재 안 하는 key는 0으로 처리 — 누락 분류 enum도 안전."""
    counts = {"over_provisioned": 7}
    result = _top_actions(counts)
    assert result == [{"action_type": "downsize_cpu", "count": 7}]
