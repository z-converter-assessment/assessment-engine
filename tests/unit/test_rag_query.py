"""rag.query — scope 별 query 합성 단위 테스트 (ADR 0024 결정 7).

본질 catalog:
- server scope: hostname + CPU/Mem/iowait p95 + classification + action 포함
- environment scope: total/evaluated + 분류 분포 count 포함
- recommendation 미정 시 action 제외 fallback
- 결측 값 (None) 'n/a' 표기 정합
- 영어 통일 (도메인 지식 자료 정합)
"""

from assessment_engine.rag.query import build_query


def test_server_query_includes_all_signals():
    payload = {
        "server": {"hostname": "web-01"},
        "use_method": {
            "cpu": {"p95": 85.0},
            "memory": {"p95": 60.5},
            "iowait": {"p95": 5.2},
        },
        "classification": "cpu_high",
        "recommendation": {"action": "downsize_cpu"},
    }
    q = build_query("server", payload)
    assert "Server diagnostic context" in q
    assert "web-01" in q
    assert "85.0%" in q
    assert "60.5%" in q
    assert "5.2%" in q
    assert "cpu_high" in q
    assert "downsize_cpu" in q
    assert "Related domain knowledge" in q


def test_server_query_no_recommendation_omits_action_line():
    payload = {
        "server": {"hostname": "h"},
        "use_method": {"cpu": {"p95": 10}, "memory": {"p95": 20}, "iowait": {"p95": 1}},
        "classification": "insufficient_data",
        "recommendation": {},
    }
    q = build_query("server", payload)
    assert "Recommended action" not in q
    assert "insufficient_data" in q


def test_server_query_missing_metrics_render_n_a():
    payload = {
        "server": {"hostname": "h"},
        "use_method": {"cpu": {"p95": None}, "memory": {"p95": None}, "iowait": {"p95": None}},
        "classification": "unknown",
        "recommendation": {},
    }
    q = build_query("server", payload)
    assert "n/a" in q


def test_environment_query_includes_distribution_counts():
    payload = {
        "coverage": {"total_servers": 50, "evaluated_servers": 48},
        "classification": {
            "over_provisioned": {"count": 12},
            "under_provisioned": {"count": 3},
            "idle": {"count": 5},
            "optimal": {"count": 28},
        },
    }
    q = build_query("environment", payload)
    assert "Environment diagnostic context" in q
    assert "Total servers: 50" in q
    assert "Evaluated servers: 48" in q
    assert "Over-provisioned: 12" in q
    assert "Under-provisioned: 3" in q
    assert "Idle: 5" in q
    assert "Optimal: 28" in q


def test_environment_query_empty_classification_defaults_zero():
    payload = {
        "coverage": {"total_servers": 0, "evaluated_servers": 0},
        "classification": {},
    }
    q = build_query("environment", payload)
    assert "Total servers: 0" in q
    assert "Over-provisioned: 0" in q
    assert "Optimal: 0" in q


def test_environment_query_classification_non_dict_bucket_yields_zero():
    """safe_count — bucket 가 dict 아닐 때 0 fallback."""
    payload = {
        "coverage": {"total_servers": 10, "evaluated_servers": 10},
        "classification": {"over_provisioned": "weird-non-dict"},
    }
    q = build_query("environment", payload)
    assert "Over-provisioned: 0" in q


def test_dispatch_server_vs_environment():
    """build_query dispatch — scope=='server' 만 server query 합성, 다른 모든 값은 environment."""
    server_payload = {
        "server": {"hostname": "h"},
        "use_method": {"cpu": {"p95": 1}, "memory": {"p95": 1}, "iowait": {"p95": 1}},
        "classification": "optimal",
        "recommendation": {},
    }
    env_payload = {
        "coverage": {"total_servers": 1, "evaluated_servers": 1},
        "classification": {"optimal": {"count": 1}},
    }
    assert "Server diagnostic context" in build_query("server", server_payload)
    assert "Environment diagnostic context" in build_query("environment", env_payload)
    # scope == 'unknown' (잘못된 입력) -> environment 합성으로 fallback
    assert "Environment diagnostic context" in build_query("anything-else", env_payload)
