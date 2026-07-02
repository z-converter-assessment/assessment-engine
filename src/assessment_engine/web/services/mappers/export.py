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
from assessment_engine.service_classifier import classify, well_known_ports
from assessment_engine.web.services.device_filters import find_parent_disk, is_data_volume
from assessment_engine.web.services.mappers.report import build_resource_stats
from assessment_engine.web.services.mappers.server import infer_role


def _split_from_mounts(mounts: list[dict]) -> tuple[int | None, list[dict]]:
    """물리 disks 부재(Windows 등 미발행) 시 data volume mounts(total_bytes)로 boot/additional 도출.

    device_filters.disk_total_bytes 와 동일 fallback 정책 — export 디스크 정보 누락 0.
    """
    data = [m for m in (mounts or []) if is_data_volume(m.get("kind"))]
    if not data:
        return (None, [])
    sorted_m = sorted(data, key=lambda m: m.get("total_bytes") or 0, reverse=True)
    boot_gb = (sorted_m[0]["total_bytes"] // 10**9) if sorted_m[0].get("total_bytes") else None
    additional = [
        {
            "mount_point": m.get("mount"),
            "size_gb": (m["total_bytes"] // 10**9) if m.get("total_bytes") else None,
            "fstype": m.get("fstype"),
        }
        for m in sorted_m[1:]
    ]
    return (boot_gb, additional)


def _split_disks(disks: list[dict], mounts: list[dict]) -> tuple[int | None, list[dict]]:
    """disks 중 가장 큰 1개를 boot, 나머지를 additional로 분리.

    물리 disks 미발행(Windows 등)이면 data volume mounts 로 fallback(`_split_from_mounts`).
    additional의 mount_point는 find_parent_disk(mount.major/minor -> disk) 역방향 매칭.
    fstype은 동일 mount의 fstype 필드. iops_baseline은 mapper 호출자가 별도 주입.
    """
    if not disks:
        return _split_from_mounts(mounts)
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


def _network_addresses(interfaces: list[dict] | None, ip_external: list[str] | None) -> list[dict]:
    """internal 은 구조화 interfaces(family/address), external 은 문자열. scope·family 로 분류 (loopback 제외)."""
    out: list[dict] = []
    for i in interfaces or []:
        if i.get("kind") == "loopback":
            continue
        family = "v6" if i.get("family") == "ipv6" else "v4"
        out.append({"scope": "internal", "family": family, "address": i.get("address", "")})
    for ip in ip_external or []:
        out.append({"scope": "external", "family": "v6" if ":" in ip else "v4", "address": ip})
    return out


def _services_for_export(services: list[dict] | None, listen_ports: list[dict] | None = None) -> list[dict]:
    """services[] + listen_ports[] -> [{category, unit, listeners}] for SG 자동화 입력.

    `unknown` 카테고리는 제외 — 보안그룹 룰 자동 생성에 의미 없음.
    listeners: ports 매핑별 실제 (proto, address) 정보 — listen_ports inventory와 매칭.
    매칭 실패 시 service_classifier의 `well_known_ports` 폴백 (proto=tcp, address=0.0.0.0 가정).
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
        cat = classify(unit, listen_ports, s.get("pid"))
        if cat == "unknown":
            continue
        # well-known 포트는 unit normalized 이름 substring 매칭 — classifier 카탈로그 단일 진실.
        port_list = well_known_ports(unit)
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
    """ServerDetail(outbound) + 선택적 ReportRowRaw -> InventoryExportEntry v4 (사용처축 배치).

    블록 = 사용처 1:1 — spec(VM 생성) / usage(right-sizing 측정) / assessment(평가 결과) / services(보안그룹).
    `stats`가 None이면 usage 측정값 null + assessment=insufficient_data — 신규 서버 / 데이터 부족 시.
    """
    boot_gb, additional = _split_disks(detail.disks, detail.mounts)
    if stats is not None:
        # 분류 입력은 build_resource_stats 단일 진실 — net baseline 포함, 보고서·대시보드와 동일 분류.
        rec = recommendation.classify(build_resource_stats(stats))
    else:
        rec = "insufficient_data"

    return InventoryExportEntry(
        identity={
            "composite_id": detail.composite_id,
            "hostname": detail.hostname,
            "role": infer_role(detail.services, detail.listen_ports),
            "last_seen_at": detail.last_seen_at,
        },
        os={
            "family": detail.os_id,
            "version": detail.os_version,
            "kernel": detail.kernel_version,
        },
        spec={
            "vcpu_count": detail.cpu_cores,
            "memory_mb": (detail.mem_total_kb // 1024) if detail.mem_total_kb else None,
            "boot_disk_gb": boot_gb,
            "additional_disks": additional,
            "addresses": _network_addresses(detail.interfaces, detail.ip_external),
        },
        usage={
            "cpu": {
                "p95_pct": stats.cpu_p95_pct if stats else None,
                "peak_pct": stats.cpu_peak_pct if stats else None,
            },
            "mem": {
                "p95_pct": stats.mem_p95_pct if stats else None,
                "peak_pct": stats.mem_peak_pct if stats else None,
            },
            # saturation raw 신호 — os-aware (분류 근거 가시성). Linux 는 load/swap/iowait, Windows 는
            # run queue/paging/disk queue 축이 채워진다(반대 OS 축은 null = 미측정). 소비자가 채워진 축으로
            # under_provisioned 근거를 확인. Windows 임계: run queue/cores>=2, Pages/sec>=1000, disk queue>=2.
            "load_15m_max": stats.load_15m_max if stats else None,
            "cpu_run_queue_p95": stats.cpu_run_queue_p95 if stats else None,
            "swap_used": stats.swap_used if stats else False,
            "mem_paging_rate_p95": stats.mem_paging_rate_p95 if stats else None,
            "disk_io": {
                "iops_baseline": stats.disk_iops_baseline if stats else None,
                "iops_p95": stats.disk_iops_p95 if stats else None,
                "iops_peak": stats.disk_iops_peak if stats else None,
                "throughput_kbps_baseline": stats.disk_throughput_kbps if stats else None,
                "throughput_kbps_p95": stats.disk_throughput_kbps_p95 if stats else None,
                "throughput_kbps_peak": stats.disk_throughput_kbps_peak if stats else None,
                "iowait_p95_pct": stats.iowait_p95_pct if stats else None,
                "queue_p95": stats.disk_queue_p95 if stats else None,
            },
            "network": {
                "rx_kbps_baseline": stats.net_rx_kbps if stats else None,
                "rx_kbps_p95": stats.net_rx_kbps_p95 if stats else None,
                "rx_kbps_peak": stats.net_rx_kbps_peak if stats else None,
                "tx_kbps_baseline": stats.net_tx_kbps if stats else None,
                "tx_kbps_p95": stats.net_tx_kbps_p95 if stats else None,
                "tx_kbps_peak": stats.net_tx_kbps_peak if stats else None,
            },
        },
        assessment={
            "recommended_size_class": {
                "key": rec,
                "label": recommendation.LABEL_KO.get(rec, rec),
            },
        },
        services=_services_for_export(detail.services, detail.listen_ports),
    )
