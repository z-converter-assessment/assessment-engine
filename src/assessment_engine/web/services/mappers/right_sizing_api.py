"""Right-sizing API 매퍼 — 자동화/외부 소비용 per-server 프로비저닝 판정 dict.

화면 표시(ViewModel)와 별개 채널이나 분류·근거·신뢰도·권고는 전부 도메인 단일 진실(rollup_host·
recommendation 처방) 재사용 — 화면(보고서·자원평가)과 값 정합(재계산 0). 포화 신호는 표시 문자열이 아니라
stats 원자료·임계 상수로 numeric(파싱 계약).
자원 3축(CPU/메모리/디스크) 사이징 + 네트워크는 별도 품질 플래그(사이징 아님, ADR 0052 정합).
"""

from __future__ import annotations

from assessment_engine import recommendation
from assessment_engine.web.services.mappers.report import build_resource_stats
from assessment_engine.web.services.mappers.shared import (
    _CAUSE_LABEL_BY_TRIGGER,
    build_host_confidence_notes,
)

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


def _res_confidence_notes(c: recommendation.ConfidenceNote) -> list[str]:
    """자원별 신뢰도 하향 사유 — biased(virtio 구조 편향)는 상시라 노이즈로 제외(build_host_confidence_notes 정합)."""
    notes: list[str] = []
    if c.low_precision:
        notes.append("표본 부족")
    if c.coverage_gap:
        notes.append("포화 수치 미관측")
    if c.nonstationary:
        notes.append("상승 추세")
    return notes


def _evidence_labels(triggers: list[str]) -> list[str]:
    """trigger key -> 통일 한국어 근거 라벨(_CAUSE_LABEL_BY_TRIGGER 단일 진실). 미매핑 키는 그대로."""
    return [_CAUSE_LABEL_BY_TRIGGER.get(t, t) for t in triggers]


def _sizeable_recommendation(kind: str, ra: recommendation.ResourceAssessment) -> str | None:
    """자원 1개 사이징 권고 문구 — under/over(사이징 관련 상태)에만. 도메인 resource_prescription 단일 진실."""
    if ra.status in ("under", "over", "io_bound", "filling") or ra.sizing_target is not None:
        text = recommendation.resource_prescription(kind, ra)
        return text or None
    return None


def _net_signal(value: float | None, threshold: float, *, inclusive: bool = False) -> dict:
    """네트워크 품질 신호 1개 — 값·임계·초과여부·측정여부. resource saturation 블록과 대칭.

    inclusive = conntrack(>= 임계) 여부. retrans/drop 은 > 임계. 미측정(value None)이면 exceeded=None.
    """
    if value is None:
        return {"value": None, "threshold": threshold, "exceeded": None, "measured": False}
    exceeded = value >= threshold if inclusive else value > threshold
    return {"value": round(value, 3), "threshold": threshold, "exceeded": exceeded, "measured": True}


def _primary_ip(raw) -> str | None:
    for i in raw.interfaces or []:
        if i.get("kind") == "physical" and i.get("family") == "ipv4":
            return i.get("address")
    return None


def _sat_dict(signal: str, value: float | None, threshold: float | None, unit: str, saturated: bool | None) -> dict:
    """포화 신호 1건 — raw numeric(파싱 계약). 표시 문자열이 아니라 stats 원자료·recommendation 임계 상수.

    signal = OS별 신호 이름(라벨), unit = 값 단위(per_core·per_sec·ms·queue·event), saturated = 도메인 판정(bool|None).
    network.signals 와 동형 — value 미측정 시 null.
    """
    return {
        "signal": signal,
        "value": round(value, 2) if value is not None else None,
        "threshold": threshold,
        "unit": unit,
        "measured": value is not None,
        "saturated": saturated,
    }


def _sat_block(kind: str, stats) -> dict:
    """자원별 포화 신호 — os-aware raw 수치. saturation_axis_displays(표시용 "W 1.20") 대신 계약용 numeric."""
    win = stats.os_family == "windows"
    if kind == "cpu":
        rq = stats.cpu_run_queue_p95 if win else stats.procs_running_p95
        val = recommendation.cpu_saturation_index(rq, stats.cpu_cores, stats.os_family)
        thr = recommendation.CPU_RUN_QUEUE_PER_CORE_SATURATION if win else recommendation.PROCS_RUNNING_PER_CORE_SATURATION
        sig = "Processor Queue Length/core" if win else "run queue (procs_running)/core"
        return _sat_dict(sig, val, thr, "per_core", recommendation.cpu_saturated(stats))
    if kind == "memory":
        if win:
            return _sat_dict(
                "Pages Input/sec", stats.mem_pages_input_rate_p95, recommendation.WIN_PAGES_INPUT_SATURATION,
                "per_sec", recommendation.mem_saturated(stats),
            )
        # Linux swap page-out 은 발생 이벤트(수치 없음) — 판정은 saturated 로.
        sat = recommendation.mem_saturated(stats)
        return {"signal": "swap page-out", "value": None, "threshold": None, "unit": "event",
                "measured": sat is not None, "saturated": sat}
    # disk_io — await 우선(양 OS), 구세대 viostor 만 큐 폴백.
    if stats.disk_await_p95_ms is not None:
        return _sat_dict("await", stats.disk_await_p95_ms, recommendation.RS_DISKIO_AWAIT_MS, "ms",
                         recommendation.disk_io_saturated(stats))
    if stats.disk_queue_p95 is not None:
        return _sat_dict("Avg Disk Queue Length", stats.disk_queue_p95, recommendation.DISK_QUEUE_PER_DISK_SATURATION,
                         "queue", recommendation.disk_io_saturated(stats))
    return {"signal": "await", "value": None, "threshold": recommendation.RS_DISKIO_AWAIT_MS, "unit": "ms",
            "measured": False, "saturated": recommendation.disk_io_saturated(stats)}


def _cpu_resource(raw, stats, host) -> dict:
    ra = host.resources["cpu"]
    return {
        "status": ra.status,
        "status_label": _STATUS_LABEL_KO.get(ra.status, ra.status),
        "utilization_p95_pct": round(raw.cpu_p95_pct, 1) if raw.cpu_p95_pct is not None else None,
        "saturation": _sat_block("cpu", stats),
        "evidence": _evidence_labels(ra.triggers),
        "confidence_notes": _res_confidence_notes(ra.confidence),
        "sizing_target_cores": ra.sizing_target,
        "recommendation": _sizeable_recommendation("cpu", ra),
        "detail": ra.detail or None,
    }


def _memory_resource(raw, stats, host) -> dict:
    ra = host.resources["memory"]
    return {
        "status": ra.status,
        "status_label": _STATUS_LABEL_KO.get(ra.status, ra.status),
        "utilization_p95_pct": round(raw.mem_p95_pct, 1) if raw.mem_p95_pct is not None else None,
        "saturation": _sat_block("memory", stats),
        "evidence": _evidence_labels(ra.triggers),
        "confidence_notes": _res_confidence_notes(ra.confidence),
        "sizing_target_mb": ra.sizing_target,
        "recommendation": _sizeable_recommendation("memory", ra),
        "detail": ra.detail or None,
    }


def _disk_resource(raw, stats, host) -> dict:
    cap = host.resources["disk_capacity"]
    io = host.resources["disk_io"]
    return {
        "capacity": {
            "status": cap.status,
            "status_label": _STATUS_LABEL_KO.get(cap.status, cap.status),
            "worst_mount": raw.worst_mount,
            "worst_mount_used_pct": round(raw.worst_mount_used_pct, 1) if raw.worst_mount_used_pct is not None else None,
            "days_until_full": raw.worst_mount_days_until_full,
            "evidence": _evidence_labels(cap.triggers),
            "confidence_notes": _res_confidence_notes(cap.confidence),
            "sizing_target_gb": cap.sizing_target,
            "recommendation": _sizeable_recommendation("disk_capacity", cap),
            "detail": cap.detail or None,
        },
        "io": {
            "status": io.status,
            "status_label": _STATUS_LABEL_KO.get(io.status, io.status),
            "saturation": _sat_block("disk_io", stats),
            "evidence": _evidence_labels(io.triggers),
            "confidence_notes": _res_confidence_notes(io.confidence),
            "detail": io.detail or None,
        },
    }


_TARGET_KEY: dict[str, str] = {"cpu": "target_cores", "memory": "target_mb", "disk_capacity": "target_gb"}


def _action(kind: str, ra: recommendation.ResourceAssessment, op: str) -> dict:
    """조치 1건 — 자원·연산·타입 목표(있으면)·표시. 목표 수치는 타입별 키(target_cores/_mb/_gb)로 직접 파싱 가능."""
    a: dict = {"resource": kind, "op": op, "target_display": recommendation.resource_prescription(kind, ra) or None}
    key = _TARGET_KEY.get(kind)
    if key and ra.sizing_target is not None:
        a[key] = ra.sizing_target
    return a


def _recommendation(host, stats, rec: str) -> dict:
    """종합 권고 구조 (파싱용 견고 포맷) — 이 하나만 보고 조치를 결정한다.

    actions = 실제 조치 목록(근본원인만, 증상 억제) / suppressed = 증상 자원(근본원인 해결 후 재평가, 독립 조치 금지).
    예: 메모리 포화가 CPU 를 유발하면 actions=[메모리 증설]만, suppressed=[cpu] — per-resource CPU 타겟을 잘못 증설하지 않게.
    per-resource sizing_target 은 진단용(각 자원 독립 가정) — 실제 프로비저닝은 본 actions 를 파싱한다.
    """
    if rec == "under_provisioned":
        # under_prescription 과 동일 집합 — 인과 결합이면 root 만, 독립이면 전부.
        kinds = recommendation.prescribed_under_kinds(host)
        actions = [_action(k, host.resources[k], "tier_up" if k == "disk_io" else "increase") for k in kinds]
        return {
            "summary": recommendation.under_prescription(host),
            "kind": "provision",
            "actions": actions,
            "suppressed": list(host.symptom_of_root),
        }
    if rec == "over_provisioned":
        actions = [
            _action(k, host.resources[k], "decrease")
            for k in ("cpu", "memory")
            if host.resources[k].status == "over"
            and recommendation.downsize_prescribable(host.resources[k], stats)
            and host.resources[k].sizing_target is not None
        ]
        # 다운사이즈 게이트 미충족이면 actions=[] (분류는 과다지만 구체 처방 보류 — 관찰만).
        return {"summary": recommendation.recommend_action(rec, stats), "kind": "downsize", "actions": actions, "suppressed": []}
    _kind = {"idle": "decommission", "insufficient_data": "insufficient", "optimal": "maintain"}.get(rec, "maintain")
    return {"summary": recommendation.recommend_action(rec, stats), "kind": _kind, "actions": [], "suppressed": []}


def build_right_sizing_entry(raw, is_online: bool, hostname_ambiguous: bool = False) -> dict:
    """ReportRowRaw + is_online -> right-sizing 판정 dict (자원 3축 사이징 + 네트워크 품질).

    분류·근본원인·신뢰도·권고 전부 rollup_host 종합에서 파생 — 보고서/자원평가 화면과 값 정합(재계산 0).
    hostname_ambiguous = 이 hostname 이 환경 내 2대+ 공유(안전 신호) — 소비 측이 hostname 단독 대신 public_id/순서쌍 사용 판단.
    """
    stats = build_resource_stats(raw)
    host = recommendation.rollup_host(stats)
    rec = recommendation.host_status_to_recommendation(host.host_status)
    net = host.resources["network"]
    return {
        "hostname": raw.hostname,
        "hostname_ambiguous": hostname_ambiguous,  # 환경 내 동명 2대+ (안전 신호). true 면 public_id/순서쌍 권장.
        "public_id": raw.public_id,
        "primary_ip": _primary_ip(raw),
        "os_family": raw.os_family,
        "online": is_online,
        "classification": rec,
        "classification_label": recommendation.LABEL_KO[rec],
        "root_cause": recommendation.root_cause_display(host) or None,
        # 종합 권고 — 견고한 구조(파싱 대상). actions=근본원인만·suppressed=증상. per-resource 타겟보다 이게 실행 진실.
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
