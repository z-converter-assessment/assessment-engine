from dataclasses import dataclass
from datetime import datetime
from typing import Literal


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
    # counter reset 정밀 식별 (calculator가 prev↔cur 비교).
    boot_time: datetime | None = None
    agent_started_at: datetime | None = None


@dataclass
class DiskIoRaw:
    device: str
    collected_at: datetime
    reads_completed: int
    writes_completed: int
    sectors_read: int
    sectors_written: int
    boot_time: datetime | None = None
    agent_started_at: datetime | None = None


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
    boot_time: datetime | None = None
    agent_started_at: datetime | None = None


@dataclass
class MountUsageRaw:
    mount: str
    total_bytes: int | None
    avail_bytes: int | None
    free_bytes: int | None
    collected_at: datetime | None
    # 시계열 4개 테이블 메타데이터 일관성 — calculator는 시점값이라 활용 안 하지만 보존.
    boot_time: datetime | None = None
    agent_started_at: datetime | None = None


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


# ---------- Reboot / Agent restart 이벤트 (차트 vertical marker용) ----------

@dataclass
class ReportRow:
    """Assessment 보고서 한 행 — 서버 1대의 USE Method 통계 + recommendation.

    1차 MVP: CPU p95/peak + MEM p95/peak + swap_used + load_15m max.
    추후 확장: net_avg_kbps (idle/shutdown 활성), iowait_p95 (Saturation 차원).
    근거: docs/assessment-deliverables.md "RISK 분류" + docs/ai_roadmap.md §3.B·C.
    """
    server_id: int
    public_id: str
    hostname: str
    role: str                   # service_classifier 추론
    is_online: bool
    os_display: str
    kernel_version: str | None
    internal_ip: str | None

    # USE Method Utilization (p95)
    cpu_p95_pct: float | None
    cpu_peak_pct: float | None
    mem_p95_pct: float | None
    mem_peak_pct: float | None

    # USE Method Saturation
    load_15m_max: float | None
    swap_used: bool

    # 분류 결과 (recommendation.classify 결과)
    recommendation: str         # idle | shutdown | over_provisioned | under_provisioned | optimal | insufficient_data
    recommendation_label: str   # 한국어 라벨


@dataclass
class InventoryExportEntry:
    """정제 inventory JSON 항목 — 자동화 도구(OpenStack/Terraform/SDK) 입력용 표준 스키마.

    벤더 중립 — 특정 vendor flavor 식별자 없음. v1: VM 생성 최소 정보(vcpus·memory·disk·network).
    상세 정의: docs/assessment-deliverables.md "정제 Inventory JSON" 절.
    """
    name: str
    machine_id: str
    role: str
    os: dict           # {"family", "version", "kernel"}
    compute: dict      # {"vcpus", "memory_mb"}
    storage: dict      # {"boot_disk_gb", "additional_disks":[{"mount_hint","size_gb","fstype"}]}
    network: dict      # {"hostname", "internal_ip", "external_ip"}


@dataclass
class RebootEvent:
    """server_inventory_history에서 boot_time / agent_started_at 변경 시점 추출.

    kind 분류:
    - "reboot": boot_time 변경 (시스템 재부팅) 또는 첫 등록 (이전 행 없음)
    - "restart": boot_time 동일 + agent_started_at 변경 (에이전트 단독 재시작)
    """
    collected_at: datetime
    boot_time: datetime | None
    agent_started_at: datetime | None
    kind: Literal["reboot", "restart"]