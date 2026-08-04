"""Assessment API 매퍼 — /api/assessment per-server 통합 계약 dict.

계약 단일 진실: docs/reference/contracts/assessment-api.md. decision-first 2계층 —
identity/reproduction/sizing(axes[])/assessment 가 1급, diagnostics 는 선택 소비.

분류/사이징/근본원인은 도메인 단일 진실(rollup_host + recommendation) 재사용 — 화면/right-sizing API 와 값 정합.
reproduction 은 인벤토리(os 서술자·boot·nonblock_mounts 컬럼 + block_devices/net_interfaces/lvm_vgs JSONB)를
계약 OUTPUT 형태로 reshape. sizing 은 near-peak 메모리 + p95 CPU + per-mount 디스크(도메인 assess_*).
"""

from __future__ import annotations

from assessment_engine import recommendation
from assessment_engine.contract import API_CONTRACT_VERSION
from assessment_engine.db.dtos.outbound import MountCapacityRaw, ReportRowRaw
from assessment_engine.json_types import JsonObject, json_list
from assessment_engine.web.services.mappers.report import build_resource_stats
from assessment_engine.web.services.mappers.shared import (
    build_host_confidence_notes,
    primary_ip,
    resource_confidence_notes,
    saturation_block,
)


# ─── identity ──────────────────────────────────────────────
def _identity(raw: ReportRowRaw, is_online: bool, hostname_ambiguous: bool) -> JsonObject:
    return {
        "public_id": raw.public_id,
        "hostname": raw.hostname,
        "hostname_ambiguous": hostname_ambiguous,
        "primary_ip": primary_ip(raw),
        "os_family": raw.os_family,
        "online": is_online,
    }


# ─── reproduction ──────────────────────────────────────────
# bond_mode raw 커널 토큰 -> 계약 enum(에이전트는 raw 발행, 엔진 정규화). 미매핑/None -> None.
_BOND_MODE_MAP = {
    "802.3ad": "lacp", "active-backup": "active-backup", "balance-rr": "balance-rr",
    "balance-xor": "balance-xor", "broadcast": "broadcast",
    "balance-tlb": "balance-tlb", "balance-alb": "balance-alb",
}


def _norm_bond_mode(v: object) -> str | None:
    return _BOND_MODE_MAP.get(v) if isinstance(v, str) else None


def _norm_raid_level(v: object) -> int | None:
    """raid 레벨 문자열/숫자 -> int|null. 비수치(linear/multipath 등) -> null(계약 int enum)."""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    s = str(v).lower().removeprefix("raid")
    return int(s) if s.isdigit() else None


# 계약 4.3 block_devices 전 키 — OUTPUT 은 "전 키 존재(null)" 규약(미수집 키도 노출). wire 는 자연노드만(엔진이 채움).
_BLOCK_DEVICE_KEYS = (
    "id", "id_type", "name", "type", "parent", "size_bytes",
    "partition_table", "sector_size", "serial", "wwn", "rotational",
    "part_number", "part_start_bytes", "part_type", "part_name", "part_flags",
    "fstype", "fs_uuid", "fs_label", "block_size", "mountpoint", "mount_options", "fs_freq", "fs_passno",
    "lvm_vg", "lvm_lv", "lvm_segtype", "lvm_stripes", "lvm_stripe_size_kib",
    "raid_level", "raid_chunk_kib", "raid_metadata", "raid_uuid", "crypt_type",
)


def _repro_block_device(d: JsonObject) -> JsonObject:
    out = {k: d.get(k) for k in _BLOCK_DEVICE_KEYS}
    out["raid_level"] = _norm_raid_level(d.get("raid_level"))
    return out


def _repro_interface(i: JsonObject, link_speeds: dict[str, int] | None = None) -> JsonObject:
    # speed_mbps null(Windows NT5.2/virtio 는 inventory 미발행) 시 metrics link.speed(bit/s)로 폴백 — reproduction
    # 정확도(agent 확정 규약). iface 안정 id 로 매칭. link.speed 도 null(virtio)이면 그대로 null.
    speed = i.get("speed_mbps")
    iface_id = i.get("id")
    if speed is None and link_speeds and isinstance(iface_id, str):
        bps = link_speeds.get(iface_id)
        if bps:
            speed = int(bps // 1_000_000)  # bit/s -> Mbps
    return {
        "id": i.get("id"), "id_type": i.get("id_type"), "name": i.get("name"), "kind": i.get("kind"),
        "mtu": i.get("mtu"),
        "addresses": [
            {"address": a.get("address"), "prefix": a.get("prefix"),
             "family": a.get("family"), "origin": a.get("origin")}
            for a in json_list(i, "addresses")
        ],
        "gateway": i.get("gateway"), "dns": i.get("dns"), "routes": i.get("routes"),
        "bond_mode": _norm_bond_mode(i.get("bond_mode")), "vlan_id": i.get("vlan_id"),
        "speed_mbps": speed,
    }


def _repro_lvm_vg(v: JsonObject) -> JsonObject:
    return {
        "name": v.get("name"), "vg_uuid": v.get("vg_uuid"),
        "size_bytes": v.get("size_bytes"), "free_bytes": v.get("free_bytes"),
        "extent_size_bytes": v.get("extent_size_bytes"), "pv_ids": v.get("pv_ids"),
    }


def _reproduction(raw: ReportRowRaw, link_speeds: dict[str, int] | None = None) -> JsonObject:
    """재현 팩트 — 현 인벤토리를 계약 OUTPUT 형태로 reshape.

    os 재현 서술자(arch/bits/boot_firmware 등)·boot·nonblock_mounts 는 server_inventory 전용 컬럼에서,
    레이아웃 상세(block_devices/lvm_vgs 세부)는 raw JSONB 에서 _repro_* 의 d.get 으로 픽업. 소비자는 값 부재 시
    null 처리(계약: 모든 키 present + nullable). speed_mbps 는 inventory null 시 link_speeds(metrics) 폴백.
    """
    boot = raw.boot or {}
    return {
        "os": {
            "family": raw.os_family, "id": raw.os_id, "version": raw.os_version,
            "codename": raw.os_codename, "kernel": raw.kernel_version,
            "arch": raw.arch, "bits": raw.bits, "boot_firmware": raw.boot_firmware,
            "secure_boot": raw.secure_boot, "edition": raw.edition,
            "timezone": raw.timezone, "rtc_utc": raw.rtc_utc,
        },
        "boot": {
            "kernel_cmdline": boot.get("kernel_cmdline"),
            "root_ref_type": boot.get("root_ref_type"),
            "grub_install_target": boot.get("grub_install_target"),
        },
        "network": {"interfaces": [_repro_interface(i, link_speeds) for i in raw.net_interfaces or []]},
        "storage": {
            "block_devices": [_repro_block_device(d) for d in raw.block_devices or []],
            "lvm_vgs": [_repro_lvm_vg(v) for v in raw.lvm_vgs or []],
        },
        "mounts": [
            {
                "source": m.get("source"), "target": m.get("target"), "fstype": m.get("fstype"),
                "options": m.get("options"), "fs_freq": m.get("fs_freq"), "fs_passno": m.get("fs_passno"),
            }
            for m in raw.nonblock_mounts or []
        ],
    }


# ─── sizing (axes[]) ───────────────────────────────────────
def _axis_size(current: int | float, ra: recommendation.ResourceAssessment, stats: recommendation.ResourceStats):
    """cpu/memory 축 판정 -> (recommended, action, estimate_quality). recommended never-null(current 폴백).

    under: 정확 목표 있으면 exact, floor 있으면 floor, 둘 다 없으면 uncertain(방어).
    over: 다운사이즈 게이트 통과 시 decrease(exact), 미충족이면 keep(과다는 diagnostics).
    unmeasured/insufficient: uncertain(current 유지). optimal: keep(exact).
    """
    status = ra.status
    if status == "under":
        if ra.sizing_target is not None:
            return ra.sizing_target, "increase", "exact"
        if ra.sizing_floor is not None:
            return ra.sizing_floor, "increase", "floor"
        return current, "keep", "uncertain"
    if status == "over":
        if recommendation.downsize_prescribable(ra, stats) and ra.sizing_target is not None:
            return ra.sizing_target, "decrease", "exact"
        return current, "keep", "exact"
    if status in ("unmeasured", "insufficient"):
        return current, "keep", "uncertain"
    return current, "keep", "exact"


def _device_ref(raw: ReportRowRaw, mountpoint: str | None) -> str | None:
    """마운트포인트 -> reproduction block_device id (트리 조인). fs 얹힌 노드가 mountpoint 를 가짐."""
    for d in raw.block_devices or []:
        if d.get("mountpoint") == mountpoint:
            return d.get("id")
    return None


def _sizing(raw: ReportRowRaw, stats: recommendation.ResourceStats, host: recommendation.HostAssessment, mounts: list[MountCapacityRaw]) -> JsonObject:
    """사이징 축 배열 — cpu/memory(호스트 1축) + disk(마운트별 N축). 소비자 전 원소 순회 + max(current,recommended)."""
    axes: list[JsonObject] = []
    for kind, unit, current in (("cpu", "vcpus", stats.cpu_cores), ("memory", "mib", stats.mem_total_mb)):
        if current is None:
            continue  # base 수량(코어/총 RAM) 미상 -> 축 생략(recommended never-null 보장, disk 축과 대칭)
        ra = host.resources[kind]
        rec, action, quality = _axis_size(current, ra, stats)
        axes.append({
            "axis": kind, "current": current, "recommended": rec,
            "unit": unit, "action": action, "estimate_quality": quality,
        })
    for m in mounts:
        s = recommendation.assess_mount_capacity(
            m.total_bytes, m.target_bytes, m.byte_runway_days, m.used_pct,
            m.inode_runway_days, m.inode_used_pct,
        )
        if s is None:
            continue  # total 미상 -> 사이징 불가(축 생략)
        axes.append({
            "axis": "disk", "mountpoint": m.mountpoint, "device_ref": _device_ref(raw, m.mountpoint),
            "current": s.current_gib, "recommended": s.recommended_gib,
            "unit": "gib", "action": s.action, "estimate_quality": s.estimate_quality,
            # 관측 근거(cpu/memory 의 utilization 과 대칭) + 크기로 안 풀리는 신호(inode 소진 등).
            "used_pct": round(m.used_pct, 1) if m.used_pct is not None else None,
            "runway_days": int(m.byte_runway_days) if m.byte_runway_days is not None else None,
            "note": s.note or None,
        })
    return {"axes": axes}


# ─── assessment ────────────────────────────────────────────
def _assessment(host: recommendation.HostAssessment) -> JsonObject:
    """호스트 종합 판정 — classification(도메인 Recommendation, 계약 enum 과 동일 값) + confidence + data_quality.

    confidence: insufficient_data -> low / 하향 사유 있으면 medium / 없으면 high. 불변식(high 아니면 notes 최소 1).
    """
    rec = recommendation.host_status_to_recommendation(host.host_status)
    notes = build_host_confidence_notes(host)
    if rec == "insufficient_data":
        confidence = "low"
    elif notes:
        confidence = "medium"
    else:
        confidence = "high"
    if confidence != "high" and not notes:
        notes = ["관측 데이터 부족"]  # 불변식: high 아니면 notes 비지 않음
    return {
        "classification": rec,
        "confidence": confidence,
        "data_quality": {"sufficient": confidence == "high", "notes": notes},
    }


# ─── diagnostics (선택 소비) ───────────────────────────────
_DIAG_AXES = ("cpu", "memory", "disk_capacity", "disk_io", "network")


def _diag_util(kind: str, raw: ReportRowRaw) -> JsonObject:
    """utilization eval/sizing — eval=판정 p95, sizing=축별 사이징 통계(cpu p95, memory near-peak, 비사이징 null)."""
    if kind == "cpu":
        p = round(raw.cpu_p95_pct, 1) if raw.cpu_p95_pct is not None else None
        return {"eval_pct": p, "sizing_pct": p}
    if kind == "memory":
        return {
            "eval_pct": round(raw.mem_p95_pct, 1) if raw.mem_p95_pct is not None else None,
            "sizing_pct": round(raw.mem_near_peak_pct, 1) if raw.mem_near_peak_pct is not None else None,
        }
    return {"eval_pct": None, "sizing_pct": None}


def _diag_resource(kind: str, ra: recommendation.ResourceAssessment, raw: ReportRowRaw, stats: recommendation.ResourceStats) -> JsonObject:
    axis = "disk" if kind == "disk_capacity" else kind
    return {
        "axis": axis,
        "status": ra.status,
        "utilization": _diag_util(kind, raw),
        "saturation": saturation_block(kind, stats) if kind in ("cpu", "memory", "disk_io") else None,
        "confidence_notes": resource_confidence_notes(ra.confidence),
    }


def _root_cause_axis(host: recommendation.HostAssessment):
    """근본원인 축 소문자 enum (disk_capacity -> disk) | null."""
    rc = host.root_cause
    if rc is None:
        return None
    return "disk" if rc == "disk_capacity" else rc


def _diagnostics(raw: ReportRowRaw, stats: recommendation.ResourceStats, host: recommendation.HostAssessment) -> JsonObject:
    return {
        "root_cause": _root_cause_axis(host),
        "root_cause_detail": recommendation.root_cause_display(host) or None,
        "resources": [_diag_resource(k, host.resources[k], raw, stats) for k in _DIAG_AXES],
        "advisory": {
            # 디스크 I/O 티어 힌트 단일 권위(호스트 단위) — 엔진에 per-device await 없어 per-disk 미제공.
            "disk_io_tier_hint": "high_iops" if host.resources["disk_io"].status == "io_bound" else None,
            "network_congested": host.network_congested,
        },
    }


def build_assessment_entry(
    raw: ReportRowRaw, mounts: list[MountCapacityRaw], is_online: bool, hostname_ambiguous: bool = False,
    link_speeds: dict[str, int] | None = None,
) -> JsonObject:
    """ReportRowRaw + per-mount 용량 + online -> /api/assessment 서버 항목 (계약 4.2).

    분류/사이징/근본원인 전부 rollup_host 종합에서 파생 — 화면/right-sizing API 와 값 정합(재계산 0).
    link_speeds = iface별 최신 link.speed(bit/s) — inventory speed_mbps null 폴백(reproduction 정확도).
    """
    stats = build_resource_stats(raw)
    host = recommendation.rollup_host(stats)
    return {
        "identity": _identity(raw, is_online, hostname_ambiguous),
        "reproduction": _reproduction(raw, link_speeds),
        "sizing": _sizing(raw, stats, host, mounts),
        "assessment": _assessment(host),
        "diagnostics": _diagnostics(raw, stats, host),
    }


def build_assessment_envelope(
    result: JsonObject,
    *,
    generated_at: str,
    window_days: int,
    window_start: str,
    window_end: str,
    filters: JsonObject,
) -> JsonObject:
    """계약 4.1 최상위 envelope — GET /api/assessment 와 POST export 가 공유(byte-identical 구조).

    result = QueryService.get_assessment 반환(servers + ambiguous_hostnames/unresolved_pairs/unmatched_filters).
    export(파일 다운로드)도 이 함수를 써 최상위 구조가 GET 응답과 동일하다(계약 8절 — 같은 데이터, 전달만 파일).
    """
    servers = result["servers"]
    return {
        "contract_version": API_CONTRACT_VERSION,
        "generated_at": generated_at,
        "window": {
            "days": window_days,
            "start": window_start,
            "end": window_end,
            "basis": "관측 창(기본 14일). 데이터가 창보다 짧으면 assessment.data_quality 로 신뢰도 하향.",
        },
        "filter": filters,
        "warnings": {
            "ambiguous_hostnames": result["ambiguous_hostnames"],
            "unresolved_pairs": result["unresolved_pairs"],
            "unmatched_filters": result["unmatched_filters"],
        },
        "count": len(servers),
        "servers": servers,
    }
