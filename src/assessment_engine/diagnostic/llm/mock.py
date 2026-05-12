"""Mock LLM client — deterministic 텍스트 합성.

ADR 0004 LLM 토글의 기본값. 외부 API 호출 0, 비용 0.
payload 안 통계 수치만 인용해 합성 → ADR 0003 3G절 수치 검증 자동 통과.
asyncio.sleep으로 LLM latency 시뮬레이션 — UI progress_stage 단계 표시 확인용.
"""
import asyncio

from assessment_engine.config import diagnostic_settings
from assessment_engine.db.repositories.base_diagnostic_repository import DIAGNOSTIC_RANGE_LABEL_KR
from assessment_engine.diagnostic.llm.base import BaseLlmClient


class MockLlmClient(BaseLlmClient):
    async def generate_narrative(self, scope: str, payload: dict) -> str:
        await asyncio.sleep(diagnostic_settings.llm_mock_latency_seconds)
        if scope == "server":
            return _server_narrative(payload)
        return _environment_narrative(payload)


def _server_narrative(payload: dict) -> str:
    server = payload.get("server", {})
    use_method = payload.get("use_method", {})
    classification = payload.get("classification", "insufficient_data")
    recommendation = payload.get("recommendation", {})
    period = payload.get("period_window", {})

    hostname = server.get("hostname", "unknown")
    window_label = _fmt_window(period)
    cpu_p95 = _fmt_num(use_method.get("cpu", {}).get("p95"))
    mem_p95 = _fmt_num(use_method.get("memory", {}).get("p95"))

    action = recommendation.get("action") or "no_action"
    classification_kr = _CLASSIFICATION_LABEL_KR.get(classification, classification)

    base = (
        f"서버 {hostname}는 최근 {window_label} 동안 CPU p95 {cpu_p95}%, "
        f"메모리 p95 {mem_p95}% 사용. 분류는 {classification_kr}."
    )
    if action == "downsize_cpu":
        return base + " AWS Compute Optimizer 임계값(CPU p95 30%) 기준으로 CPU 다운사이즈 권장."
    if action == "upsize_memory":
        return base + " 메모리 압박이 관찰되어 메모리 업사이즈 권장."
    if action == "shutdown_idle":
        return base + " Azure Advisor 임계값(CPU p95 3%) 기준으로 미사용 — 종료 검토 권장."
    if action == "shutdown_review":
        return base + " 사용량이 매우 낮아 종료 검토 권장."
    return base + " 현재 사용 패턴은 적정 범위."


def _environment_narrative(payload: dict) -> str:
    coverage = payload.get("coverage", {})
    classification = payload.get("classification", {})
    period = payload.get("period_window", {})

    window_label = _fmt_window(period)
    total = coverage.get("total_servers", 0)
    evaluated = coverage.get("evaluated_servers", 0)
    over_n = classification.get("over_provisioned", {}).get("count", 0)
    idle_n = classification.get("idle", {}).get("count", 0)
    under_n = classification.get("under_provisioned", {}).get("count", 0)
    optimal_n = classification.get("optimal", {}).get("count", 0)

    return (
        f"최근 {window_label} 환경 진단 — 평가 대상 {evaluated}대 (전체 {total}대). "
        f"분류 분포: over-provisioned {over_n}대, under-provisioned {under_n}대, "
        f"idle {idle_n}대, optimal {optimal_n}대. "
        f"우선 검토 권장: over-provisioned {over_n}대의 다운사이즈."
    )


def _fmt_window(period: dict) -> str:
    """period_window dict → 한국어 라벨. time_range 우선, fallback은 days 정수형."""
    tr = period.get("time_range")
    if tr and tr in DIAGNOSTIC_RANGE_LABEL_KR:
        return DIAGNOSTIC_RANGE_LABEL_KR[tr]
    days = period.get("days")
    if days is None:
        return "14일"
    if days >= 1:
        return f"{int(days)}일"
    return f"{round(days * 24)}시간"


def _fmt_num(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


_CLASSIFICATION_LABEL_KR = {
    "idle":              "idle",
    "shutdown":          "shutdown 검토",
    "over_provisioned":  "over-provisioned",
    "under_provisioned": "under-provisioned",
    "optimal":           "optimal",
    "insufficient_data": "데이터 부족",
}
