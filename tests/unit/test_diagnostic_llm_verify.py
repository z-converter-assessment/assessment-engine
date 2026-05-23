"""diagnostic.llm.verify — 수치 환각 검증 단위 테스트 (ADR 0003 3G절 + ADR 0025).

본질:
- collect_payload_numbers: payload (nested dict/list/scalar) 안 모든 숫자 토큰 수집
- find_hallucinated_numbers: narrative 안 숫자 - (payload + whitelist) = 환각 catalog
- 빈 set = 검증 통과
- non-empty = 환각 발견 (호출자 재생성 1회 + 재실패 시 mark_failed)
"""

from assessment_engine.diagnostic.llm.verify import collect_payload_numbers, find_hallucinated_numbers

# ─── collect_payload_numbers ──────────────────────────────────────────────


def test_collect_int_scalar():
    nums = collect_payload_numbers(42)
    assert "42" in nums


def test_collect_float_scalar_multiple_forms():
    """float = ".1f" + 본 값 모두 포함."""
    nums = collect_payload_numbers(85.3)
    assert "85.3" in nums


def test_collect_float_integer_value_includes_int_form():
    """float(14.0) = "14" 도 포함 (LLM 가 정수 형식으로 인용 가능)."""
    nums = collect_payload_numbers(14.0)
    assert "14" in nums or "14.0" in nums


def test_collect_string_extracts_numbers():
    """string 안 숫자 = 정규식 추출."""
    nums = collect_payload_numbers("14d")
    assert "14" in nums


def test_collect_dict_recursive():
    payload = {
        "server": {"hostname": "h", "id": 42},
        "use_method": {"cpu": {"p95": 85.3}},
    }
    nums = collect_payload_numbers(payload)
    assert "42" in nums
    assert "85.3" in nums


def test_collect_list_recursive():
    payload = [{"count": 12}, {"count": 8}]
    nums = collect_payload_numbers(payload)
    assert "12" in nums
    assert "8" in nums


def test_collect_bool_ignored():
    """bool 본 시점 numeric subclass 본질 catalog 본 시점 catalog — 본 시점 무시 (True/False 토큰 인용 안 됨)."""
    nums = collect_payload_numbers({"enabled": True, "count": 5})
    assert "5" in nums
    assert "1" not in nums  # True != 1 본질 catalog


# ─── find_hallucinated_numbers ────────────────────────────────────────────


def test_no_hallucination_when_all_numbers_from_payload():
    payload = {"cpu_p95": 85.3, "mem_p95": 60.5}
    narrative = "서버 CPU p95 85.3%, 메모리 p95 60.5% 사용."
    assert find_hallucinated_numbers(narrative, payload) == set()


def test_hallucination_detected_when_invented_number():
    payload = {"cpu_p95": 85.3}
    narrative = "서버 CPU p95 85.3%, 메모리 p95 99.9% 사용."
    assert "99.9" in find_hallucinated_numbers(narrative, payload)


def test_whitelist_zero_and_hundred_allowed():
    """trivial integers (0, 100) 본 시점 환각 아님."""
    payload = {"cpu_p95": 85.3}
    narrative = "CPU p95 85.3%, 100% 안 도달, 0건 발생."
    assert find_hallucinated_numbers(narrative, payload) == set()


def test_empty_narrative_yields_empty_set():
    assert find_hallucinated_numbers("", {"x": 1}) == set()


def test_payload_with_period_window_days_inline():
    """period_window {'days': 14} → "14" 본 시점 payload 안 포함."""
    payload = {"period_window": {"days": 14}, "cpu_p95": 25.3}
    narrative = "최근 14일 동안 CPU p95 25.3%."
    assert find_hallucinated_numbers(narrative, payload) == set()


def test_rag_context_content_numbers_pass():
    """payload['rag_context'] 안 content 본문 숫자도 collect_payload_numbers 가 추출."""
    payload = {
        "cpu_p95": 85.3,
        "rag_context": [{"content": "USE Method 안 CPU 30% 이하 = over-provisioned", "score": 0.9}],
    }
    narrative = "CPU p95 85.3%, USE Method 임계값 30% 초과."
    assert find_hallucinated_numbers(narrative, payload) == set()


def test_multiple_hallucinations_all_reported():
    payload = {"cpu_p95": 85.3}
    narrative = "CPU 85.3%, 어제 70.1%, 작년 평균 65.5%."
    hallucinated = find_hallucinated_numbers(narrative, payload)
    assert "70.1" in hallucinated
    assert "65.5" in hallucinated
