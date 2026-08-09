"""Repository -> Service 로 나가는 raw dataclass (#C2 · P1).

단위는 수집 원본 그대로다 — 시간 s, 크기 By, CPU jiffies. 변환·분류는 service 소관이라 여기서는
값을 옮겨 담기만 한다. 전부 frozen+slots — 근거는 `docs/reference/db/dtos.md`.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from assessment_engine.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class ServerSummary:
    id: int
    public_id: str
    composite_id: str | None
    hostname: str
    os_id: str | None
    os_version: str | None
    kernel_version: str | None  # 레거시 Windows Server 표시명 보강 (build -> 버전, os_version 빈값 세대)
    product_name: str | None  # Windows CurrentVersion ProductName 원문 — os_display 라벨 파싱 소스
    cpu_cores: int | None
    mem_total_bytes: int | None
    ip_external: list[str] | None
    block_devices: list[JsonObject]
    service_categories: list[str]
    last_seen_at: datetime | None  # Redis online TTL fallback 용도


@dataclass(frozen=True, slots=True)
class ServerDetail:
    id: int
    public_id: str
    agent_id: str
    composite_id: str | None
    machine_id: str | None
    hostname: str
    agent_version: str | None
    os_family: str | None  # "linux" | "windows" — task.install dispatch 단일 진실
    os_id: str | None
    os_version: str | None
    os_codename: str | None
    kernel_version: str | None
    cpu_cores: int | None
    cpu_model: str | None
    cpu_arch: str | None
    cpu_bits: int | None
    mem_total_bytes: int | None
    boot_time: datetime | None
    agent_started_at: datetime | None
    net_interfaces: list[JsonObject]
    ip_external: list[str] | None
    block_devices: list[JsonObject]
    lvm_vgs: list[JsonObject]
    services: list[JsonObject] | None
    listen_ports: list[JsonObject]
    last_seen_at: datetime | None
    service_categories: list[str] | None = None
    product_name: str | None = None  # Windows only — os_display 라벨 파싱 소스
    edition: str | None = None  # Windows EditionID(SKU)


@dataclass(frozen=True, slots=True)
class CollectionStatus:
    last_metric_at: datetime | None
    last_inventory_at: datetime | None


@dataclass(frozen=True, slots=True)
class TaskRow:
    """Task row raw — 표시 파생(badge_class·duration_label)은 mapper."""

    public_id: str
    target_server_id: int
    target_public_id: str | None
    target_hostname: str | None
    task_type: str
    status: str
    created_at: datetime
    completed_at: datetime | None
    failure_reason: str | None
    exit_code: int | None
    signal_no: int | None
    duration_ms: int | None
    stdout_tail: str | None
    stderr_tail: str | None
    params: JsonObject | None = None

    deadline_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MetricPairRaw:
    collected_at: datetime

    cpu_user_s: float | None
    cpu_nice_s: float | None
    cpu_system_s: float | None
    cpu_idle_s: float | None
    cpu_iowait_s: float | None
    cpu_irq_s: float | None
    cpu_softirq_s: float | None
    cpu_steal_s: float | None

    mem_limit_bytes: int | None
    mem_free_bytes: int | None
    mem_available_bytes: int | None
    mem_buffered_bytes: int | None
    mem_cached_bytes: int | None
    mem_used_bytes: int | None
    # 실행 큐 gauge — Linux procs_running / Windows Processor Queue. 스냅샷 os-aware 표시.
    cpu_run_queue: float | None = None
    cpu_logical_count: int | None = None
    cpu_blocked: float | None = None

    boot_time: datetime | None = None
    agent_started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CpuCoreRaw:
    """per-core CPU 시간 원자료 (server_cpu_core, Linux 전용 — Windows 미발행). 단일스레드 병목 표시용."""

    core_id: int
    collected_at: datetime
    cpu_user_s: float | None = None
    cpu_nice_s: float | None = None
    cpu_system_s: float | None = None
    cpu_idle_s: float | None = None
    cpu_iowait_s: float | None = None
    cpu_irq_s: float | None = None
    cpu_softirq_s: float | None = None
    cpu_steal_s: float | None = None


@dataclass(frozen=True, slots=True)
class DiskIoRaw:
    device_id: str
    collected_at: datetime
    io_read_bytes: int | None = None
    io_write_bytes: int | None = None
    ops_read: int | None = None
    ops_write: int | None = None
    op_read_time_s: float | None = None
    op_write_time_s: float | None = None
    io_time_s: float | None = None
    pending_ops: float | None = None
    device_name: str | None = None
    boot_time: datetime | None = None
    agent_started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NetIoRaw:
    iface_id: str
    collected_at: datetime
    rx_bytes: int | None = None
    tx_bytes: int | None = None
    rx_packets: int | None = None
    tx_packets: int | None = None
    rx_errors: int | None = None
    tx_errors: int | None = None
    rx_dropped: int | None = None
    tx_dropped: int | None = None
    link_speed_bps: int | None = None
    iface_name: str | None = None
    boot_time: datetime | None = None
    agent_started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MountUsageRaw:
    mountpoint: str
    used_bytes: int | None = None
    free_bytes: int | None = None
    inodes_used: int | None = None
    inodes_free: int | None = None
    device_id: str | None = None  # block_devices 조인
    fstype: str | None = None
    collected_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DashboardRaw:
    metrics: list[MetricPairRaw]
    disk_io: list[DiskIoRaw]
    net_io: list[NetIoRaw]
    filesystems: list[MountUsageRaw]
    os_family: str | None = None  # os-aware 스냅샷 포화 판정 입력 (linux|windows|null)
    kernel_version: str | None = None  # PSI 지원(Linux 4.20+) 판정 입력 — 구커널 N/A 분기용

    block_devices: list[JsonObject] | None = None
    net_interfaces: list[JsonObject] | None = None
    cpu_cores: list[CpuCoreRaw] = field(default_factory=list[CpuCoreRaw])


@dataclass(frozen=True, slots=True)
class StorageWithUsage:
    server_id: int
    public_id: str
    hostname: str
    block_devices: list[JsonObject]
    lvm_vgs: list[JsonObject]
    filesystems: list[MountUsageRaw]
    inventory_at: datetime | None
    os_family: str | None = None  # OS 분기 표시 (Windows PSI N/A 등)


@dataclass(frozen=True, slots=True)
class NetworkWithIo:
    server_id: int
    public_id: str
    hostname: str
    net_interfaces: list[JsonObject]
    ip_external: list[str] | None
    net_io: list[NetIoRaw]
    inventory_at: datetime | None
    os_family: str | None = None  # OS 분기 표시 (Windows conntrack N/A 등)
    # iface_id -> 최신 link_speed_bps(bit/s) — 인벤토리 speed_mbps null(virtio·Windows NT5.2) 폴백용.
    link_speed_by_iface: dict[str, int] = field(default_factory=dict[str, int])


@dataclass(frozen=True, slots=True)
class EnvironmentUtilizationRaw:
    """환경(또는 선택 N대) capacity-weighted 평균 활용률 (sum(used) / sum(total)).

    윈도우 안 전 서버·전 시점 통합 비율 — CPU는 시간 delta 합, MEM/DISK는 total 가중(가상 mount 제외).
    거대 VM이 큰 비중 = 물리 자원 관점(서버 동등 가중 아님). 산식 단일 진실 = repo get_environment_utilization.
    """

    cpu_avg_pct: float | None
    mem_avg_pct: float | None
    disk_avg_pct: float | None
    sample_size: int
    cpu_p95_pct: float | None = None
    mem_p95_pct: float | None = None


@dataclass(frozen=True, slots=True)
class DiskIoBaselineRaw:
    """get_report_disk_io_baseline — 서버별 디스크 I/O baseline + p95/peak. baseline=SUM(delta)/SUM(dt)."""

    iops_baseline: int | None
    throughput_kbps_baseline: float | None
    iops_p95: float | None
    iops_peak: float | None
    kbps_p95: float | None
    kbps_peak: float | None


@dataclass(frozen=True, slots=True)
class NetIoBaselineRaw:
    """get_report_net_io_baseline — 서버별 네트워크 I/O baseline + p95/peak."""

    rx_kbps_baseline: float | None
    tx_kbps_baseline: float | None
    rx_p95: float | None
    rx_peak: float | None
    tx_p95: float | None
    tx_peak: float | None


@dataclass(frozen=True, slots=True)
class SaturationRaw:
    """get_latest_saturation — 신선 표본 실시간 포화 원자료 (os-aware).

    미존재 server 는 빈 인스턴스(전 필드 None) 사용.
    """

    run_queue: float | None = None  # CPU 실행 큐 (Linux procs_running / Windows Processor Queue)
    await_ms: float | None = None
    pending_ops: float | None = None

    disk_io_util_pct: float | None = None
    # 하드폴트 rate — os-aware 소스(Linux refault=paging_major / Windows Pages Input=paging_in). Windows 는

    paging_major_rate: float | None = None
    retrans_pct: float | None = None
    drop_pct: float | None = None
    conntrack_ratio: float | None = None
    # PSI (Pressure Stall Info, Linux 4.20+ 전용 — Windows None) %정체: 자원 대기로 태스크가 멈춘 시간 비율

    psi_cpu: float | None = None
    psi_mem: float | None = None
    psi_io: float | None = None


@dataclass(frozen=True, slots=True)
class ErrorFleetRaw:
    """get_latest_errors — 창내 하드웨어/디스크/네트워크 에러 카운트 (Errors 축, 정상 0). counter delta(max-min).

    measured=False(창 안 표본 없음)면 카운트 지표 전부 no_data. corrupted_bytes 는 gauge(현재값>0=존재).
    """

    measured: bool = False
    net_measured: bool = False
    disk_err_measured: bool = False
    mce_count: int = 0
    oom_count: int = 0
    corrupted_bytes: int | None = None
    net_error_count: int = 0
    disk_error_count: int = 0
    disk_error_kinds: list[str] = field(default_factory=list[str])
    last_error_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FleetErrorRaw:
    """get_fleet_error_summary — 전 서버 에러축 영향 호스트 수 (환경 개요 fleet 에러 표시자). 창내 발생 호스트 count."""

    total: int = 0
    mce_hosts: int = 0
    oom_hosts: int = 0
    corrupted_hosts: int = 0
    net_error_hosts: int = 0
    disk_error_hosts: int = 0


@dataclass(frozen=True, slots=True)
class MountCapacityRaw:
    """마운트별 용량 사이징 raw (per-mount) — /api/assessment 디스크 축 입력.

    runway/target 은 가용 이력 전체 span 산출(get_report_aggregate mount_calc 와 동일 산식).
    target_bytes = 소진 임박 시 목표 총 용량(By). None=안 참(유지).
    """

    mountpoint: str
    total_bytes: int | None
    used_pct: float | None
    byte_runway_days: float | None
    inode_runway_days: float | None
    inode_used_pct: float | None
    target_bytes: int | None


@dataclass(frozen=True, slots=True)
class MemoryBreakdownRaw:
    """메모리 구성 윈도우 평균 — used/available/cached/buffers (전체 메모리 대비 %, 시점값 avg)."""

    used_pct: float | None
    available_pct: float | None
    cached_pct: float | None
    buffers_pct: float | None


@dataclass(frozen=True, slots=True)
class CpuBreakdownRaw:
    """CPU 분류 윈도우 평균 — user/system/iowait (시간 delta 기반 %, reset 정책 get_metric_trend 동일)."""

    user_pct: float | None
    system_pct: float | None
    iowait_pct: float | None


@dataclass(frozen=True, slots=True)
class MetricSeries:
    collected_at: datetime
    value: float | Decimal | None  # SQL avg·sum 은 numeric 을 Decimal 로 준다
    dimension: str | None
    kind: str | None = None


@dataclass(frozen=True, slots=True)
class ReportRowRaw:
    """Assessment 보고서 한 행의 raw stats — USE Method(Utilization/Saturation/Errors) 신호 묶음."""

    server_id: int
    public_id: str
    hostname: str
    os_family: str | None  # "linux"|"windows" — os-aware 신호 분기
    os_id: str | None
    os_version: str | None
    os_codename: str | None
    kernel_version: str | None
    net_interfaces: list[JsonObject] | None
    services: list[JsonObject] | None
    last_seen_at: datetime | None

    cpu_p95_pct: float | None
    cpu_avg_pct: float | None
    cpu_peak_pct: float | None
    mem_p95_pct: float | None
    mem_avg_pct: float | None
    mem_peak_pct: float | None
    mem_near_peak_pct: float | None = None

    listen_ports: list[JsonObject] | None = None

    # I/O wait (cpu iowait 시간 / total non-idle 비율) — Linux 디스크 병목 참고 신호
    iowait_p95_pct: float | None = None
    iowait_peak_pct: float | None = None
    cpu_run_queue_p95: float | None = None  # Windows Processor Queue Length p95 (Linux run queue 등가 축)
    mem_pages_input_rate_p95: float | None = None  # Windows Pages Input/sec rate p95 (Linux paging_major 등가 축)

    cpu_cores: int | None = None
    mem_total_bytes: int | None = None
    block_devices: list[JsonObject] | None = None
    lvm_vgs: list[JsonObject] | None = None

    arch: str | None = None
    bits: int | None = None
    boot_firmware: str | None = None
    secure_boot: bool | None = None
    edition: str | None = None
    product_name: str | None = None  # Windows only — os_display 라벨 파싱 소스
    timezone: str | None = None
    rtc_utc: bool | None = None
    boot: JsonObject | None = None
    nonblock_mounts: list[JsonObject] | None = None
    boot_time: datetime | None = None

    worst_mount_used_pct: float | None = None

    disk_capacity_worst_mount: str | None = None

    reboot_count: int = 0  # period 내 재부팅 — 별도 SQL(`get_report_uptime_stats`)에서 채움
    agent_restart_count: int = 0  # period 내 에이전트 재시작 — 별도 SQL, anchor+window 정합 (#F10)

    disk_iops_baseline: int | None = None
    disk_iops_p95: float | None = None
    disk_iops_peak: float | None = None
    disk_throughput_kbps: float | None = None
    disk_throughput_kbps_p95: float | None = None
    disk_throughput_kbps_peak: float | None = None

    net_rx_kbps: float | None = None
    net_rx_kbps_p95: float | None = None
    net_rx_kbps_peak: float | None = None
    net_tx_kbps: float | None = None
    net_tx_kbps_p95: float | None = None
    net_tx_kbps_peak: float | None = None

    cpu_sufficiency: float | None = None
    mem_sufficiency: float | None = None

    cpu_steal_p95_pct: float | None = None
    cpu_burst_ratio: float | None = None
    procs_blocked_p95: float | None = None
    procs_running_p95: float | None = None  # R-state 실행 큐 p95 (Linux CPU 포화)
    mem_swap_paging: bool | None = None  # paging_major(refault) rate sustained (Linux 메모리 포화 dual-gate 입력)
    oom_occurred: bool = False
    history_hours: float | None = None
    disk_await_p95_ms: float | None = None
    disk_capacity_runway_days: float | None = None
    disk_capacity_driving_mount: str | None = None
    disk_inode_runway_days: float | None = None
    disk_inode_driving_mount: str | None = None
    disk_inode_used_pct: float | None = None
    disk_capacity_target_gb: float | None = None
    disk_capacity_proj_30d_pct: float | None = None
    disk_capacity_driving_used_pct: float | None = None

    net_drop_pct: float | None = None
    net_retrans_pct: float | None = None
    conntrack_ratio: float | None = None
    cpu_trend_slope: float | None = None
    mem_trend_slope: float | None = None
    cpu_percore_p95_max: float | None = None


@dataclass(frozen=True, slots=True)
class RebootEvent:
    """server_inventory_history 에서 추출한 재부팅/에이전트 재시작 시점 (차트 vertical marker).

    kind — "reboot": boot_time 변경 또는 첫 등록(이전 행 없음) / "restart": boot_time 동일 + agent_started_at 변경.
    """

    collected_at: datetime
    boot_time: datetime | None
    agent_started_at: datetime | None
    kind: Literal["reboot", "restart"]


@dataclass(frozen=True, slots=True)
class MetricGapWarningRaw:
    """metric 발행 갭 — last_metric_at 이 임계 초과로 끊긴 호스트 (통신 끊김 운영신호)."""

    public_id: str
    hostname: str
    last_metric_at: datetime


@dataclass(frozen=True, slots=True)
class DiagnosticJobRecord:
    """보고서 발행 job 단건 — 라우터 조회 응답·발행 이력 표현.

    id 는 UUID 문자열 (PG_UUID as_uuid=False). job_type 은 'customer_report' | 'engineer_report'.
    result·error_message 는 status 따라 둘 중 하나만 채운다.
    """

    id: str
    job_type: str
    scope: str
    input_params: JsonObject
    input_hash: str
    status: str
    progress_stage: str | None
    result: JsonObject | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    requested_by: str | None
