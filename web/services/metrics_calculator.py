from db.repositories.outbound import (
    DashboardRaw,
    DiskIoRaw,
    MetricPairRaw,
    MountUsageRaw,
    NetIoRaw,
)
from web.services.filters import is_virtual_mount
from web.services.units import _bytes_to_gb, _sector_to_kbps, _usage_pct
from web.view_models import (
    CpuSnapshot,
    DiskIoSnapshot,
    MemSnapshot,
    MetricDashboard,
    MountDashSnapshot,
    NetIoSnapshot,
    SwapSnapshot,
)


def build_dashboard(raw: DashboardRaw) -> MetricDashboard:
    cur = raw.metrics[0] if raw.metrics else None
    prev = raw.metrics[1] if len(raw.metrics) >= 2 else None

    return MetricDashboard(
        collected_at=cur.collected_at if cur else None,
        cpu=compute_cpu(cur, prev),
        load_1m=cur.load_1m if cur else None,
        load_5m=cur.load_5m if cur else None,
        load_15m=cur.load_15m if cur else None,
        memory=compute_mem(cur),
        swap=compute_swap(cur),
        disk_io=compute_disk_io(raw.disk_io),
        net_io=compute_net_io(raw.net_io),
        mounts=compute_mounts(raw.mounts),
    )


def compute_cpu(cur: MetricPairRaw | None, prev: MetricPairRaw | None) -> CpuSnapshot | None:
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


def compute_mem(cur: MetricPairRaw | None) -> MemSnapshot | None:
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


def compute_swap(cur: MetricPairRaw | None) -> SwapSnapshot | None:
    if cur is None or not cur.swap_total_kb:
        return None
    used = (cur.swap_total_kb - cur.swap_free_kb) if cur.swap_free_kb is not None else None
    return SwapSnapshot(
        total_kb=cur.swap_total_kb,
        used_kb=used,
        usage_pct=_usage_pct(used, cur.swap_total_kb),
    )


def compute_disk_io(pairs: list[DiskIoRaw]) -> list[DiskIoSnapshot]:
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
            return None if d < 0 else round(d / dt, 1)

        result.append(DiskIoSnapshot(
            device=device,
            read_iops=iorate(cur.reads_completed, prev.reads_completed),
            write_iops=iorate(cur.writes_completed, prev.writes_completed),
            read_kbps=_sector_to_kbps(cur.sectors_read, prev.sectors_read, dt),
            write_kbps=_sector_to_kbps(cur.sectors_written, prev.sectors_written, dt),
        ))
    return result


def compute_net_io(pairs: list[NetIoRaw]) -> list[NetIoSnapshot]:
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


def compute_mounts(mounts: list[MountUsageRaw]) -> list[MountDashSnapshot]:
    result = []
    for m in sorted(mounts, key=lambda x: x.mount):
        if is_virtual_mount(None, m.mount):
            continue
        used_bytes = (m.total_bytes - m.avail_bytes) if (m.total_bytes and m.avail_bytes is not None) else None
        result.append(MountDashSnapshot(
            mount=m.mount,
            total_gb=_bytes_to_gb(m.total_bytes),
            used_gb=_bytes_to_gb(used_bytes),
            avail_gb=_bytes_to_gb(m.avail_bytes),
            usage_pct=_usage_pct(used_bytes, m.total_bytes),
        ))
    return result