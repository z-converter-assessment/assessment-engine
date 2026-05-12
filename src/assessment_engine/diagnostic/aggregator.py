"""진단 통계 추출 — query_repo의 raw 메서드 호출 + 룰 분류 매핑.

ADR 0004 result 카탈로그 구축: server scope = 1대 USE Method + classification + recommendation,
environment scope = N대 분포 카운트 + USE Method 분포 + top_actions + saturation_alerts.
ADR 0003 D단계 Pricing Adapter 미도입 — cost 필드는 None 자리 잡음 (forward compatibility).
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from assessment_engine.db.repositories.base_query_repository import BaseQueryRepository
from assessment_engine.web.services.recommendation import ResourceStats, classify


async def extract_server(
    query_repo: BaseQueryRepository,
    server_public_id: str,
    period_days: float,
    end: datetime | None = None,
    time_range: str = "14d",
) -> dict[str, Any]:
    if end is None:
        end = datetime.now(timezone.utc)

    sid = await query_repo.resolve_server_id(server_public_id)
    if sid is None:
        raise ValueError(f"server not found: {server_public_id}")

    rows = await query_repo.report_aggregate([sid], period_days, end)
    if not rows:
        raise ValueError(f"no metrics for server: {server_public_id} (period={period_days}d)")
    row = rows[0]

    stats = ResourceStats(
        cpu_p95_pct=row.cpu_p95_pct,
        cpu_peak_pct=row.cpu_peak_pct,
        mem_p95_pct=row.mem_p95_pct,
        swap_used=row.swap_used,
        net_avg_kbps=None,  # 1차 — net 집계는 별도 query 도입 후
    )
    classification_label = classify(stats)

    return {
        "scope": "server",
        "period_window": _period_window(end, period_days, time_range),
        "evaluated_at": end.isoformat(),
        "server": {
            "public_id": row.public_id,
            "hostname": row.hostname,
            "os_id": row.os_id,
            "os_version": row.os_version,
            "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        },
        "use_method": {
            "cpu":    {"p95": row.cpu_p95_pct, "peak": row.cpu_peak_pct},
            "memory": {"p95": row.mem_p95_pct, "peak": row.mem_peak_pct},
            "load_15m_max": row.load_15m_max,
            "swap_used": row.swap_used,
            "iowait_p95":  row.iowait_p95_pct,
            "iowait_peak": row.iowait_peak_pct,
        },
        "classification": classification_label,
        "recommendation": _server_recommendation(classification_label),
    }


async def extract_environment(
    query_repo: BaseQueryRepository,
    period_days: float,
    end: datetime | None = None,
    time_range: str = "14d",
) -> dict[str, Any]:
    if end is None:
        end = datetime.now(timezone.utc)
    sids = await query_repo.list_server_ids()
    rows = await query_repo.report_aggregate(sids, period_days, end) if sids else []

    counts: dict[str, int] = {
        "idle": 0, "shutdown": 0, "over_provisioned": 0,
        "under_provisioned": 0, "optimal": 0, "insufficient_data": 0,
    }
    swap_pressure = 0
    cpu_p95s: list[float] = []
    cpu_peaks: list[float] = []
    mem_p95s: list[float] = []
    mem_peaks: list[float] = []
    saturation_alerts: list[dict] = []

    for row in rows:
        if row.swap_used:
            swap_pressure += 1

        stats = ResourceStats(
            cpu_p95_pct=row.cpu_p95_pct,
            cpu_peak_pct=row.cpu_peak_pct,
            mem_p95_pct=row.mem_p95_pct,
            swap_used=row.swap_used,
            net_avg_kbps=None,
        )
        label = classify(stats)
        counts[label] = counts.get(label, 0) + 1

        if row.cpu_p95_pct is not None:
            cpu_p95s.append(row.cpu_p95_pct)
        if row.cpu_peak_pct is not None:
            cpu_peaks.append(row.cpu_peak_pct)
        if row.mem_p95_pct is not None:
            mem_p95s.append(row.mem_p95_pct)
        if row.mem_peak_pct is not None:
            mem_peaks.append(row.mem_peak_pct)

        if row.mem_peak_pct is not None and row.mem_peak_pct >= _SATURATION_MEM_THRESHOLD:
            saturation_alerts.append({
                "server_id": row.public_id,
                "resource":  "memory",
                "value":     row.mem_peak_pct,
                "threshold": _SATURATION_MEM_THRESHOLD,
            })

    total = len(sids)
    evaluated = len(rows)

    return {
        "scope": "environment",
        "period_window": _period_window(end, period_days, time_range),
        "evaluated_at": end.isoformat(),
        "coverage": {
            "total_servers":     total,
            "evaluated_servers": evaluated,
            "coverage_pct":      round(evaluated / total * 100, 1) if total else 0.0,
        },
        "classification": {
            "idle":              {"count": counts["idle"]},
            "swap_pressure":     {"count": swap_pressure},
            "over_provisioned":  {"count": counts["over_provisioned"]},
            "under_provisioned": {"count": counts["under_provisioned"]},
            "optimal":           {"count": counts["optimal"]},
        },
        "use_method": {
            "cpu":    _summary(cpu_p95s, cpu_peaks),
            "memory": _summary(mem_p95s, mem_peaks),
        },
        "top_actions": _top_actions(counts),
        "saturation_alerts": saturation_alerts[:_SATURATION_TOP_N],
        "cost": None,  # Phase D Pricing Adapter 도입 시 채움
    }


_SATURATION_MEM_THRESHOLD = 90.0
_SATURATION_TOP_N = 20


def _period_window(end: datetime, period_days: float, time_range: str) -> dict:
    start = end - timedelta(days=period_days)
    return {
        "time_range": time_range,   # 표시용 라벨 키 (DIAGNOSTIC_RANGE_LABEL_KR)
        "days":       period_days,  # raw float — 클라이언트는 time_range 우선
        "start":      start.isoformat(),
        "end":        end.isoformat(),
    }


def _server_recommendation(classification: str) -> dict:
    """ADR 0003 3C절 분류 → 단순 액션 매핑. Pricing 미연동이라 from/to 사이즈·비용 미포함."""
    if classification == "over_provisioned":
        return {"action": "downsize_cpu"}
    if classification == "under_provisioned":
        return {"action": "upsize_memory"}
    if classification == "idle":
        return {"action": "shutdown_idle"}
    if classification == "shutdown":
        return {"action": "shutdown_review"}
    return {"action": "no_action"}


def _summary(p95s: list[float], peaks: list[float]) -> dict:
    return {
        "avg_p95":    round(sum(p95s) / len(p95s), 1) if p95s else None,
        "median_p95": round(sorted(p95s)[len(p95s) // 2], 1) if p95s else None,
        "peak_max":   round(max(peaks), 1) if peaks else None,
    }


def _top_actions(counts: dict[str, int]) -> list[dict]:
    actions = [
        ("downsize_cpu",    counts.get("over_provisioned", 0)),
        ("upsize_memory",   counts.get("under_provisioned", 0)),
        ("shutdown_idle",   counts.get("idle", 0)),
        ("shutdown_review", counts.get("shutdown", 0)),
    ]
    return [
        {"action_type": a, "count": c}
        for a, c in sorted(actions, key=lambda x: x[1], reverse=True)
        if c > 0
    ]
