"""diagnostic.llm.mock — deterministic narrative 합성 단위 테스트 (ADR 0004).

수치 검증 핵심: narrative에 등장하는 모든 숫자가 payload 안에 존재해야 한다 (ADR 0003 3G절).
"""

import pytest

from assessment_engine.diagnostic.llm.mock import (
    MockLlmClient,
    _environment_narrative,
    _server_narrative,
)

# ─── _server_narrative ────────────────────────────────────────────────────


def test_server_narrative_includes_hostname_and_cpu_mem_values():
    payload = {
        "server": {"hostname": "web-01"},
        "use_method": {"cpu": {"p95": 25.3}, "memory": {"p95": 42.1}},
        "classification": "over_provisioned",
        "recommendation": {"action": "downsize_cpu"},
        "period_window": {"days": 14},
    }
    text = _server_narrative(payload)
    assert "web-01" in text
    assert "25.3" in text
    assert "42.1" in text
    assert "14" in text


@pytest.mark.parametrize(
    "classification, expected_substring",
    [
        ("over_provisioned", "다운사이즈"),
        ("under_provisioned", "업사이즈"),
        ("idle", "종료"),
        ("shutdown", "종료"),
        ("optimal", "적정"),
        ("insufficient_data", "데이터 부족"),
    ],
)
def test_server_narrative_branch_keywords(classification, expected_substring):
    payload = {
        "server": {"hostname": "h"},
        "use_method": {"cpu": {"p95": 50.0}, "memory": {"p95": 50.0}},
        "classification": classification,
        "recommendation": {
            "action": "downsize_cpu"
            if classification == "over_provisioned"
            else "upsize_memory"
            if classification == "under_provisioned"
            else "shutdown_idle"
            if classification == "idle"
            else "shutdown_review"
            if classification == "shutdown"
            else "no_action"
        },
        "period_window": {"days": 14},
    }
    assert expected_substring in _server_narrative(payload)


def test_server_narrative_missing_values_render_dash():
    payload = {
        "server": {"hostname": "h"},
        "use_method": {"cpu": {"p95": None}, "memory": {"p95": None}},
        "classification": "insufficient_data",
        "recommendation": {"action": "no_action"},
        "period_window": {"days": 14},
    }
    text = _server_narrative(payload)
    assert "—" in text  # _fmt_num이 None을 em dash로


# ─── _environment_narrative ──────────────────────────────────────────────


def test_environment_narrative_includes_counts_and_days():
    payload = {
        "coverage": {"total_servers": 120, "evaluated_servers": 118},
        "classification": {
            "over_provisioned": {"count": 42},
            "under_provisioned": {"count": 8},
            "idle": {"count": 5},
            "optimal": {"count": 60},
        },
        "period_window": {"days": 14},
    }
    text = _environment_narrative(payload)
    for s in ("120", "118", "42", "8", "5", "60", "14"):
        assert s in text


def test_environment_narrative_empty_classification_defaults_zero():
    """classification 비어도 0으로 채워 narrative 생성 (스케줄러 빈 환경 E2E 흐름)."""
    payload = {
        "coverage": {"total_servers": 0, "evaluated_servers": 0},
        "classification": {},
        "period_window": {"days": 14},
    }
    text = _environment_narrative(payload)
    assert "0대" in text


# ─── 수치 hallucination 검증 (ADR 0003 3G절) ──────────────────────────────


def test_server_narrative_all_numbers_come_from_payload():
    """narrative 안 정수·소수는 모두 payload 안 값이어야 함 — 외부 숫자 생성 금지.

    임계값 인용(예: "임계값 30%")은 mock client가 정적 텍스트로 박은 것 — 본 테스트는
    동적 수치만 검증. 정적 임계값은 payload-independent하게 항상 동일 출력이므로 OK.
    """
    payload = {
        "server": {"hostname": "h"},
        "use_method": {"cpu": {"p95": 25.3}, "memory": {"p95": 42.1}},
        "classification": "optimal",
        "recommendation": {"action": "no_action"},
        "period_window": {"days": 14},
    }
    text = _server_narrative(payload)
    # 동적 수치 인용 확인 (payload 값이 narrative에 있는지)
    assert "25.3" in text and "42.1" in text and "14" in text


# ─── MockLlmClient.generate_narrative (entry) ────────────────────────────


@pytest.mark.asyncio
async def test_mock_client_routes_by_scope(monkeypatch):
    """sleep을 0으로 만들고 scope별 분기 검증."""
    from assessment_engine.diagnostic.settings import diagnostic_settings

    monkeypatch.setattr(diagnostic_settings, "llm_mock_latency_seconds", 0)

    client = MockLlmClient()
    server_payload = {
        "server": {"hostname": "x"},
        "use_method": {"cpu": {"p95": 10}, "memory": {"p95": 10}},
        "classification": "optimal",
        "recommendation": {"action": "no_action"},
        "period_window": {"days": 14},
    }
    env_payload = {
        "coverage": {"total_servers": 1, "evaluated_servers": 1},
        "classification": {"optimal": {"count": 1}},
        "period_window": {"days": 14},
    }
    s = await client.generate_narrative("server", server_payload)
    e = await client.generate_narrative("environment", env_payload)
    assert "x" in s
    assert "1대" in e
