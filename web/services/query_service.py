from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from typing import TYPE_CHECKING

from redis.asyncio import Redis

from config import consumer_settings
from db.repositories.base_query_repository import BaseQueryRepository
from web.view_models import (
    CpuSnapshot,
    DiskItem,
    DiskIoSnapshot,
    MemSnapshot,
    MetricDashboard,
    MetricSeriesItem,
    MountDashSnapshot,
    MountUsageItem,
    NetInterfaceItem,
    NetIoSnapshot,
    NetworkDetailResponse,
    CollectionStatusItem,
    ServerDetailResponse,
    ServerListItem,
    StorageDetailResponse,
    SwapSnapshot,
)

if TYPE_CHECKING:
    from db.repositories.outbound import (
        CollectionStatusResponse,
        DashboardRaw,
        DiskIoRaw,
        MetricPairRaw,
        MetricSeriesResponse,
        MountUsageRaw,
        NetIoRaw,
        NetworkWithIoResponse,
        ServerListItemResponse,
        ServerResponse,
        StorageWithUsageResponse,
    )


# ============================================================
# Inventory → ViewModel 변환
# ============================================================

def _to_server_list_item(dto: ServerListItemResponse) -> ServerListItem:
    return ServerListItem(
        id=dto.id,
        hostname=dto.hostname,
        os_id=dto.os_id,
        os_version=dto.os_version,
        cpu_cores=dto.cpu_cores,
        mem_total_kb=dto.mem_total_kb,
        last_seen_at=dto.last_seen_at,
        is_online=False,
    )


def _to_server_detail(dto: ServerResponse) -> ServerDetailResponse:
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
        mem_total_kb=dto.mem_total_kb,
        swap_total_kb=dto.swap_total_kb,
        boot_time=dto.boot_time,
        ip_internal=dto.ip_internal,
        ip_external=dto.ip_external,
        disks=[
            DiskItem(name=d.get("name", ""), size_bytes=d.get("size_bytes"), type=d.get("type"))
            for d in dto.disks
        ],
        last_seen_at=dto.last_seen_at,
    )


def _to_storage_detail(dto: StorageWithUsageResponse) -> StorageDetailResponse:
    usage_by_mount = {u.mount: u for u in dto.mount_usage}

    mounts: list[MountUsageItem] = []
    seen: set[str] = set()

    for inv in dto.inventory_mounts:
        path = inv.get("mount", "")
        seen.add(path)
        usage = usage_by_mount.get(path)
        total_bytes = inv.get("total_bytes")
        mounts.append(_build_mount_item(
            mount=path,
            fstype=inv.get("fstype"),
            total_bytes=total_bytes,
            avail_bytes=usage.avail_bytes if usage else None,
        ))

    # live 데이터에만 존재하는 마운트 (인벤토리 갱신 전 edge case)
    for path, usage in usage_by_mount.items():
        if path not in seen:
            mounts.append(_build_mount_item(
                mount=path, fstype=None,
                total_bytes=usage.total_bytes,
                avail_bytes=usage.avail_bytes,
            ))

    return StorageDetailResponse(
        server_id=dto.server_id,
        hostname=dto.hostname,
        disks=[
            DiskItem(name=d.get("name", ""), size_bytes=d.get("size_bytes"), type=d.get("type"))
            for d in dto.disks
        ],
        mounts=sorted(mounts, key=lambda m: m.mount),
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


def _to_network_detail(dto: NetworkWithIoResponse) -> NetworkDetailResponse:
    return NetworkDetailResponse(
        server_id=dto.server_id,
        hostname=dto.hostname,
        ip_internal=dto.ip_internal,
        ip_external=dto.ip_external,
        interfaces=_compute_net_io(dto.net_io),
    )


def _to_collection_status_item(dto: CollectionStatusResponse, is_online: bool) -> CollectionStatusItem:
    return CollectionStatusItem(
        last_metric_at=dto.last_metric_at,
        last_inventory_at=dto.last_inventory_at,
        is_online=is_online,
    )


def _to_metric_series_item(dto: MetricSeriesResponse) -> MetricSeriesItem:
    return MetricSeriesItem(
        collected_at=dto.collected_at,
        value=dto.value,
        dimension=dto.dimension,
    )


# ============================================================
# Dashboard 계산 (delta / 단일 시점)
# ============================================================

def _build_dashboard(raw: DashboardRaw) -> MetricDashboard:
    cur = raw.metrics[0] if raw.metrics else None
    prev = raw.metrics[1] if len(raw.metrics) >= 2 else None

    return MetricDashboard(
        collected_at=cur.collected_at if cur else None,
        cpu=_compute_cpu(cur, prev),
        load_1m=cur.load_1m if cur else None,
        load_5m=cur.load_5m if cur else None,
        load_15m=cur.load_15m if cur else None,
        memory=_compute_mem(cur),
        swap=_compute_swap(cur),
        disk_io=_compute_disk_io(raw.disk_io),
        net_io=_compute_net_io(raw.net_io),
        mounts=_compute_mounts(raw.mounts),
    )


def _compute_cpu(cur: MetricPairRaw | None, prev: MetricPairRaw | None) -> CpuSnapshot | None:
    if cur is None:
        return None

    def cpu_total(r: MetricPairRaw) -> int | None:
        vals = [r.cpu_user, r.cpu_nice, r.cpu_system, r.cpu_idle,
                r.cpu_iowait, r.cpu_irq, r.cpu_softirq, r.cpu_steal]
        return None if any(v is None for v in vals) else sum(vals)  # type: ignore[arg-type]

    if prev is None:
        return CpuSnapshot(usage_pct=None, user_pct=None, system_pct=None, iowait_pct=None)

    dt = cpu_total(cur)
    dp = cpu_total(prev)
    if dt is None or dp is None:
        return CpuSnapshot(usage_pct=None, user_pct=None, system_pct=None, iowait_pct=None)

    delta_total = dt - dp
    if delta_total <= 0:
        return CpuSnapshot(usage_pct=None, user_pct=None, system_pct=None, iowait_pct=None)

    def pct(c: int | None, p: int | None) -> float | None:
        if c is None or p is None:
            return None
        return round(max(0.0, (c - p) / delta_total * 100), 1)

    idle_pct = pct(cur.cpu_idle, prev.cpu_idle)
    return CpuSnapshot(
        usage_pct=round(max(0.0, 100.0 - idle_pct), 1) if idle_pct is not None else None,
        user_pct=pct(cur.cpu_user, prev.cpu_user),
        system_pct=pct(cur.cpu_system, prev.cpu_system),
        iowait_pct=pct(cur.cpu_iowait, prev.cpu_iowait),
    )


def _compute_mem(cur: MetricPairRaw | None) -> MemSnapshot | None:
    if cur is None or cur.mem_total_kb is None:
        return None
    used = (cur.mem_total_kb - cur.mem_available_kb) if cur.mem_available_kb is not None else None
    return MemSnapshot(
        total_kb=cur.mem_total_kb,
        used_kb=used,
        available_kb=cur.mem_available_kb,
        cached_kb=cur.mem_cached_kb,
        buffers_kb=cur.mem_buffers_kb,
        usage_pct=_usage_pct(used, cur.mem_total_kb),
    )


def _compute_swap(cur: MetricPairRaw | None) -> SwapSnapshot | None:
    if cur is None or not cur.swap_total_kb:
        return None
    used = (cur.swap_total_kb - cur.swap_free_kb) if cur.swap_free_kb is not None else None
    return SwapSnapshot(
        total_kb=cur.swap_total_kb,
        used_kb=used,
        usage_pct=_usage_pct(used, cur.swap_total_kb),
    )


def _compute_disk_io(pairs: list[DiskIoRaw]) -> list[DiskIoSnapshot]:
    by_device: dict[str, list[DiskIoRaw]] = {}
    for r in pairs:
        by_device.setdefault(r.device, []).append(r)

    result = []
    for device, rows in sorted(by_device.items()):
        if len(rows) < 2:
            result.append(DiskIoSnapshot(device=device, read_iops=None, write_iops=None,
                                         read_kbps=None, write_kbps=None))
            continue
        cur, prev = rows[0], rows[1]
        dt = (cur.collected_at - prev.collected_at).total_seconds()
        if dt <= 0:
            result.append(DiskIoSnapshot(device=device, read_iops=None, write_iops=None,
                                         read_kbps=None, write_kbps=None))
            continue

        def iorate(c: int, p: int) -> float | None:
            d = c - p
            return None if d < 0 else round(d / dt, 1)  # counter reset → None

        result.append(DiskIoSnapshot(
            device=device,
            read_iops=iorate(cur.reads_completed, prev.reads_completed),
            write_iops=iorate(cur.writes_completed, prev.writes_completed),
            read_kbps=_sector_to_kbps(cur.sectors_read, prev.sectors_read, dt),
            write_kbps=_sector_to_kbps(cur.sectors_written, prev.sectors_written, dt),
        ))
    return result


def _compute_net_io(pairs: list[NetIoRaw]) -> list[NetIoSnapshot]:
    by_iface: dict[str, list[NetIoRaw]] = {}
    for r in pairs:
        by_iface.setdefault(r.interface, []).append(r)

    result = []
    for iface, rows in sorted(by_iface.items()):
        if len(rows) < 2:
            result.append(NetIoSnapshot(interface=iface, rx_kbps=None, tx_kbps=None,
                                        rx_pps=None, tx_pps=None))
            continue
        cur, prev = rows[0], rows[1]
        dt = (cur.collected_at - prev.collected_at).total_seconds()
        if dt <= 0:
            result.append(NetIoSnapshot(interface=iface, rx_kbps=None, tx_kbps=None,
                                        rx_pps=None, tx_pps=None))
            continue

        def brate(c: int, p: int) -> float | None:
            d = c - p
            return None if d < 0 else round(d / 1024 / dt, 1)

        def prate(c: int, p: int) -> float | None:
            d = c - p
            return None if d < 0 else round(d / dt, 1)

        result.append(NetIoSnapshot(
            interface=iface,
            rx_kbps=brate(cur.rx_bytes, prev.rx_bytes),
            tx_kbps=brate(cur.tx_bytes, prev.tx_bytes),
            rx_pps=prate(cur.rx_packets, prev.rx_packets),
            tx_pps=prate(cur.tx_packets, prev.tx_packets),
        ))
    return result


def _compute_mounts(mounts: list[MountUsageRaw]) -> list[MountDashSnapshot]:
    result = []
    for m in sorted(mounts, key=lambda x: x.mount):
        used_bytes = (m.total_bytes - m.avail_bytes) if (m.total_bytes and m.avail_bytes is not None) else None
        result.append(MountDashSnapshot(
            mount=m.mount,
            total_gb=_bytes_to_gb(m.total_bytes),
            used_gb=_bytes_to_gb(used_bytes),
            avail_gb=_bytes_to_gb(m.avail_bytes),
            usage_pct=_usage_pct(used_bytes, m.total_bytes),
        ))
    return result


# ============================================================
# 유틸
# ============================================================

def _bytes_to_gb(b: int | None) -> float | None:
    return round(b / 1024 ** 3, 2) if b is not None else None


def _usage_pct(used: int | None, total: int | None) -> float | None:
    if used is None or not total:
        return None
    return round(max(0.0, used / total * 100), 1)


def _sector_to_kbps(cur: int, prev: int, dt: float) -> float | None:
    d = cur - prev
    return None if d < 0 else round(d * 512 / 1024 / dt, 1)


def _json_default(obj: object) -> str:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Cannot serialize {type(obj)}")


# ============================================================
# 캐시 직렬화 / 역직렬화
# ============================================================

def _server_detail_to_json(v: ServerDetailResponse) -> str:
    return json.dumps(dataclasses.asdict(v), default=_json_default)


def _server_detail_from_json(raw: str) -> ServerDetailResponse:
    data = json.loads(raw)
    data["disks"] = [DiskItem(**d) for d in data.get("disks") or []]
    for field in ("boot_time", "last_seen_at"):
        if isinstance(data.get(field), str):
            data[field] = datetime.fromisoformat(data[field])
    return ServerDetailResponse(**data)


def _dashboard_to_json(v: MetricDashboard) -> str:
    return json.dumps(dataclasses.asdict(v), default=_json_default)


def _dashboard_from_json(raw: str) -> MetricDashboard:
    data = json.loads(raw)
    return MetricDashboard(
        collected_at=data.get("collected_at"),
        cpu=CpuSnapshot(**data["cpu"]) if data.get("cpu") else None,
        load_1m=data.get("load_1m"),
        load_5m=data.get("load_5m"),
        load_15m=data.get("load_15m"),
        memory=MemSnapshot(**data["memory"]) if data.get("memory") else None,
        swap=SwapSnapshot(**data["swap"]) if data.get("swap") else None,
        disk_io=[DiskIoSnapshot(**d) for d in data.get("disk_io") or []],
        net_io=[NetIoSnapshot(**n) for n in data.get("net_io") or []],
        mounts=[MountDashSnapshot(**m) for m in data.get("mounts") or []],
    )


# ============================================================
# QueryService
# ============================================================

class QueryService:
    def __init__(self, repo: BaseQueryRepository, redis: Redis):
        self.repo = repo
        self.redis = redis

    async def _is_online(self, server_id: int) -> bool:
        return bool(await self.redis.exists(consumer_settings.redis_key_online.format(server_id)))

    async def list_servers(
        self,
        page: int,
        limit: int,
        search: str | None,
        is_online: bool | None,
    ) -> list[ServerListItem]:
        dtos = await self.repo.list_servers(page, limit, search, None)
        items: list[ServerListItem] = []
        for dto in dtos:
            item = _to_server_list_item(dto)
            item.is_online = await self._is_online(dto.id)
            items.append(item)
        if is_online is not None:
            items = [i for i in items if i.is_online == is_online]
        return items

    async def get_server(self, server_id: int) -> ServerDetailResponse | None:
        cache_key = consumer_settings.redis_key_cache_inventory.format(server_id)
        cached = await self.redis.get(cache_key)
        if cached:
            return _server_detail_from_json(cached)

        dto = await self.repo.get_server(server_id)
        if not dto:
            return None
        result = _to_server_detail(dto)
        await self.redis.set(cache_key, _server_detail_to_json(result), ex=300)
        return result

    async def get_storage(self, server_id: int) -> StorageDetailResponse | None:
        dto = await self.repo.get_storage(server_id)
        return _to_storage_detail(dto) if dto else None

    async def get_network(self, server_id: int) -> NetworkDetailResponse | None:
        dto = await self.repo.get_network(server_id)
        return _to_network_detail(dto) if dto else None

    async def get_collection_status(self, server_id: int) -> list[CollectionStatusItem]:
        dtos = await self.repo.get_collection_status(server_id)
        online = await self._is_online(server_id)
        return [_to_collection_status_item(dto, online) for dto in dtos]

    async def get_latest_metric(self, server_id: int) -> MetricDashboard | None:
        cache_key = consumer_settings.redis_key_cache_metrics.format(server_id)
        cached = await self.redis.get(cache_key)
        if cached:
            return _dashboard_from_json(cached)

        raw = await self.repo.latest_dashboard(server_id)
        if not raw or not raw.metrics:
            return None
        result = _build_dashboard(raw)
        await self.redis.set(cache_key, _dashboard_to_json(result), ex=60)
        return result

    async def get_metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeriesItem]:
        dtos = await self.repo.metric_snapshots(server_id, cursor, limit)
        return [_to_metric_series_item(dto) for dto in dtos]

    async def get_metric_chart(
        self,
        server_id: int,
        metric_type: str,
        dimension: str | None,
        time_range: str,
        bucket: str,
        agg: str,
    ) -> list[MetricSeriesItem]:
        dtos = await self.repo.metric_chart(server_id, metric_type, dimension, time_range, bucket, agg)
        return [_to_metric_series_item(dto) for dto in dtos]