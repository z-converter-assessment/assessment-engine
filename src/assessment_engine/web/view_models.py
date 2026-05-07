from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DiskItem:
    name: str
    size_gb: float | None
    type: str | None


@dataclass
class MatchedPort:
    """서비스 유닛에 매핑된 listen 포트 1개. service_classifier.matched_ports의 결과 단위."""
    proto: str
    port: int


@dataclass
class ServiceItem:
    unit: str
    sub: str
    category: str
    ports: list[MatchedPort]
    display_name: str = ""


@dataclass
class ListenPortItem:
    proto: str
    addr: str
    port: int
    uid: int
    pid: int | None
    comm: str | None
    is_well_known: bool = False  # port <= 1024. mapper에서 계산 (P2)


# ---------- 서버 목록 ----------

@dataclass
class ServerListItem:
    id: int
    public_id: str
    hostname: str
    os_id: str | None
    os_version: str | None
    cpu_cores: int | None
    mem_total_gb: float | None
    storage_total_gb: float | None
    is_online: bool
    ip_external: list[str] | None
    services: list[ServiceItem] | None
    known_services: list[ServiceItem] = field(default_factory=list)
    show_unknown_badge: bool = False
    os_display: str = ""


# ---------- 서버 상세 ----------


@dataclass
class ServerDetailResponse:
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
    mem_total_gb: float | None
    swap_total_gb: float | None
    boot_time: datetime | None
    ip_internal: list[str]
    ip_external: list[str] | None
    disks: list[DiskItem]
    services: list[ServiceItem] | None
    listen_ports: list[ListenPortItem]
    last_seen_at: datetime | None
    # 이하 mapper(enrich_server_detail)에서 채우는 파생 필드 — default 필수 (dataclass 순서 제약)
    sorted_services: list[ServiceItem] = field(default_factory=list)       # P3: unit ASC 정렬
    sorted_listen_ports: list[ListenPortItem] = field(default_factory=list) # P3: port ASC 정렬
    known_services: list[ServiceItem] = field(default_factory=list)
    show_unknown_badge: bool = False
    key_listen_ports: list[ListenPortItem] = field(default_factory=list)
    os_display: str = ""
    cpu_display: str = ""
    disk_total_gb: float | None = None


# ---------- 스토리지 (인벤토리 + 실시간 사용량) ----------

@dataclass
class MountUsageItem:
    mount: str
    fstype: str | None
    total_gb: float | None
    used_gb: float | None
    avail_gb: float | None
    usage_pct: float | None
    badge_class: str = ""
    bar_color: str = ""
    # mount가 어느 물리 디스크 위에 있는지 (major/minor 조인 결과). 매핑 실패 시 None.
    # mapper(to_storage_detail)에서 inventory.disks와 (major) 매칭으로 채움.
    device_name: str | None = None


@dataclass
class StorageDetailResponse:
    server_id: int
    public_id: str
    hostname: str
    disks: list[DiskItem]
    mounts: list[MountUsageItem]
    snapshot_at: datetime | None
    inventory_at: datetime | None


# ---------- 메트릭 대시보드 스냅샷 (AJAX /metrics/latest) ----------

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
    # stacked bar 표시용 비율 (P5: 클라이언트가 다시 계산하지 않음). metrics_calculator에서 산출.
    cached_pct: float | None = None
    buffers_pct: float | None = None


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
    disk_io_phys: list[DiskIoSnapshot]
    disk_io_lvm: list[DiskIoSnapshot]
    disk_io_part: list[DiskIoSnapshot]
    net_io: list[NetIoSnapshot]
    mounts: list[MountDashSnapshot]


# ---------- 네트워크 (IPs + 실시간 인터페이스 현황) ----------
# NetIoSnapshot을 재사용 — 필드 구조가 동일하므로 별도 타입 불필요

@dataclass
class NetworkDetailResponse:
    server_id: int
    public_id: str
    hostname: str
    ip_internal: list[str]
    ip_external: list[str] | None
    interfaces: list[NetIoSnapshot]
    inventory_at: datetime | None
    snapshot_at: datetime | None


# ---------- 수집 상태 ----------

@dataclass
class CollectionStatusItem:
    last_metric_at: datetime | None
    last_inventory_at: datetime | None
    is_online: bool


# ---------- 시계열 ----------

@dataclass
class MetricSeriesItem:
    collected_at: datetime
    value: float | None
    dimension: str | None