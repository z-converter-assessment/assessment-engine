"""RAG query 합성 — scope 별 함수 (ADR 0024 결정 7).

handler retrieve_context 단계 안 호출. payload (use_method 통계 + classification + recommendation) ->
영어 query 텍스트 합성. RAG 검색 query = '본 진단 결과 narrative 합성에 필요한 도메인 지식' 의도.

scope 별 분리:
- server scope: 단일 서버 통계 + 분류 + 권장 액션
- environment scope: 환경 전체 분포 + KPI

recommendation 미정 시 (insufficient_data 등) -> 통계 + 분류 만 (action 부재 fallback).
"""

from typing import Any


def build_query(scope: str, payload: dict[str, Any]) -> str:
    """scope -> query 합성 함수 dispatch."""
    if scope == "server":
        return _build_server_query(payload)
    return _build_environment_query(payload)


def _build_server_query(payload: dict[str, Any]) -> str:
    server = payload.get("server", {})
    use_method = payload.get("use_method", {})
    classification = payload.get("classification", "unknown")
    recommendation = payload.get("recommendation", {})

    hostname = server.get("hostname", "unknown")
    cpu_p95 = _fmt(use_method.get("cpu", {}).get("p95"))
    mem_p95 = _fmt(use_method.get("memory", {}).get("p95"))
    iowait_p95 = _fmt(use_method.get("iowait", {}).get("p95"))
    action = recommendation.get("action")

    lines = [
        "Server diagnostic context:",
        f" - Hostname: {hostname}",
        f" - CPU p95: {cpu_p95}",
        f" - Memory p95: {mem_p95}",
        f" - iowait p95: {iowait_p95}",
        f" - Classification: {classification}",
    ]
    if action:
        lines.append(f" - Recommended action: {action}")
    lines.append("Related domain knowledge for right-sizing decision.")
    return "\n".join(lines)


def _build_environment_query(payload: dict[str, Any]) -> str:
    coverage = payload.get("coverage", {})
    classification = payload.get("classification", {})

    total = coverage.get("total_servers", 0)
    evaluated = coverage.get("evaluated_servers", 0)
    over_n = _safe_count(classification, "over_provisioned")
    under_n = _safe_count(classification, "under_provisioned")
    idle_n = _safe_count(classification, "idle")
    optimal_n = _safe_count(classification, "optimal")

    return "\n".join(
        [
            "Environment diagnostic context:",
            f" - Total servers: {total}",
            f" - Evaluated servers: {evaluated}",
            f" - Over-provisioned: {over_n}",
            f" - Under-provisioned: {under_n}",
            f" - Idle: {idle_n}",
            f" - Optimal: {optimal_n}",
            "Related domain knowledge for fleet right-sizing strategy.",
        ]
    )


def _safe_count(classification: dict[str, Any], key: str) -> int:
    bucket = classification.get(key, {})
    if isinstance(bucket, dict):
        return int(bucket.get("count", 0))
    return 0


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.1f}%"
    return str(value)
