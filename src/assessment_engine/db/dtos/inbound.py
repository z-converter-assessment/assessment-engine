from dataclasses import dataclass
from datetime import datetime


@dataclass
class ServerInventoryCreate:
    # message_id는 consumer 멱등성 체크 전용 — DTO 미포함.
    composite_id: str
    machine_id: str | None  # raw machine-id, 표시 전용 (식별은 composite_id)
    hostname: str
    agent_version: str
    collected_at: datetime
    boot_time: datetime | None
    agent_started_at: datetime | None

    os_family: str | None  # "linux" | "windows" — task.install dispatch 단일 진실 (ADR 0020)
    os_id: str | None
    os_version: str | None
    os_codename: str | None
    kernel_version: str | None
    cpu_cores: int | None
    cpu_model: str | None
    mem_total_kb: int | None
    swap_total_kb: int | None
    ip_internal: list[str]
    ip_external: list[str] | None
    mac_addresses: list[str]  # NIC MAC 목록 (clone collision 감사용, 식별 미사용)
    disks: list[dict]  # JSONB — [{name, size_bytes, type, major, minor}]
    mounts: list[dict]  # JSONB — [{mount, fstype, total_bytes, major, minor}]
    services: list[dict] | None  # JSONB — [{unit, sub}] | None (non-systemd host)
    listen_ports: list[dict]  # JSONB — [{proto, addr, port, uid, pid, comm}]
    # 서비스 카테고리 집합 (ingest 사전계산, service_classifier.compute_service_categories). read 경로 뱃지 단일 진실.
    service_categories: list[str]


# metrics 는 inventory(JSONB dict) 와 달리 4개 시계열 테이블 행에 매핑 — dataclass 타입 보장.


@dataclass
class DiskIoEntry:
    device: str
    reads_completed: int | None
    writes_completed: int | None
    sectors_read: int | None
    sectors_written: int | None


@dataclass
class NetIoEntry:
    interface: str
    rx_bytes: int | None
    tx_bytes: int | None
    rx_packets: int | None
    tx_packets: int | None
    rx_errors: int | None
    tx_errors: int | None


@dataclass
class MountUsageEntry:
    mount: str
    total_bytes: int | None
    free_bytes: int | None
    avail_bytes: int | None
    major: int | None = None
    minor: int | None = None


# ─── Task DTO ──────────────────────────────────────────────────────────────


@dataclass
class TaskCreate:
    """task 등록 시 — web router → repository."""

    target_server_id: int
    target_composite_id: str
    task_type: str
    params: dict | None
    deadline_at: datetime | None = None  # 응답 마감 (install 발행 시 세팅, 그 외 None)


@dataclass
class TaskResultUpdate:
    """task 결과 보고 — MQ → consumer → repository.

    public_id는 결과 보고 메시지의 task_id 값을 그대로 받아 Task.public_id 매칭.
    """

    public_id: str
    status: str  # "success" | "failure"
    failure_reason: str | None
    exit_code: int | None
    duration_ms: int
    stdout_tail: str
    stderr_tail: str
    completed_at: datetime


@dataclass
class ServerMetricCreate:
    # composite_id는 consumer 가 server_id 해석에 사용 — 본 DTO 미포함.
    # boot_time/agent_started_at: 시계열 행마다 저장, counter reset 정밀 식별 (#C1·#B).
    collected_at: datetime
    boot_time: datetime | None
    agent_started_at: datetime | None

    # /proc/stat CPU jiffies (raw 누적값)
    cpu_user: int | None
    cpu_nice: int | None
    cpu_system: int | None
    cpu_idle: int | None
    cpu_iowait: int | None
    cpu_irq: int | None
    cpu_softirq: int | None
    cpu_steal: int | None

    # 메모리·스왑 (kB, /proc/meminfo)
    mem_total_kb: int | None
    mem_free_kb: int | None
    mem_available_kb: int | None
    mem_buffers_kb: int | None
    mem_cached_kb: int | None
    swap_total_kb: int | None
    swap_free_kb: int | None

    # load average (/proc/loadavg)
    load_1m: float | None
    load_5m: float | None
    load_15m: float | None

    # 시계열 4개 테이블 nested 행 매핑
    disk_io: list[DiskIoEntry]
    mounts: list[MountUsageEntry]
    net_io: list[NetIoEntry]


# --- 보고서 발행 job INSERT 입력 ---


@dataclass
class DiagnosticJobCreate:
    """보고서 발행 job INSERT 입력 — id·created_at·status는 DB default가 채움.

    job_type: 'customer_report' / 'engineer_report' — 보고서 발행 이력 (비동기 생성, ADR 0040)
    scope: 'server' | 'environment'
    input_hash: sha256(scope + canonical(input_params) + job_type) — 캐시·active UNIQUE 키
    """

    scope: str
    input_params: dict
    input_hash: str
    job_type: str = "customer_report"
    requested_by: str | None = None
