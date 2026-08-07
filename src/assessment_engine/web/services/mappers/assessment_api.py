"""Assessment API 매퍼 — /api/assessment per-server 계약 dict.

계약 단일 진실: docs/reference/contracts/assessment-api.md.
분류·사이징·근본원인은 right_sizing.rollup_host 재사용 — 화면·right-sizing API 와 값이 갈라지지 않는다.
"""

from typing import TYPE_CHECKING, cast

from assessment_engine.contract import API_CONTRACT_VERSION
from assessment_engine.domain import right_sizing
from assessment_engine.json_types import JsonObject, json_list
from assessment_engine.web.services.mappers.assessment_display import (
    build_host_confidence_notes,
    resource_confidence_notes,
    saturation_block,
)
from assessment_engine.web.services.mappers.host_display import primary_ip
from assessment_engine.web.services.mappers.resource_stats import build_resource_stats
from assessment_engine.web.view_models.assessment_api import (
    Assessment,
    AssessmentIdentity,
    DiagUtilization,
    ReproBlockDevice,
    Reproduction,
    ReproInterface,
    ReproLvmVg,
)

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import MountCapacityRaw, ReportRowRaw


def _identity(raw: ReportRowRaw, is_online: bool, hostname_ambiguous: bool) -> AssessmentIdentity:
    return {
        "public_id": raw.public_id,
        "hostname": raw.hostname,
        "hostname_ambiguous": hostname_ambiguous,
        "primary_ip": primary_ip(raw),
        "os_family": raw.os_family,
        "online": is_online,
    }


# 에이전트는 raw 커널 토큰을 발행한다 — 계약 enum 정규화는 엔진 몫. 미매핑은 None.
_BOND_MODE_MAP = {
    "802.3ad": "lacp",
    "active-backup": "active-backup",
    "balance-rr": "balance-rr",
    "balance-xor": "balance-xor",
    "broadcast": "broadcast",
    "balance-tlb": "balance-tlb",
    "balance-alb": "balance-alb",
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


# 스키마에서 파생 — 키 목록을 손으로 한 번 더 적으면 필드가 늘 때 여기만 옛 상태로 남는다.
# 계약 OUTPUT 은 미수집 키도 null 로 내보내므로 순서·집합이 스키마와 같아야 한다.
_BLOCK_DEVICE_KEYS = tuple(ReproBlockDevice.__annotations__)


def _repro_block_device(d: JsonObject) -> ReproBlockDevice:
    # 키 집합이 스키마에서 파생되므로 모양은 맞지만, 동적 키 생성이라 pyright 가 그것을 증명하지 못한다.
    out = {k: d.get(k) for k in _BLOCK_DEVICE_KEYS}
    out["raid_level"] = _norm_raid_level(d.get("raid_level"))
    return cast("ReproBlockDevice", out)


def _repro_interface(i: JsonObject, link_speeds: dict[str, int] | None = None) -> ReproInterface:
    # inventory 가 speed_mbps 를 안 싣는 환경(Windows NT5.2·virtio)에서 metrics link.speed(bit/s)로 폴백.
    # link.speed 도 없으면 null 그대로 둔다.
    speed = i.get("speed_mbps")
    iface_id = i.get("id")
    if speed is None and link_speeds and isinstance(iface_id, str):
        bps = link_speeds.get(iface_id)
        if bps:
            speed = int(bps // 1_000_000)  # bit/s -> Mbps
    return {
        "id": i.get("id"),
        "id_type": i.get("id_type"),
        "name": i.get("name"),
        "kind": i.get("kind"),
        "mtu": i.get("mtu"),
        "addresses": [
            {
                "address": a.get("address"),
                "prefix": a.get("prefix"),
                "family": a.get("family"),
                "origin": a.get("origin"),
            }
            for a in json_list(i, "addresses")
        ],
        "gateway": i.get("gateway"),
        "dns": i.get("dns"),
        "routes": i.get("routes"),
        "bond_mode": _norm_bond_mode(i.get("bond_mode")),
        "vlan_id": i.get("vlan_id"),
        "speed_mbps": speed,
    }


def _repro_lvm_vg(v: JsonObject) -> ReproLvmVg:
    return {
        "name": v.get("name"),
        "vg_uuid": v.get("vg_uuid"),
        "size_bytes": v.get("size_bytes"),
        "free_bytes": v.get("free_bytes"),
        "extent_size_bytes": v.get("extent_size_bytes"),
        "pv_ids": v.get("pv_ids"),
    }


def _reproduction(raw: ReportRowRaw, link_speeds: dict[str, int] | None = None) -> Reproduction:
    """재현 팩트 — 인벤토리를 계약 OUTPUT 형태로 reshape (값 부재는 키 유지 + null)."""
    boot = raw.boot or {}
    return {
        "os": {
            "family": raw.os_family,
            "id": raw.os_id,
            "version": raw.os_version,
            "codename": raw.os_codename,
            "kernel": raw.kernel_version,
            "arch": raw.arch,
            "bits": raw.bits,
            "boot_firmware": raw.boot_firmware,
            "secure_boot": raw.secure_boot,
            "edition": raw.edition,
            "timezone": raw.timezone,
            "rtc_utc": raw.rtc_utc,
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
                "source": m.get("source"),
                "target": m.get("target"),
                "fstype": m.get("fstype"),
                "options": m.get("options"),
                "fs_freq": m.get("fs_freq"),
                "fs_passno": m.get("fs_passno"),
            }
            for m in raw.nonblock_mounts or []
        ],
    }


def _axis_size(current: float, ra: right_sizing.ResourceAssessment, stats: right_sizing.ResourceStats):
    """cpu/memory 축 판정 -> (recommended, action, estimate_quality). recommended 는 never-null(current 폴백)."""
    status = ra.status
    if status == "under":
        if ra.sizing_target is not None:
            return ra.sizing_target, "increase", "exact"
        if ra.sizing_floor is not None:
            return ra.sizing_floor, "increase", "floor"
        return current, "keep", "uncertain"
    if status == "over":
        if right_sizing.downsize_prescribable(ra, stats) and ra.sizing_target is not None:
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


def _sizing(
    raw: ReportRowRaw,
    stats: right_sizing.ResourceStats,
    host: right_sizing.HostAssessment,
    mounts: list[MountCapacityRaw],
) -> JsonObject:
    """사이징 축 배열 — cpu/memory 는 호스트 1축, disk 는 마운트별 N축."""
    axes: list[JsonObject] = []
    sizing_axes: tuple[tuple[right_sizing.ResourceKind, str, int | None], ...] = (
        ("cpu", "vcpus", stats.cpu_cores),
        ("memory", "mib", stats.mem_total_mb),
    )
    for kind, unit, current in sizing_axes:
        if current is None:
            continue  # 기준 수량 미상 -> 축 생략 (recommended never-null 유지)
        ra = host.resources[kind]
        rec, action, quality = _axis_size(current, ra, stats)
        axes.append(
            {
                "axis": kind,
                "current": current,
                "recommended": rec,
                "unit": unit,
                "action": action,
                "estimate_quality": quality,
            }
        )
    for m in mounts:
        s = right_sizing.assess_mount_capacity(
            m.total_bytes,
            m.target_bytes,
            m.byte_runway_days,
            m.used_pct,
            m.inode_runway_days,
            m.inode_used_pct,
        )
        if s is None:
            continue  # total 미상 -> 사이징 불가(축 생략)
        axes.append(
            {
                "axis": "disk",
                "mountpoint": m.mountpoint,
                "device_ref": _device_ref(raw, m.mountpoint),
                "current": s.current_gib,
                "recommended": s.recommended_gib,
                "unit": "gib",
                "action": s.action,
                "estimate_quality": s.estimate_quality,
                # 크기 조정으로 풀리지 않는 신호(inode 소진 등)까지 관측 근거로 싣는다.
                "used_pct": round(m.used_pct, 1) if m.used_pct is not None else None,
                "runway_days": int(m.byte_runway_days) if m.byte_runway_days is not None else None,
                "note": s.note or None,
            }
        )
    return {"axes": axes}


def _assessment(host: right_sizing.HostAssessment) -> Assessment:
    rec = right_sizing.host_status_to_recommendation(host.host_status)
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


_DIAG_AXES = ("cpu", "memory", "disk_capacity", "disk_io", "network")


def _diag_util(kind: str, raw: ReportRowRaw) -> DiagUtilization:
    """eval=판정 p95, sizing=축별 사이징 통계(cpu p95 / memory near-peak / 그 외 null)."""
    if kind == "cpu":
        p = round(raw.cpu_p95_pct, 1) if raw.cpu_p95_pct is not None else None
        return {"eval_pct": p, "sizing_pct": p}
    if kind == "memory":
        return {
            "eval_pct": round(raw.mem_p95_pct, 1) if raw.mem_p95_pct is not None else None,
            "sizing_pct": round(raw.mem_near_peak_pct, 1) if raw.mem_near_peak_pct is not None else None,
        }
    return {"eval_pct": None, "sizing_pct": None}


def _diag_resource(
    kind: str, ra: right_sizing.ResourceAssessment, raw: ReportRowRaw, stats: right_sizing.ResourceStats
) -> JsonObject:
    axis = "disk" if kind == "disk_capacity" else kind
    return {
        "axis": axis,
        "status": ra.status,
        "utilization": _diag_util(kind, raw),
        "saturation": saturation_block(kind, stats) if kind in ("cpu", "memory", "disk_io") else None,
        "confidence_notes": resource_confidence_notes(ra.confidence),
    }


def _root_cause_axis(host: right_sizing.HostAssessment):
    rc = host.root_cause
    if rc is None:
        return None
    return "disk" if rc == "disk_capacity" else rc


def _diagnostics(raw: ReportRowRaw, stats: right_sizing.ResourceStats, host: right_sizing.HostAssessment) -> JsonObject:
    return {
        "root_cause": _root_cause_axis(host),
        "root_cause_detail": right_sizing.root_cause_display(host) or None,
        "resources": [_diag_resource(k, host.resources[k], raw, stats) for k in _DIAG_AXES],
        "advisory": {
            # 엔진에 per-device await 가 없어 티어 힌트는 호스트 단위로만 낸다.
            "disk_io_tier_hint": "high_iops" if host.resources["disk_io"].status == "io_bound" else None,
            "network_congested": host.network_congested,
        },
    }


def build_assessment_entry(
    raw: ReportRowRaw,
    mounts: list[MountCapacityRaw],
    is_online: bool,
    hostname_ambiguous: bool = False,
    link_speeds: dict[str, int] | None = None,
) -> JsonObject:
    """ReportRowRaw + per-mount 용량 + online -> /api/assessment 서버 항목 (계약 4.2).

    link_speeds = iface별 최신 link.speed(bit/s) — inventory speed_mbps 가 null 일 때의 폴백.
    """
    # 계약 API 는 net baseline 만 주입받는다 — disk 활동 축은 미관측(보고서 경로 전용).
    stats = build_resource_stats(raw, disk_baseline=None)
    host = right_sizing.rollup_host(stats)
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
    """계약 4.1 최상위 envelope — GET /api/assessment 와 export 가 공유해 최상위 구조가 어긋나지 않는다."""
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
