"""Inventory JSON Export mapper — ServerDetail + ReportRowRaw → InventoryExportEntry (P2).

벤더 중립 정제 v2 스키마. 자동화 도구 입력으로 활용. 사용처: `/api/exports/inventory`.
스키마·정제 원칙·사용처 deep dive: docs/products/json-export.md.
"""

from assessment_engine import recommendation
from assessment_engine.db.dtos.outbound import (
    InventoryExportEntry,
    ReportRowRaw,
    ServerDetail,
)
from assessment_engine.web.services.device_filters import find_parent_disk
from assessment_engine.web.services.mappers.server import infer_role
from assessment_engine.web.services.service_classifier import SERVICE_PORTS, classify


def _split_disks(disks: list[dict], mounts: list[dict]) -> tuple[int | None, list[dict]]:
    """disks 중 가장 큰 1개를 boot, 나머지를 additional로 분리.

    additional의 mount_point는 find_parent_disk(mount.major/minor -> disk) 역방향 매칭.
    fstype은 동일 mount의 fstype 필드. iops_baseline은 mapper 호출자가 별도 주입.
    """
    if not disks:
        return (None, [])
    sorted_disks = sorted(disks, key=lambda d: d.get("size_bytes") or 0, reverse=True)
    boot = sorted_disks[0]
    boot_gb = (boot["size_bytes"] // 10**9) if boot.get("size_bytes") else None
    additional: list[dict] = []
    for d in sorted_disks[1:]:
        mount_point = None
        fstype = None
        for m in mounts or []:
            if find_parent_disk(m.get("major"), m.get("minor"), [d]) == d.get("name"):
                mount_point = m.get("mount")
                fstype = m.get("fstype")
                break
        size_gb = (d["size_bytes"] // 10**9) if d.get("size_bytes") else None
        additional.append(
            {
                "mount_point": mount_point,
                "size_gb": size_gb,
                "fstype": fstype,
            }
        )
    return (boot_gb, additional)


def _network_addresses(ip_internal: list[str] | None, ip_external: list[str] | None) -> list[dict]:
    """v4·v6 family 자동 분류 — `:` 포함 시 v6, 아니면 v4. scope는 input 파라미터로 결정."""
    out: list[dict] = []
    for ip in ip_internal or []:
        out.append({"scope": "internal", "family": "v6" if ":" in ip else "v4", "address": ip})
    for ip in ip_external or []:
        out.append({"scope": "external", "family": "v6" if ":" in ip else "v4", "address": ip})
    return out


def _services_for_export(services: list[dict] | None, listen_ports: list[dict] | None = None) -> list[dict]:
    """services[] + listen_ports[] -> [{category, unit, listeners}] for SG 자동화 입력.

    `unknown` 카테고리는 제외 — 보안그룹 룰 자동 생성에 의미 없음.
    listeners: ports 매핑별 실제 (proto, address) 정보 — listen_ports inventory와 매칭.
    매칭 실패 시 service_classifier의 `SERVICE_PORTS` 폴백 (proto=tcp, address=0.0.0.0 가정).
    """
    if not services:
        return []
    # listen_ports를 port → list of (proto, addr) 인덱스 (서비스가 여러 인터페이스 listen할 수 있음)
    by_port: dict[int, list[dict]] = {}
    for lp in listen_ports or []:
        if not isinstance(lp, dict):
            continue
        port = lp.get("port")
        if port is None:
            continue
        by_port.setdefault(port, []).append(
            {
                "proto": lp.get("proto") or "tcp",
                "address": lp.get("addr") or "0.0.0.0",
            }
        )

    out: list[dict] = []
    for s in services:
        unit = s.get("unit") if isinstance(s, dict) else None
        if not unit:
            continue
        cat = classify(unit)
        if cat == "unknown":
            continue
        # SERVICE_PORTS는 unit normalized 이름(`nginx`/`postgresql` 등) 키 — classifier와 동일 normalize 의무.
        unit_normalized = unit.lower().removesuffix(".service")
        port_list: list[int] = []
        for keyword, ports in SERVICE_PORTS.items():
            if keyword in unit_normalized:
                port_list = ports
                break
        listeners: list[dict] = []
        for port in port_list:
            matched = by_port.get(port, [])
            if matched:
                for m in matched:
                    listeners.append({"port": port, "proto": m["proto"], "address": m["address"]})
            else:
                # 폴백 — 카테고리 표준 포트만 명시 (proto/address는 자동화 도구가 기본 가정 사용)
                listeners.append({"port": port, "proto": "tcp", "address": "0.0.0.0"})
        out.append({"category": cat, "unit": unit, "listeners": listeners})
    return out


def to_inventory_export_entry(
    detail: ServerDetail,
    stats: ReportRowRaw | None = None,
) -> InventoryExportEntry:
    """ServerDetail(outbound) + 선택적 ReportRowRaw -> InventoryExportEntry v3.

    `stats`가 None이면 right-sizing 필드 null로 발행 — 신규 서버 / 데이터 부족 시.

    AI narrative 본질 catalog 본 시점 본질 catalog 본 시점 정공 — 자동화 도구 (Terraform · Ansible 등)
    안 자연어 parse 불가, 구조화 데이터 (`recommended_size_class` 필드) 만 활용 catalog 정공.
    """
    boot_gb, additional = _split_disks(detail.disks, detail.mounts)
    if stats is not None:
        rec = recommendation.classify(
            recommendation.ResourceStats(
                cpu_p95_pct=stats.cpu_p95_pct,
                cpu_peak_pct=stats.cpu_peak_pct,
                cpu_load_15m_max=stats.load_15m_max,
                cpu_cores=stats.cpu_cores,
                mem_p95_pct=stats.mem_p95_pct,
                swap_used=stats.swap_used,
                disk_used_pct=stats.worst_mount_used_pct,
                iowait_p95_pct=stats.iowait_p95_pct,
                net_avg_kbps=None,  # 현재 net 집계 미통합 — idle/shutdown 판정 skip
            )
        )
        cpu_p95 = stats.cpu_p95_pct
        cpu_peak = stats.cpu_peak_pct
        mem_p95 = stats.mem_p95_pct
        mem_peak = stats.mem_peak_pct
        load_15m_max = stats.load_15m_max
        swap_used = stats.swap_used
    else:
        rec = "insufficient_data"
        cpu_p95 = cpu_peak = mem_p95 = mem_peak = load_15m_max = None
        swap_used = False

    return InventoryExportEntry(
        composite_id=detail.composite_id,
        hostname=detail.hostname,
        role=infer_role(detail.services),
        last_seen_at=detail.last_seen_at,
        services=_services_for_export(detail.services, detail.listen_ports),
        os={
            "family": detail.os_id,
            "version": detail.os_version,
            "kernel": detail.kernel_version,
        },
        compute={
            "vcpu_count": detail.cpu_cores,
            "memory_mb": (detail.mem_total_kb // 1024) if detail.mem_total_kb else None,
            "cpu_p95_pct": cpu_p95,
            "cpu_peak_pct": cpu_peak,
            "mem_p95_pct": mem_p95,
            "mem_peak_pct": mem_peak,
            "load_15m_max": load_15m_max,
            "swap_used": swap_used,
            "recommended_size_class": {
                "key": rec,
                "label": recommendation.LABEL_KO.get(rec, rec),
            },
        },
        storage={
            "boot_disk_gb": boot_gb,
            "iops_baseline": stats.disk_iops_baseline if stats else None,
            "iops_p95": stats.disk_iops_p95 if stats else None,
            "iops_peak": stats.disk_iops_peak if stats else None,
            "throughput_kbps_baseline": stats.disk_throughput_kbps if stats else None,
            "throughput_kbps_p95": stats.disk_throughput_kbps_p95 if stats else None,
            "throughput_kbps_peak": stats.disk_throughput_kbps_peak if stats else None,
            "additional_disks": additional,
        },
        network={
            "addresses": _network_addresses(detail.ip_internal, detail.ip_external),
            "rx_kbps_baseline": stats.net_rx_kbps if stats else None,
            "rx_kbps_p95": stats.net_rx_kbps_p95 if stats else None,
            "rx_kbps_peak": stats.net_rx_kbps_peak if stats else None,
            "tx_kbps_baseline": stats.net_tx_kbps if stats else None,
            "tx_kbps_p95": stats.net_tx_kbps_p95 if stats else None,
            "tx_kbps_peak": stats.net_tx_kbps_peak if stats else None,
        },
    )
