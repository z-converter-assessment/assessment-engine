from db.repositories.outbound import (
    CollectionStatus,
    MetricSeries,
    NetworkWithIo,
    ServerSummary,
    ServerDetail,
    StorageWithUsage,
)
from web.services.device_filters import is_physical_disk, is_virtual_mount
from web.services.metrics_calculator import compute_net_io
from web.services.service_classifier import classify, matched_ports
from web.services.units import bytes_to_gb, kb_to_gb, usage_pct
from web.view_models import (
    CollectionStatusItem,
    DiskItem,
    ListenPortItem,
    MetricSeriesItem,
    MountUsageItem,
    NetworkDetailResponse,
    ServerDetailResponse,
    ServerListItem,
    ServiceItem,
    StorageDetailResponse,
)


def _usage_badge_class(pct: float | None) -> str:
    if pct is None:
        return ""
    if pct >= 90:
        return "badge-danger"
    if pct >= 75:
        return "badge-warn"
    return "badge-ok"


def _usage_bar_color(pct: float | None) -> str:
    if pct is None:
        return "#22c55e"
    if pct >= 90:
        return "#ef4444"
    if pct >= 75:
        return "#f59e0b"
    return "#22c55e"


def to_server_list_item(dto: ServerSummary) -> ServerListItem:
    physical = [d for d in dto.disks if is_physical_disk(d.get("name", ""))]
    raw_total = sum(bytes_to_gb(d.get("size_bytes")) or 0.0 for d in physical)
    storage_total_gb = round(raw_total, 1) if physical else None

    services = (
        [ServiceItem(unit=s.get("unit", ""), sub=s.get("sub", ""), category=classify(s.get("unit", "")), ports=[], display_name=s.get("unit", "").removesuffix(".service")) for s in dto.services]
        if dto.services is not None else None
    )
    _known = [s for s in services if s.category != "unknown"] if services is not None else []
    seen: set[str] = set()
    known_services: list[ServiceItem] = []
    for s in _known:
        if s.category not in seen:
            seen.add(s.category)
            known_services.append(s)
    show_unknown_badge = services is not None and bool(services) and not known_services

    os_parts = [p for p in [dto.os_id, dto.os_version] if p]
    os_display = " ".join(os_parts) or "-"

    return ServerListItem(
        id=dto.id,
        public_id=dto.public_id,
        hostname=dto.hostname,
        os_id=dto.os_id,
        os_version=dto.os_version,
        cpu_cores=dto.cpu_cores,
        mem_total_gb=kb_to_gb(dto.mem_total_kb),
        storage_total_gb=storage_total_gb,
        is_online=False,
        ip_external=dto.ip_external,
        services=services,
        known_services=known_services,
        show_unknown_badge=show_unknown_badge,
        os_display=os_display,
    )


def to_server_detail(dto: ServerDetail) -> ServerDetailResponse:
    detail = ServerDetailResponse(
        id=dto.id,
        public_id=dto.public_id,
        machine_id=dto.machine_id,
        hostname=dto.hostname,
        agent_version=dto.agent_version,
        os_id=dto.os_id,
        os_version=dto.os_version,
        os_codename=dto.os_codename,
        kernel_version=dto.kernel_version,
        cpu_cores=dto.cpu_cores,
        cpu_model=dto.cpu_model,
        mem_total_gb=kb_to_gb(dto.mem_total_kb),
        swap_total_gb=kb_to_gb(dto.swap_total_kb),
        boot_time=dto.boot_time,
        ip_internal=dto.ip_internal,
        ip_external=dto.ip_external,
        disks=[
            DiskItem(name=d.get("name", ""), size_gb=bytes_to_gb(d.get("size_bytes")), type=d.get("type"))
            for d in dto.disks
            if is_physical_disk(d.get("name", ""))
        ],
        services=(
            [ServiceItem(unit=s.get("unit", ""), sub=s.get("sub", ""), category=classify(s.get("unit", "")), ports=matched_ports(s.get("unit", ""), dto.listen_ports), display_name=s.get("unit", "").removesuffix(".service")) for s in dto.services]
            if dto.services is not None else None
        ),
        listen_ports=[
            ListenPortItem(
                proto=p.get("proto", ""),
                addr=p.get("addr", ""),
                port=p.get("port", 0),
                uid=p.get("uid", 0),
                pid=p.get("pid"),
                comm=p.get("comm"),
                is_well_known=p.get("port", 0) <= 1024,
            )
            for p in dto.listen_ports
        ],
        last_seen_at=dto.last_seen_at,
    )
    return enrich_server_detail(detail)


def to_storage_detail(dto: StorageWithUsage) -> StorageDetailResponse:
    usage_by_mount = {u.mount: u for u in dto.mount_usage}

    mounts: list[MountUsageItem] = []
    seen: set[str] = set()

    for inv in dto.inventory_mounts:
        path = inv.get("mount", "")
        fstype = inv.get("fstype")
        seen.add(path)
        if is_virtual_mount(fstype, path):
            continue
        usage = usage_by_mount.get(path)
        total_bytes = inv.get("total_bytes")
        mounts.append(_build_mount_item(
            mount=path,
            fstype=fstype,
            total_bytes=total_bytes,
            avail_bytes=usage.avail_bytes if usage else None,
        ))

    for path, usage in usage_by_mount.items():
        if path not in seen:
            if is_virtual_mount(None, path):
                continue
            mounts.append(_build_mount_item(
                mount=path, fstype=None,
                total_bytes=usage.total_bytes,
                avail_bytes=usage.avail_bytes,
            ))

    collected_ats = [u.collected_at for u in dto.mount_usage if u.collected_at is not None]
    snapshot_at = max(collected_ats) if collected_ats else None

    return StorageDetailResponse(
        server_id=dto.server_id,
        public_id=dto.public_id,
        hostname=dto.hostname,
        disks=[
            DiskItem(name=d.get("name", ""), size_gb=bytes_to_gb(d.get("size_bytes")), type=d.get("type"))
            for d in dto.disks
            if is_physical_disk(d.get("name", ""))
        ],
        mounts=sorted(mounts, key=lambda m: m.mount),
        snapshot_at=snapshot_at,
        inventory_at=dto.inventory_at,
    )


def _build_mount_item(
    mount: str, fstype: str | None, total_bytes: int | None, avail_bytes: int | None,
) -> MountUsageItem:
    used_bytes = (total_bytes - avail_bytes) if (total_bytes and avail_bytes is not None) else None
    pct = usage_pct(used_bytes, total_bytes)
    return MountUsageItem(
        mount=mount,
        fstype=fstype,
        total_gb=bytes_to_gb(total_bytes),
        used_gb=bytes_to_gb(used_bytes),
        avail_gb=bytes_to_gb(avail_bytes),
        usage_pct=pct,
        badge_class=_usage_badge_class(pct),
        bar_color=_usage_bar_color(pct),
    )


def to_network_detail(dto: NetworkWithIo) -> NetworkDetailResponse:
    collected_ats = [r.collected_at for r in dto.net_io]
    snapshot_at = max(collected_ats) if collected_ats else None
    return NetworkDetailResponse(
        server_id=dto.server_id,
        public_id=dto.public_id,
        hostname=dto.hostname,
        ip_internal=dto.ip_internal,
        ip_external=dto.ip_external,
        interfaces=compute_net_io(dto.net_io),
        inventory_at=dto.inventory_at,
        snapshot_at=snapshot_at,
    )


def to_collection_status_item(dto: CollectionStatus, is_online: bool) -> CollectionStatusItem:
    return CollectionStatusItem(
        last_metric_at=dto.last_metric_at,
        last_inventory_at=dto.last_inventory_at,
        is_online=is_online,
    )


def to_metric_series_item(dto: MetricSeries) -> MetricSeriesItem:
    return MetricSeriesItem(
        collected_at=dto.collected_at,
        value=dto.value,
        dimension=dto.dimension,
    )


def enrich_server_detail(detail: ServerDetailResponse) -> ServerDetailResponse:
    services = detail.services or []
    seen_chip_keys: set[str] = set()
    shown_port_numbers: set[int] = set()
    known: list[ServiceItem] = []

    for svc in services:
        if svc.category == "unknown":
            continue
        deduped: list[dict] = []
        for p in svc.ports:
            key = f"{p['proto']}:{p['port']}"
            if key not in seen_chip_keys:
                seen_chip_keys.add(key)
                shown_port_numbers.add(p["port"])
                deduped.append(p)
        known.append(ServiceItem(
            unit=svc.unit, sub=svc.sub, category=svc.category,
            ports=deduped, display_name=svc.display_name,
        ))

    detail.known_services = known
    detail.show_unknown_badge = (
        detail.services is not None and bool(detail.services) and not known
    )
    detail.key_listen_ports = sorted(
        [lp for lp in detail.listen_ports if lp.is_well_known and lp.port not in shown_port_numbers],
        key=lambda lp: (lp.port, lp.proto),
    )

    # 템플릿(P3)이 sort 못 하도록 mapper에서 한 번만 정렬
    detail.sorted_services = sorted(detail.services or [], key=lambda s: s.unit) if detail.services else []
    detail.sorted_listen_ports = sorted(detail.listen_ports, key=lambda lp: (lp.port, lp.proto))

    os_parts = [p for p in [detail.os_id, detail.os_version] if p]
    detail.os_display = " ".join(os_parts) or "-"

    cpu_parts = [p for p in [detail.cpu_model, f"{detail.cpu_cores} cores" if detail.cpu_cores else None] if p]
    detail.cpu_display = " ".join(cpu_parts) or "-"

    detail.disk_total_gb = round(sum(d.size_gb or 0.0 for d in detail.disks), 1) if detail.disks else None

    return detail