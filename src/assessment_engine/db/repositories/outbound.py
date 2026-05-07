from dataclasses import dataclass
from datetime import datetime


@dataclass
class ServerSummary:
    id: int
    public_id: str
    machine_id: str
    hostname: str
    os_id: str | None
    os_version: str | None
    cpu_cores: int | None
    mem_total_kb: int | None
    ip_external: list[str] | None
    disks: list[dict]
    services: list[dict] | None
    last_seen_at: datetime | None  # Redis online TTL fallback 용도


@dataclass
class ServerDetail:
    id: int
    public_id: str
    machine_id: str
    hostname: str
    agent_version: str | None
    os_id: str | None
    os_version: str | None
    os_codename: str | None
    kernel_version: str | None
    cpu_cores: int | None
    cpu_model: str | None
    mem_total_kb: int | None
    swap_total_kb: int | None
    boot_time: datetime | None
    ip_internal: list[str]
    ip_external: list[str] | None
    disks: list[dict]
    mounts: list[dict]
    services: list[dict] | None
    listen_ports: list[dict]
    last_seen_at: datetime | None


@dataclass
class CollectionStatus:
    last_metric_at: datetime | None
    last_inventory_at: datetime | None


# ---------- Dashboard raw DTOs (delta 계산용 2행 페어) ----------

@dataclass
class MetricPairRaw:
    collected_at: datetime
    cpu_user: int | None
    cpu_nice: int | None
    cpu_system: int | None
    cpu_idle: int | None
    cpu_iowait: int | None
    cpu_irq: int | None
    cpu_softirq: int | None
    cpu_steal: int | None
    mem_total_kb: int | None
    mem_free_kb: int | None
    mem_available_kb: int | None
    mem_buffers_kb: int | None
    mem_cached_kb: int | None
    swap_total_kb: int | None
    swap_free_kb: int | None
    load_1m: float | None
    load_5m: float | None
    load_15m: float | None


@dataclass
class DiskIoRaw:
    device: str
    collected_at: datetime
    reads_completed: int
    writes_completed: int
    sectors_read: int
    sectors_written: int


@dataclass
class NetIoRaw:
    interface: str
    collected_at: datetime
    rx_bytes: int
    tx_bytes: int
    rx_packets: int
    tx_packets: int
    rx_errors: int
    tx_errors: int


@dataclass
class MountUsageRaw:
    mount: str
    total_bytes: int | None
    avail_bytes: int | None
    free_bytes: int | None
    collected_at: datetime | None


@dataclass
class DashboardRaw:
    metrics: list[MetricPairRaw]   # 최대 2행, collected_at desc
    disk_io: list[DiskIoRaw]       # 디바이스당 최대 2행, desc within device
    net_io: list[NetIoRaw]         # 인터페이스당 최대 2행, desc within interface
    mounts: list[MountUsageRaw]    # 마운트당 최신 1행


# ---------- Storage / Network 풍부화 DTOs ----------

@dataclass
class StorageWithUsage:
    server_id: int
    public_id: str
    hostname: str
    disks: list[dict]            # 인벤토리 JSONB: {name, size_bytes, type}
    inventory_mounts: list[dict] # 인벤토리 JSONB: {mount, fstype, total_bytes}
    mount_usage: list[MountUsageRaw]
    inventory_at: datetime | None


@dataclass
class NetworkWithIo:
    server_id: int
    public_id: str
    hostname: str
    ip_internal: list[str]
    ip_external: list[str] | None
    net_io: list[NetIoRaw]       # 인터페이스당 최대 2행 (delta 계산용)
    inventory_at: datetime | None


# ---------- Series ----------

@dataclass
class MetricSeries:
    collected_at: datetime
    value: float | None
    dimension: str | None