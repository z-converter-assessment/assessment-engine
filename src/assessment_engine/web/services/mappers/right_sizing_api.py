"""Right-sizing API 매퍼 — 자동화/외부 소비용 per-server 프로비저닝 판정 dict.

화면 표시(ViewModel)와 별개 채널이나 분류·근거·신뢰도·권고는 전부 도메인 단일 진실(rollup_host·
recommendation 처방) 재사용 — 화면(보고서·자원평가)과 값 정합(재계산 0). 포화 신호는 표시 문자열이 아니라
stats 원자료·임계 상수로 numeric(파싱 계약).
자원 3축(CPU/메모리/디스크) 사이징 + 네트워크는 별도 품질 플래그(사이징 아님, ADR 0052 정합).
"""

from typing import TYPE_CHECKING

from assessment_engine import recommendation
from assessment_engine.web.services.device_filters import disk_total_bytes
from assessment_engine.web.services.mappers.resource_stats import build_resource_stats
from assessment_engine.web.services.mappers.shared import (
    _CAUSE_LABEL_BY_TRIGGER,
    build_host_confidence_notes,
    primary_ip,
    resource_confidence_notes,
    saturation_block,
)
from assessment_engine.web.services.unit_converter import bytes_to_gb

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import ReportRowRaw
    from assessment_engine.json_types import JsonObject

# ResourceStatus(도메인 enum) -> 외부 노출 한국어 라벨. 프로젝트 통일 어휘(자원 적정성 화면과 동일 개념).
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


def _evidence_labels(triggers: list[recommendation.TriggerKind]) -> list[str]:
    """trigger key -> 통일 한국어 근거 라벨. _CAUSE_LABEL_BY_TRIGGER(자원부족 축) 우선, 미커버 키는

    도메인 RS_TRIGGER_LABEL_KO 폴백 — mem_oom·net_retrans/drop/conntrack 이 raw enum 으로 누출되지 않게.
    """
    return [_CAUSE_LABEL_BY_TRIGGER.get(t) or recommendation.RS_TRIGGER_LABEL_KO.get(t, t) for t in triggers]


def _sizeable_recommendation(kind: recommendation.ResourceKind, ra: recommendation.ResourceAssessment) -> str | None:
    """자원 1개 사이징 권고 문구 — under/over(사이징 관련 상태)에만. 도메인 resource_prescription 단일 진실."""
    if ra.status in ("under", "over", "io_bound", "filling") or ra.sizing_target is not None:
        text = recommendation.resource_prescription(kind, ra)
        return text or None
    return None


def _net_signal(value: float | None, threshold: float, *, inclusive: bool = False) -> JsonObject:
    """네트워크 품질 신호 1개 — 값·임계·초과여부·측정여부. resource saturation 블록과 대칭.

    inclusive = conntrack(>= 임계) 여부. retrans/drop 은 > 임계. 미측정(value None)이면 exceeded=None.
    """
    if value is None:
        return {"value": None, "threshold": threshold, "exceeded": None, "measured": False}
    exceeded = value >= threshold if inclusive else value > threshold
    return {"value": round(value, 3), "threshold": threshold, "exceeded": exceeded, "measured": True}


def _cpu_resource(
    raw: ReportRowRaw, stats: recommendation.ResourceStats, host: recommendation.HostAssessment
) -> JsonObject:
    ra = host.resources["cpu"]
    return {
        "status": ra.status,
        "status_label": _STATUS_LABEL_KO.get(ra.status, ra.status),
        "utilization_p95_pct": round(raw.cpu_p95_pct, 1) if raw.cpu_p95_pct is not None else None,
        "saturation": saturation_block("cpu", stats),
        "evidence": _evidence_labels(ra.triggers),
        "confidence_notes": resource_confidence_notes(ra.confidence),
        "current_cores": stats.cpu_cores,  # 현재 배정 — target 과 짝(현재 vs 목표)
        "sizing_target_cores": ra.sizing_target,
        "recommendation": _sizeable_recommendation("cpu", ra),
        "detail": ra.detail or None,
    }


def _memory_resource(
    raw: ReportRowRaw, stats: recommendation.ResourceStats, host: recommendation.HostAssessment
) -> JsonObject:
    ra = host.resources["memory"]
    return {
        "status": ra.status,
        "status_label": _STATUS_LABEL_KO.get(ra.status, ra.status),
        "utilization_p95_pct": round(raw.mem_p95_pct, 1) if raw.mem_p95_pct is not None else None,
        "saturation": saturation_block("memory", stats),
        "evidence": _evidence_labels(ra.triggers),
        "confidence_notes": resource_confidence_notes(ra.confidence),
        "current_mb": stats.mem_total_mb,  # 현재 배정 RAM — target 과 짝
        "sizing_target_mb": ra.sizing_target,
        "recommendation": _sizeable_recommendation("memory", ra),
        "detail": ra.detail or None,
    }


def _disk_resource(
    raw: ReportRowRaw, stats: recommendation.ResourceStats, host: recommendation.HostAssessment
) -> JsonObject:
    cap = host.resources["disk_capacity"]
    io = host.resources["disk_io"]
    # 현재 배정 디스크 총량 — block_devices type=disk size_bytes 합(disk_total_bytes 단일 산식, 양 OS).
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
            "current_gb": int(_dgb) if _dgb is not None else None,  # 현재 배정 — target 과 짝
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


_TARGET_KEY: dict[str, str] = {"cpu": "target_cores", "memory": "target_mb", "disk_capacity": "target_gb"}


def _action(kind: recommendation.ResourceKind, ra: recommendation.ResourceAssessment, op: str) -> JsonObject:
    """조치 1건 — 자원·연산·타입 목표(있으면)·표시. 목표 수치는 타입별 키(target_cores/_mb/_gb)로 직접 파싱 가능."""
    a: JsonObject = {
        "resource": kind,
        "op": op,
        "target_display": recommendation.resource_prescription(kind, ra) or None,
    }
    key = _TARGET_KEY.get(kind)
    if key and ra.sizing_target is not None:
        a[key] = ra.sizing_target
    return a


def _recommendation(
    host: recommendation.HostAssessment, stats: recommendation.ResourceStats, rec: recommendation.Recommendation
) -> JsonObject:
    """종합 권고 구조 (파싱용 견고 포맷) — 이 하나만 보고 조치를 결정한다.

    actions = 관측된 under 자원 전부(자원별 독립, 인과에 의한 억제 없음 — assessment API sizing.axes 와 동일
    정책). suppressed 는 항상 빈 배열 — 기존 소비자가 키 존재를 가정할 수 있어 제거하지 않고 둔다. 인과 근거는 summary/root_cause 필드
    (root_cause_display, "왜")가 전달 — actions 자체(무엇을)는 자원마다 독립 판정.
    per-resource sizing_target 은 자원별 독립 목표 — actions 목록과 정합(둘 다 같은 독립 판정 소스).
    """
    # disk_io io_bound = 크기로 안 풀리는 tier advisory — under/over 분류와 무관하게 orthogonal 노출
    # (network congested 대칭, io_bound 는 사이징 축 아님). host 를 under 로 승격하지 않으므로 여기서 별도 append.
    io_advisory = (
        [_action("disk_io", host.resources["disk_io"], "tier_up")]
        if host.resources["disk_io"].status == "io_bound"
        else []
    )
    if rec == "under_provisioned":
        kinds = recommendation.prescribed_under_kinds(host)  # 관측된 under 자원 전부(사이징 축만) — 억제 없음.
        actions = [_action(k, host.resources[k], "increase") for k in kinds] + io_advisory
        return {
            "summary": recommendation.under_prescription(host),
            "kind": "provision",
            "actions": actions,
            "suppressed": [],
        }
    if rec == "over_provisioned":
        actions = [
            _action(k, host.resources[k], "decrease")
            for k in ("cpu", "memory")
            if host.resources[k].status == "over"
            and recommendation.downsize_prescribable(host.resources[k], stats)
            and host.resources[k].sizing_target is not None
        ]
        # 다운사이즈 게이트 미충족이면 actions=[](분류는 과다지만 구체 처방 보류). io_bound advisory 는 별개로 붙임.
        return {
            "summary": recommendation.recommend_action(rec, stats),
            "kind": "downsize",
            "actions": actions + io_advisory,
            "suppressed": [],
        }
    _kind = {"idle": "decommission", "insufficient_data": "insufficient", "optimal": "maintain"}.get(rec, "maintain")
    return {
        "summary": recommendation.recommend_action(rec, stats),
        "kind": _kind,
        "actions": io_advisory,
        "suppressed": [],
    }


def build_right_sizing_entry(raw: ReportRowRaw, is_online: bool, hostname_ambiguous: bool = False) -> JsonObject:
    """ReportRowRaw + is_online -> right-sizing 판정 dict (자원 3축 사이징 + 네트워크 품질).

    분류·근본원인·신뢰도·권고 전부 rollup_host 종합에서 파생 — 보고서/자원평가 화면과 값 정합(재계산 0).
    hostname_ambiguous = 이 hostname 이 환경 내 2대+ 공유(안전 신호) — 소비 측이 hostname 단독 대신 public_id/순서쌍 사용 판단.
    """
    # 계약 API 는 net baseline 만 주입받는다 — disk 활동 축은 미관측(보고서 경로 전용).
    stats = build_resource_stats(raw, disk_baseline=None)
    host = recommendation.rollup_host(stats)
    rec = recommendation.host_status_to_recommendation(host.host_status)
    net = host.resources["network"]
    return {
        "hostname": raw.hostname,
        "hostname_ambiguous": hostname_ambiguous,  # 환경 내 동명 2대+ (안전 신호). true 면 public_id/순서쌍 권장.
        "public_id": raw.public_id,
        "primary_ip": primary_ip(raw),
        "os_family": raw.os_family,
        "online": is_online,
        "classification": rec,
        "classification_label": recommendation.LABEL_KO[rec],
        "root_cause": recommendation.root_cause_display(host) or None,
        # 종합 권고 — 견고한 구조(파싱 대상). actions=관측된 under 자원 전부(독립). per-resource 타겟과 정합.
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
            # 품질 신호 3종(사이징 아닌 별도 축) — 값·임계·초과. 하나라도 초과면 congested. monitoring 표준 임계.
            "signals": {
                "retransmit_pct": _net_signal(raw.net_retrans_pct, recommendation.RS_NET_RETRANS_PCT),
                "drop_pct": _net_signal(raw.net_drop_pct, recommendation.RS_NET_DROP_PCT),
                "conntrack_ratio": _net_signal(
                    raw.conntrack_ratio, recommendation.RS_CONNTRACK_SATURATION_RATIO, inclusive=True
                ),
            },
            "detail": net.detail or None,
        },
    }
