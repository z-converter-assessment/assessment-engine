"""Ollama LLM client — 로컬 무료 LLM (ADR 0004 + 0010).

HTTP POST /api/chat (ollama 표준 API) — system + user prompt -> Korean narrative.
default 모델 = llama3.1:8b. 한국어 정합 우위 모델 (qwen2.5:14b 등) 으로 운영자 교체 가능.

과금 발생 외부 API 호출 금지 정책 정합 — 본 client 는 로컬 ollama HTTP 단독 호출.
hallucination 방지 = 본 client = LLM 호출만 + 수치 검증은 handler 단 (ADR 0003 3G절).

system prompt 본질:
- right-sizing expert role
- payload 안 숫자만 활용 (외부 숫자 생성 금지) — handler 수치 검증과 정합
- classification + action 라벨 그대로 인용
- 2~4 문장 Korean narrative (markdown · code block 금지)

user prompt 본질:
- scope (server / environment) 별 statistics + classification + action
"""

import asyncio
import time
from typing import Any

import httpx
from loguru import logger

from assessment_engine.diagnostic.llm.base import BaseLlmClient

# 일시 장애 (network · ollama 일시 unavailable · 5xx) retry 정공 (F6).
# ValueError (shape mismatch · empty narrative) 본 시점 retry 안 함 — LLM 응답 본질 변경 catalog.
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_BASE_S = 0.5

_SYSTEM_PROMPT = """\
You are a server right-sizing expert. Given server diagnostic statistics,
classification, and recommended action, generate a concise Korean narrative
explaining the diagnosis and recommendation.

Strict rules:
- Use ONLY numbers from the provided payload. Do not invent, estimate, or round.
- Cite the classification label and recommended action verbatim.
- Reference domain knowledge if provided in "Relevant domain knowledge" section.
- Output 2-4 sentences, plain Korean text (no markdown, no code blocks, no lists).
- Do not include English explanation — Korean output only.
"""


class OllamaLlmClient(BaseLlmClient):
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.1:8b",
        timeout_seconds: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = httpx.Timeout(timeout_seconds)

    async def generate_narrative(self, scope: str, payload: dict) -> str:
        user_prompt = _build_user_prompt(scope, payload)

        url = f"{self._base_url}/api/chat"
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            # narrative deterministic 우위 — temperature 낮춰 환각 감소.
            "options": {"temperature": 0.2},
        }

        data = await _ollama_post_with_retry(url, body, self._timeout, label="chat")

        narrative = data.get("message", {}).get("content", "").strip()
        if not narrative:
            logger.error("ollama empty narrative scope={} model={}", scope, self._model)
            raise ValueError("ollama empty narrative response")
        # F7 observability — narrative 길이 + token 수 (eval_count) 기록. 운영자 진단 성능 모니터링.
        logger.info(
            "ollama narrative model={} scope={} chars={} eval_count={} eval_duration_ms={}",
            self._model,
            scope,
            len(narrative),
            data.get("eval_count"),
            (data.get("eval_duration") or 0) // 1_000_000,  # ns -> ms
        )
        return narrative


async def _ollama_post_with_retry(
    url: str,
    body: dict,
    timeout: httpx.Timeout,
    label: str,
) -> dict[str, Any]:
    """ollama POST + retry 2회 + exponential backoff (F6). 일시 장애 (HTTPError) 만 retry.

    ValueError 본질 catch 안 함 — 호출자가 응답 본질 검증 책임 (LLM 본문 변경 시점에 retry 무의미).
    """
    # last_error 의도 = retry 모두 실패 시 마지막 예외 reraise. for loop 안 break 없이 종료 = 모든
    # attempt catch — last_error 항상 설정. dummy default 안 type 안 None 본 시점 정공 (F1 assert 회피).
    last_error: Exception = RuntimeError("retry exhausted with no error captured")
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=body)
                response.raise_for_status()
                body_json = response.json()
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.info("ollama http label={} attempt={} elapsed_ms={}", label, attempt, elapsed_ms)
            return body_json
        except httpx.HTTPError as e:
            last_error = e
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.warning(
                "ollama http retry label={} attempt={} elapsed_ms={} err_type={}",
                label,
                attempt,
                elapsed_ms,
                type(e).__name__,
            )
            if attempt < _RETRY_ATTEMPTS:
                await asyncio.sleep(_RETRY_BACKOFF_BASE_S * attempt)
    raise last_error


def _build_user_prompt(scope: str, payload: dict[str, Any]) -> str:
    if scope == "server":
        return _build_server_prompt(payload)
    return _build_environment_prompt(payload)


def _build_server_prompt(payload: dict[str, Any]) -> str:
    server = payload.get("server", {})
    use_method = payload.get("use_method", {})
    classification = payload.get("classification", "unknown")
    recommendation = payload.get("recommendation", {})
    period = payload.get("period_window", {})

    hostname = server.get("hostname", "unknown")
    cpu_p95 = _fmt_num(use_method.get("cpu", {}).get("p95"))
    mem_p95 = _fmt_num(use_method.get("memory", {}).get("p95"))
    iowait_p95 = _fmt_num(use_method.get("iowait", {}).get("p95"))
    action = recommendation.get("action") or "no_action"
    window_label = _fmt_window(period)

    lines = [
        "Server diagnostic context:",
        f"- Hostname: {hostname}",
        f"- Time window: {window_label}",
        f"- CPU p95: {cpu_p95}%",
        f"- Memory p95: {mem_p95}%",
        f"- iowait p95: {iowait_p95}%",
        f"- Classification: {classification}",
        f"- Recommended action: {action}",
    ]
    lines.append("")
    lines.append("Generate a Korean narrative (2-4 sentences).")
    return "\n".join(lines)


def _build_environment_prompt(payload: dict[str, Any]) -> str:
    coverage = payload.get("coverage", {})
    classification = payload.get("classification", {})
    period = payload.get("period_window", {})

    total = coverage.get("total_servers", 0)
    evaluated = coverage.get("evaluated_servers", 0)
    over_n = _safe_count(classification, "over_provisioned")
    under_n = _safe_count(classification, "under_provisioned")
    idle_n = _safe_count(classification, "idle")
    optimal_n = _safe_count(classification, "optimal")
    window_label = _fmt_window(period)

    lines = [
        "Environment diagnostic context:",
        f"- Total servers: {total}",
        f"- Evaluated servers: {evaluated}",
        f"- Time window: {window_label}",
        f"- Over-provisioned: {over_n}",
        f"- Under-provisioned: {under_n}",
        f"- Idle: {idle_n}",
        f"- Optimal: {optimal_n}",
    ]
    lines.append("")
    lines.append("Generate a Korean narrative summarizing fleet right-sizing strategy (2-4 sentences).")
    return "\n".join(lines)


def _safe_count(classification: dict[str, Any], key: str) -> int:
    bucket = classification.get(key, {})
    if isinstance(bucket, dict):
        return int(bucket.get("count", 0))
    return 0


def _fmt_num(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _fmt_window(period: dict[str, Any]) -> str:
    days = period.get("days")
    if days is None:
        return "14d"
    if isinstance(days, (int, float)) and days >= 1:
        return f"{int(days)}d"
    return f"{round(float(days) * 24)}h"
