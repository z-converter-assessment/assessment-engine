from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass
class ServerSummary:
    id: int
    public_id: str
    host_id: str
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
    host_id: str
    hostname: str
    agent_version: str | None
    os_family: str | None  # "linux" | "windows" — task.install dispatch 단일 진실 (ADR 0020)
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


@dataclass
class TaskRow:
    """Task row raw — query repo 가 채워 service 가 받는 형식. 표시 파생(badge_class·duration_label)은 mapper."""

    public_id: str
    target_server_id: int
    target_public_id: str | None  # JOIN server_inventory.public_id (목록 응답 시각·운영자용)
    target_hostname: str | None  # JOIN server_inventory.hostname
    task_type: str
    status: str  # "pending" / "success" / "failure" (legacy "failed" 가능)
    created_at: datetime
    completed_at: datetime | None
    failure_reason: str | None
    exit_code: int | None
    duration_ms: int | None
    stdout_tail: str | None
    stderr_tail: str | None
    params: dict | None = None  # install task 의 {zdm_ip, zdm_user} 등 발행 파라미터


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
    # counter reset 정밀 식별 (calculator가 prev-cur 비교).
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
    metrics: list[MetricPairRaw]  # 최대 2행, collected_at desc
    disk_io: list[DiskIoRaw]  # 디바이스당 최대 2행, desc within device
    net_io: list[NetIoRaw]  # 인터페이스당 최대 2행, desc within interface
    mounts: list[MountUsageRaw]  # 마운트당 최신 1행


# ---------- Storage / Network 풍부화 DTOs ----------


@dataclass
class StorageWithUsage:
    server_id: int
    public_id: str
    hostname: str
    disks: list[dict]  # 인벤토리 JSONB: {name, size_bytes, type}
    inventory_mounts: list[dict]  # 인벤토리 JSONB: {mount, fstype, total_bytes}
    mount_usage: list[MountUsageRaw]
    inventory_at: datetime | None


@dataclass
class NetworkWithIo:
    server_id: int
    public_id: str
    hostname: str
    ip_internal: list[str]
    ip_external: list[str] | None
    net_io: list[NetIoRaw]  # 인터페이스당 최대 2행 (delta 계산용)
    inventory_at: datetime | None


@dataclass
class EnvironmentUtilizationRaw:
    """환경 전체 latest 메트릭 평균 — list 화면 진행 막대용.

    CPU는 두 시점 delta (ROW_NUMBER + self-join). MEM은 latest 1행. DISK는 서버별 max mount.
    1시간 안 메트릭 없는 서버 자동 제외 (offline 필터).
    """

    cpu_avg_pct: float | None
    mem_avg_pct: float | None
    disk_avg_pct: float | None
    sample_size: int  # 어느 metric이든 데이터 들어온 서버 수 — UI에 표본 표시 (예: "12대 기준")


# ---------- Series ----------


@dataclass
class MetricSeries:
    collected_at: datetime
    value: float | None
    dimension: str | None


# ---------- Reboot / Agent restart 이벤트 (차트 vertical marker용) ----------


@dataclass
class ReportRowRaw:
    """Assessment 보고서 한 행의 raw stats — repository가 반환 (P1).

    표시 파생(role/is_online/recommendation/os_display 등)은 service mapper에서 ReportRowItem으로.
    USE Method (Utilization/Saturation/Errors) 기반 — Utilization p95/peak + Saturation(load/swap).
    """

    server_id: int
    public_id: str
    hostname: str
    os_id: str | None
    os_version: str | None
    kernel_version: str | None
    ip_internal: list[str] | None
    services: list[dict] | None  # service_classifier 입력 (role 추론용)
    last_seen_at: datetime | None

    # USE Method Utilization (p95 + avg + peak)
    cpu_p95_pct: float | None
    cpu_avg_pct: float | None
    cpu_peak_pct: float | None
    mem_p95_pct: float | None
    mem_avg_pct: float | None
    mem_peak_pct: float | None

    # USE Method Saturation
    load_15m_max: float | None
    swap_used: bool

    # I/O wait (cpu_stat.iowait jiffies / total non-idle 비율) — 디스크 병목 신호
    iowait_p95_pct: float | None = None
    iowait_peak_pct: float | None = None

    # Inventory 합계 산정용 — query_service.get_report가 totals 계산 시 사용
    cpu_cores: int | None = None
    mem_total_kb: int | None = None
    disks: list[dict] | None = None  # 합계 산정 위해 size_bytes 합산
    boot_time: datetime | None = None  # uptime_days = now - boot_time

    # Mount worst — 별도 SQL(`report_mount_worst`)에서 채움. mapper는 그 결과를 zip
    worst_mount: str | None = None
    worst_mount_used_pct: float | None = None
    worst_mount_days_until_full: int | None = None

    # Uptime + period 내 재부팅 횟수 — 별도 SQL(`report_uptime_stats`)에서 채움
    reboot_count: int = 0

    # Disk I/O — baseline(평균) + p95 + peak (모든 device 시점별 합산 후 통계)
    disk_iops_baseline: int | None = None
    disk_iops_p95: float | None = None
    disk_iops_peak: float | None = None
    disk_throughput_kbps: float | None = None
    disk_throughput_kbps_p95: float | None = None
    disk_throughput_kbps_peak: float | None = None

    # Net I/O — baseline(평균) + p95 + peak (모든 interface 시점별 합산 후 통계)
    net_rx_kbps: float | None = None
    net_rx_kbps_p95: float | None = None
    net_rx_kbps_peak: float | None = None
    net_tx_kbps: float | None = None
    net_tx_kbps_p95: float | None = None
    net_tx_kbps_peak: float | None = None


@dataclass
class InventoryExportEntry:
    """정제 inventory JSON 항목 — 자동화 도구(Terraform/OpenStack/Ansible/CSP SDK) 입력 표준.

    스키마·정제 원칙·사용처: docs/architecture/inventory-export.md (v3).
    벤더 중립 — recommended_size_class만 노출, 도구가 자기 도메인 instance type에 매핑.
    """

    host_id: str
    hostname: str
    role: str
    last_seen_at: datetime | None
    services: list[dict]  # [{"category": str, "unit": str, "ports": [int]}]
    os: dict  # {"family", "version", "kernel"}
    compute: dict  # {"vcpu_count", "memory_mb", "cpu_p95_pct", "cpu_peak_pct",
    #  "mem_p95_pct", "mem_peak_pct", "load_15m_max", "swap_used",
    #  "recommended_size_class"}
    storage: dict  # {"boot_disk_gb", "additional_disks":[{"mount_point","size_gb","fstype"}]}
    network: dict  # {"addresses": [{"scope","family","address"}]}


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


# ---------- 주의 신호 (목록 화면 상단 — risk_top 보완) ----------


@dataclass
class DiskUsageWarningRaw:
    """특정 mount의 사용률 임계 초과 — repository raw (P1: 단위 변환·badge 분류 X).

    last_metric_at: 해당 mount의 latest 시점 — 운영자가 stale 여부 판단.
    """

    public_id: str
    hostname: str
    mount: str
    total_bytes: int
    avail_bytes: int
    last_metric_at: datetime


@dataclass
class MetricGapWarningRaw:
    """metric 발행 갭 — last_metric_at이 임계 초과로 끊김. raw (P1)."""

    public_id: str
    hostname: str
    last_metric_at: datetime


# --- 진단 job 조회 결과 (ADR 0004) ---


@dataclass
class DiagnosticJobRecord:
    """진단 job 단건 — 라우터 polling 응답·워커 작업 단위 표현.

    id는 UUID 문자열 (PG_UUID as_uuid=False). result·error_message는 status 따라 둘 중 하나만 채움.
    job_type: 'ai_diagnostic' | 'customer_report' | 'engineer_report'
    """

    id: str
    job_type: str
    scope: str
    input_params: dict
    input_hash: str
    status: str
    progress_stage: str | None
    result: dict | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    requested_by: str | None
