"""Right-sizing API 매퍼 — 자동화/외부 소비용 per-server 프로비저닝 판정 dict.

화면 표시(ViewModel)와 별개 채널이지만 분류·근거·신뢰도·권고는 전부 도메인 단일 진실(`rollup_host`·
recommendation 처방) 재사용 — 보고서·자원평가 화면과 값이 어긋나지 않게 여기서 재계산하지 않는다.
포화 신호는 표시 문자열이 아니라 stats 원자료·임계 상수로 numeric(파싱 계약).
"""

from typing import TYPE_CHECKING

from assessment_engine.domain import right_sizing
from assessment_engine.web.services.device_filters import disk_total_bytes
from assessment_engine.web.services.mappers.assessment_display import (
    build_host_confidence_notes,
    resource_confidence_notes,
    saturation_block,
)
from assessment_engine.web.services.mappers.constants import _CAUSE_LABEL_BY_TRIGGER
from assessment_engine.web.services.mappers.host_display import primary_ip
from assessment_engine.web.services.mappers.resource_stats import build_resource_stats
from assessment_engine.web.services.unit_converter import bytes_to_gb

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import ReportRowRaw
    from assessment_engine.json_types import JsonObject
    from assessment_engine.web.view_models.right_sizing_api import RsAction, RsNetSignal, RsRecommendation

# ResourceStatus(도메인 enum) -> 외부 노출 한국어 라벨. 자원 적정성 화면과 통일 어휘.
_STATUS_LABEL_KO: dict[str, str] = {
    "under": "부족",
    "optimal": "적정",
    "over": "여유",
    "insufficient": "표본 부족",
    "filling": "소진 임박",
    "capacity_ok": "적정",
    "io_bound": "병목",
    "io_ok": "적정",
    "congested": "혼잡",
    "quality_ok": "정상",
    "unmeasured": "미관측",
}


def _evidence_labels(triggers: list[right_sizing.TriggerKind]) -> list[str]:
    return [_CAUSE_LABEL_BY_TRIGGER.get(t) or right_sizing.TRIGGER_LABEL_KO.get(t, t) for t in triggers]


def _sizeable_recommendation(kind: right_sizing.ResourceKind, ra: right_sizing.ResourceAssessment) -> str | None:
    if ra.status in ("under", "over", "io_bound", "filling") or ra.sizing_target is not None:
        text = right_sizing.resource_prescription(kind, ra)
        return text or None
    return None


def _net_signal(value: float | None, threshold: float, *, inclusive: bool = False) -> RsNetSignal:
    if value is None:
        return {"value": None, "threshold": threshold, "exceeded": None, "measured": False}
    exceeded = value >= threshold if inclusive else value > threshold
    return {"value": round(value, 3), "threshold": threshold, "exceeded": exceeded, "measured": True}


def _cpu_resource(
    raw: ReportRowRaw, stats: right_sizing.ResourceStats, host: right_sizing.HostAssessment
) -> JsonObject:
    ra = host.resources["cpu"]
    return {
        "status": ra.status,
        "status_label": _STATUS_LABEL_KO.get(ra.status, ra.status),
        "utilization_p95_pct": round(raw.cpu_p95_pct, 1) if raw.cpu_p95_pct is not None else None,
        "saturation": saturation_block("cpu", stats),
        "evidence": _evidence_labels(ra.triggers),
        "confidence_notes": resource_confidence_notes(ra.confidence),
        "current_cores": stats.cpu_cores,
        "sizing_target_cores": ra.sizing_target,
        "recommendation": _sizeable_recommendation("cpu", ra),
        "detail": ra.detail or None,
    }


def _memory_resource(
    raw: ReportRowRaw, stats: right_sizing.ResourceStats, host: right_sizing.HostAssessment
) -> JsonObject:
    ra = host.resources["memory"]
    return {
        "status": ra.status,
        "status_label": _STATUS_LABEL_KO.get(ra.status, ra.status),
        "utilization_p95_pct": round(raw.mem_p95_pct, 1) if raw.mem_p95_pct is not None else None,
        "saturation": saturation_block("memory", stats),
        "evidence": _evidence_labels(ra.triggers),
        "confidence_notes": resource_confidence_notes(ra.confidence),
        "current_mb": stats.mem_total_mb,
        "sizing_target_mb": ra.sizing_target,
        "recommendation": _sizeable_recommendation("memory", ra),
        "detail": ra.detail or None,
    }


def _disk_resource(
    raw: ReportRowRaw, stats: right_sizing.ResourceStats, host: right_sizing.HostAssessment
) -> JsonObject:
    cap = host.resources["disk_capacity"]
    io = host.resources["disk_io"]
    _dbytes = disk_total_bytes(raw.block_devices or [])
    _dgb = bytes_to_gb(_dbytes) if _dbytes else None
    return {
        "capacity": {
            "status": cap.status,
            "status_label": _STATUS_LABEL_KO.get(cap.status, cap.status),
            "worst_mount": raw.disk_capacity_driving_mount,
            "worst_mount_used_pct": (
                round(raw.disk_capacity_driving_used_pct, 1) if raw.disk_capacity_driving_used_pct is not None else None
            ),
            "days_until_full": (
                int(raw.disk_capacity_runway_days) if raw.disk_capacity_runway_days is not None else None
            ),
            "evidence": _evidence_labels(cap.triggers),
            "confidence_notes": resource_confidence_notes(cap.confidence),
            "current_gb": int(_dgb) if _dgb is not None else None,
            "sizing_target_gb": cap.sizing_target,
            "recommendation": _sizeable_recommendation("disk_capacity", cap),
            "detail": cap.detail or None,
        },
        "io": {
            "status": io.status,
            "status_label": _STATUS_LABEL_KO.get(io.status, io.status),
            "saturation": saturation_block("disk_io", stats),
            "evidence": _evidence_labels(io.triggers),
            "confidence_notes": resource_confidence_notes(io.confidence),
            "detail": io.detail or None,
        },
    }


def _action(kind: right_sizing.ResourceKind, ra: right_sizing.ResourceAssessment, op: str) -> RsAction:
    """조치 1건. 목표 수치는 자원 종류별 키(target_cores/_mb/_gb)로 직접 파싱 가능.

    사이징 축이 아닌 자원(network·disk_io)과 목표 미상은 그 키 자체가 없다 — 계약이 명시한 생략이다.
    자원별로 분기해 쓰는 이유는 변수 키 대입(`a[key] = v`)을 타입 검사기가 검증하지 못하기 때문이다.
    """
    action: RsAction = {
        "resource": kind,
        "op": op,
        "target_display": right_sizing.resource_prescription(kind, ra) or None,
    }
    target = ra.sizing_target
    if target is None:
        return action
    if kind == "cpu":
        return {**action, "target_cores": target}
    if kind == "memory":
        return {**action, "target_mb": target}
    if kind == "disk_capacity":
        return {**action, "target_gb": target}
    return action


def _recommendation(
    host: right_sizing.HostAssessment, stats: right_sizing.ResourceStats, rec: right_sizing.Recommendation
) -> RsRecommendation:

    # 대칭) — 분류와 무관하게 orthogonal 노출해야 해서 여기서 별도 append.
    io_advisory = (
        [_action("disk_io", host.resources["disk_io"], "tier_up")]
        if host.resources["disk_io"].status == "io_bound"
        else []
    )
    if rec == "under_provisioned":
        kinds = right_sizing.prescribed_under_kinds(host)
        actions = [_action(k, host.resources[k], "increase") for k in kinds] + io_advisory
        return {
            "summary": right_sizing.under_prescription(host),
            "kind": "provision",
            "actions": actions,
            "suppressed": [],
        }
    if rec == "over_provisioned":
        actions = [
            _action(k, host.resources[k], "decrease")
            for k in ("cpu", "memory")
            if host.resources[k].status == "over"
            and right_sizing.downsize_prescribable(host.resources[k], stats)
            and host.resources[k].sizing_target is not None
        ]

        return {
            "summary": right_sizing.recommend_action(rec, stats),
            "kind": "downsize",
            "actions": actions + io_advisory,
            "suppressed": [],
        }
    _kind = {"idle": "decommission", "insufficient_data": "insufficient", "optimal": "maintain"}.get(rec, "maintain")
    return {
        "summary": right_sizing.recommend_action(rec, stats),
        "kind": _kind,
        "actions": io_advisory,
        "suppressed": [],
    }


def build_right_sizing_entry(raw: ReportRowRaw, is_online: bool, hostname_ambiguous: bool = False) -> JsonObject:
    # 계약 API 는 net baseline 만 주입받는다 — disk 활동 축은 미관측(보고서 경로 전용).
    stats = build_resource_stats(raw, disk_baseline=None)
    host = right_sizing.rollup_host(stats)
    rec = right_sizing.host_status_to_recommendation(host.host_status)
    net = host.resources["network"]
    return {
        "hostname": raw.hostname,
        "hostname_ambiguous": hostname_ambiguous,
        "public_id": raw.public_id,
        "primary_ip": primary_ip(raw),
        "os_family": raw.os_family,
        "online": is_online,
        "classification": rec,
        "classification_label": right_sizing.RECOMMENDATION_LABEL_KO[rec],
        "root_cause": right_sizing.root_cause_display(host) or None,
        "recommendation": _recommendation(host, stats, rec),
        "confidence_notes": build_host_confidence_notes(host),
        "resources": {
            "cpu": _cpu_resource(raw, stats, host),
            "memory": _memory_resource(raw, stats, host),
            "disk": _disk_resource(raw, stats, host),
        },
        "network": {
            "status": net.status,
            "status_label": _STATUS_LABEL_KO.get(net.status, net.status),
            "congested": host.network_congested,
            "signals": {
                "retransmit_pct": _net_signal(raw.net_retrans_pct, right_sizing.NET_RETRANS_PCT),
                "drop_pct": _net_signal(raw.net_drop_pct, right_sizing.NET_DROP_PCT),
                "conntrack_ratio": _net_signal(
                    raw.conntrack_ratio, right_sizing.CONNTRACK_SATURATION_RATIO, inclusive=True
                ),
            },
            "detail": net.detail or None,
        },
    }
