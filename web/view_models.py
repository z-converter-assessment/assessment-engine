from dataclasses import dataclass
from datetime import datetime


@dataclass
class DiskItem:
    name: str
    size_bytes: int | None
    type: str | None


# ---------- 서버 목록 ----------

@dataclass
class ServerListItem:
    id: int
    hostname: str
    os_id: str | None
    os_version: str | None
    cpu_cores: int | None
    mem_total_kb: int | None
    last_seen_at: datetime | None
    is_online: bool


# ---------- 서버 상세 ----------

@dataclass
class ServerDetailResponse:
    id: int
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
    disks: list[DiskItem]
    last_seen_at: datetime | None


# ---------- 스토리지 (인벤토리 + 실시간 사용량) ----------

@dataclass
class MountUsageItem:
    mount: str
    fstype: str | None
    total_gb: float | None
    used_gb: float | None
    avail_gb: float | None
    usage_pct: float | None


@dataclass
class StorageDetailResponse:
    server_id: int
    hostname: str
    disks: list[DiskItem]
    mounts: list[MountUsageItem]


# ---------- 네트워크 (IPs + 실시간 인터페이스 현황) ----------

@dataclass
class NetInterfaceItem:
    interface: str
    rx_kbps: float | None
    tx_kbps: float | None
    rx_pps: float | None
    tx_pps: float | None


@dataclass
class NetworkDetailResponse:
    server_id: int
    hostname: str
    ip_internal: list[str]
    ip_external: list[str] | None
    interfaces: list[NetInterfaceItem]


# ---------- 수집 상태 ----------

@dataclass
class CollectionStatusItem:
    last_metric_at: datetime | None
    last_inventory_at: datetime | None
    is_online: bool


# ---------- 메트릭 대시보드 (AJAX /metrics/latest) ----------

@dataclass
class CpuSnapshot:
    usage_pct: float | None
    user_pct: float | None
    system_pct: float | None
    iowait_pct: float | None


@dataclass
class MemSnapshot:
    total_kb: int | None
    used_kb: int | None        # total - available
    available_kb: int | None
    cached_kb: int | None
    buffers_kb: int | None
    usage_pct: float | None


@dataclass
class SwapSnapshot:
    total_kb: int | None
    used_kb: int | None
    usage_pct: float | None


@dataclass
class DiskIoSnapshot:
    device: str
    read_iops: float | None
    write_iops: float | None
    read_kbps: float | None
    write_kbps: float | None


@dataclass
class NetIoSnapshot:
    interface: str
    rx_kbps: float | None
    tx_kbps: float | None
    rx_pps: float | None
    tx_pps: float | None


@dataclass
class MountDashSnapshot:
    mount: str
    total_gb: float | None
    used_gb: float | None
    avail_gb: float | None
    usage_pct: float | None


@dataclass
class MetricDashboard:
    collected_at: datetime | None
    cpu: CpuSnapshot | None
    load_1m: float | None
    load_5m: float | None
    load_15m: float | None
    memory: MemSnapshot | None
    swap: SwapSnapshot | None
    disk_io: list[DiskIoSnapshot]
    net_io: list[NetIoSnapshot]
    mounts: list[MountDashSnapshot]


# ---------- 시계열 ----------

@dataclass
class MetricSeriesItem:
    collected_at: datetime
    value: float | None
    dimension: str | None