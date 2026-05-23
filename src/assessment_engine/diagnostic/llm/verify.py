"""LLM narrative 수치 환각 검증 (ADR 0003 3G절 + ADR 0025).

핵심 규약:
- narrative 안 모든 숫자 토큰은 payload 안 존재 의무 (외부 숫자 생성 금지)
- 호출자 = handler — 실패 시 재생성 1회 + 재실패 시 mark_failed('llm_hallucination')

payload = dict (nested dict + list + scalar 가능). recursive walk 으로 모든 numeric value + string 안 숫자 토큰 수집.
"""

import re
from typing import Any

# collect 안 = 관대 — payload 안 모든 숫자 catalog 수집 (dict key 안 숫자 + string 안 숫자 포함).
_COLLECT_PATTERN = re.compile(r"\d+(?:\.\d+)?")

# narrative 검증 안 = 엄격 — alphanumeric 인접 시 제외 (예: "p95" 안 "95" / "5" 추출 catalog 차단).
# 라벨 안 숫자 (label name) 본 시점 본질 환각 catalog 본 시점 catalog — 본 시점 본 catalog 본 시점 정공.
_NARRATIVE_PATTERN = re.compile(r"(?<![a-zA-Z0-9])\d+(?:\.\d+)?(?![a-zA-Z0-9])")

# whitelist — payload 안 미존재여도 환각 아님. trivial integer (0, 100).
# 본 set 작게 유지 — 본질적 환각 catalog 안 누락 차단.
_WHITELIST = {"0", "100"}


def collect_payload_numbers(value: Any) -> set[str]:
    """payload (dict / list / nested) 안 모든 숫자 토큰 set 반환.

    int / float = str(value) 변환. float = ".1f" + 정수부 추가 형식도 포함 (LLM 가 어느 형식이든 활용 가능).
    string = 정규식 추출 (예: "14d" -> "14", "85.3%" -> "85.3").
    dict key 안 숫자도 추출 (예: "cpu_p95" -> "95") — LLM 가 라벨 안 숫자 인용 catalog 본 시점 정공.
    """
    out: set[str] = set()
    _walk(value, out)
    return out


def _walk(value: Any, out: set[str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _walk(k, out)
            _walk(v, out)
    elif isinstance(value, list):
        for item in value:
            _walk(item, out)
    elif isinstance(value, bool):
        # bool = numeric subclass 본 시점 본질 catalog 본 시점 catalog — 본 시점 무시
        return
    elif isinstance(value, int):
        out.add(str(value))
    elif isinstance(value, float):
        out.add(str(value))
        out.add(f"{value:.1f}")
        out.add(f"{value:.2f}")
        if value.is_integer():
            out.add(str(int(value)))
    elif isinstance(value, str):
        for m in _COLLECT_PATTERN.findall(value):
            out.add(m)


def find_hallucinated_numbers(narrative: str, payload: Any) -> set[str]:
    """narrative 안 숫자 중 payload + whitelist 안 미존재 catalog 반환.

    빈 set = 검증 통과. non-empty = 환각 발견 (호출자 재생성 1회 + 재실패 시 mark_failed).
    narrative 정규식 = alphanumeric 인접 catalog 제외 — "p95" 안 부분 추출 catalog 차단
    (라벨 자체 catalog 본 시점 환각 catalog 본 시점 정공).
    """
    payload_nums = collect_payload_numbers(payload)
    narrative_nums = set(_NARRATIVE_PATTERN.findall(narrative))
    return narrative_nums - payload_nums - _WHITELIST
