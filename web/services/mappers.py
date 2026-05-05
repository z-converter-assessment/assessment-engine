from db.repositories.outbound import (
    CollectionStatusResponse,
    MetricSeriesResponse,
    NetworkWithIoResponse,
    ServerListItemResponse,
    ServerResponse,
    StorageWithUsageResponse,
)
from web.services.filters import is_physical_disk, is_virtual_mount
from web.services.metrics_calculator import compute_net_io
from web.services.units import _bytes_to_gb, _kb_to_gb, _usage_pct
from web.view_models import (
    CollectionStatusItem,
    DiskItem,
    MetricSeriesItem,
    MountUsageItem,
    NetworkDetailResponse,
    ServerDetailResponse,
    ServerListItem,
    StorageDetailResponse,
)


def to_server_list_item(dto: ServerListItemResponse) -> ServerListItem:
    return ServerListItem(
        id=dto.id,
        hostname=dto.hostname,
        os_id=dto.os_id,
        os_version=dto.os_version,
        cpu_cores=dto.cpu_cores,
        mem_total_gb=_kb_to_gb(dto.mem_total_kb),
        last_seen_at=dto.last_seen_at,
        is_online=False,
        ip_external=dto.ip_external,
    )


def to_server_detail(dto: ServerResponse) -> ServerDetailResponse:
    return ServerDetailResponse(
        id=dto.id,
        machine_id=dto.machine_id,
        hostname=dto.hostname,
        agent_version=dto.agent_version,
        os_id=dto.os_id,
        os_version=dto.os_version,
        os_codename=dto.os_codename,
        kernel_version=dto.kernel_version,
        cpu_cores=dto.cpu_cores,
        cpu_model=dto.cpu_model,
        mem_total_gb=_kb_to_gb(dto.mem_total_kb),
        swap_total_gb=_kb_to_gb(dto.swap_total_kb),
        boot_time=dto.boot_time,
        ip_internal=dto.ip_internal,
        ip_external=dto.ip_external,
        disks=[
            DiskItem(name=d.get("name", ""), size_gb=_bytes_to_gb(d.get("size_bytes")), type=d.get("type"))
            for d in dto.disks
            if is_physical_disk(d.get("name", ""))
        ],
        last_seen_at=dto.last_seen_at,
    )


def to_storage_detail(dto: StorageWithUsageResponse) -> StorageDetailResponse:
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

    # live 데이터에만 존재하는 마운트 (인벤토리 갱신 전 edge case)
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
        hostname=dto.hostname,
        disks=[
            DiskItem(name=d.get("name", ""), size_gb=_bytes_to_gb(d.get("size_bytes")), type=d.get("type"))
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
    return MountUsageItem(
        mount=mount,
        fstype=fstype,
        total_gb=_bytes_to_gb(total_bytes),
        used_gb=_bytes_to_gb(used_bytes),
        avail_gb=_bytes_to_gb(avail_bytes),
        usage_pct=_usage_pct(used_bytes, total_bytes),
    )


def to_network_detail(dto: NetworkWithIoResponse) -> NetworkDetailResponse:
    collected_ats = [r.collected_at for r in dto.net_io]
    snapshot_at = max(collected_ats) if collected_ats else None
    return NetworkDetailResponse(
        server_id=dto.server_id,
        hostname=dto.hostname,
        ip_internal=dto.ip_internal,
        ip_external=dto.ip_external,
        interfaces=compute_net_io(dto.net_io),
        inventory_at=dto.inventory_at,
        snapshot_at=snapshot_at,
    )


def to_collection_status_item(dto: CollectionStatusResponse, is_online: bool) -> CollectionStatusItem:
    return CollectionStatusItem(
        last_metric_at=dto.last_metric_at,
        last_inventory_at=dto.last_inventory_at,
        is_online=is_online,
    )


def to_metric_series_item(dto: MetricSeriesResponse) -> MetricSeriesItem:
    return MetricSeriesItem(
        collected_at=dto.collected_at,
        value=dto.value,
        dimension=dto.dimension,
    )