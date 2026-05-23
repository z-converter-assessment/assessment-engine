from dataclasses import dataclass
from datetime import datetime


@dataclass
class ServerInventoryCreate:
    # ─── 공통 메타데이터 (모든 메시지 공통, MessageBase 대응) ─────────────────────
    # message_id는 consumer에서 멱등성 체크에만 사용되고 DTO에는 안 옴.
    host_id: str
    hostname: str
    agent_version: str
    collected_at: datetime
    boot_time: datetime | None
    agent_started_at: datetime | None

    # ─── inventory 본문 (정적 인프라 정보) ─────────────────────────────────────
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
    disks: list[dict]  # JSONB 컬럼 — [{name, size_bytes, type, major, minor}]
    mounts: list[dict]  # JSONB 컬럼 — [{mount, fstype, total_bytes, major, minor}]
    services: list[dict] | None  # JSONB 컬럼 — [{unit, sub}] | None (non-systemd host)
    listen_ports: list[dict]  # JSONB 컬럼 — [{proto, addr, port, uid, pid, comm}]


# ─── metrics 시계열 행 단위 DTO ─────────────────────────────────────────────
# inventory의 list[dict]는 JSONB 컬럼 직렬화용이라 dict 유지가 자연스러우나,
# metrics는 4개 시계열 테이블의 한 행에 매핑되므로 컴파일 타임 타입 보장이 정확성에 유리.


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


# ─── Task DTO ──────────────────────────────────────────────────────────────


@dataclass
class TaskCreate:
    """task 등록 시 — web router → repository."""

    target_server_id: int
    target_host_id: str
    task_type: str
    params: dict | None


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
    # ─── 공통 메타데이터 ────────────────────────────────────────────────────
    # host_id는 consumer 단에서 server_id 해석에 사용. 본 DTO엔 안 담음.
    # boot_time/agent_started_at은 시계열 행마다 함께 저장 — calculator가 두 시점
    # 비교로 counter reset(시스템 재부팅) 정밀 식별 (CLAUDE.md B1 정책).
    collected_at: datetime
    boot_time: datetime | None
    agent_started_at: datetime | None

    # ─── /proc/stat CPU jiffies (raw 누적값) ─────────────────────────────────
    cpu_user: int | None
    cpu_nice: int | None
    cpu_system: int | None
    cpu_idle: int | None
    cpu_iowait: int | None
    cpu_irq: int | None
    cpu_softirq: int | None
    cpu_steal: int | None

    # ─── 메모리·스왑 (kB, /proc/meminfo) ─────────────────────────────────────
    mem_total_kb: int | None
    mem_free_kb: int | None
    mem_available_kb: int | None
    mem_buffers_kb: int | None
    mem_cached_kb: int | None
    swap_total_kb: int | None
    swap_free_kb: int | None

    # ─── load average (/proc/loadavg) ────────────────────────────────────────
    load_1m: float | None
    load_5m: float | None
    load_15m: float | None

    # ─── 시계열 4개 테이블 nested 행 매핑 ────────────────────────────────────
    disk_io: list[DiskIoEntry]
    mounts: list[MountUsageEntry]
    net_io: list[NetIoEntry]


# --- 진단 job INSERT 입력 (ADR 0004) ---


@dataclass
class DiagnosticJobCreate:
    """진단 job INSERT 입력 — id·created_at·status는 DB default가 채움.

    job_type:
    - 'ai_diagnostic' (default): 규칙 기반·LLM narrative 진단 (ADR 0004 + 0010)
    - 'customer_report' / 'engineer_report': 보고서 발행 이력 (즉시 succeeded)
    scope: 'server' | 'environment'
    input_hash: sha256(scope + canonical(input_params) + job_type) — 캐시·active UNIQUE 키
    """

    scope: str
    input_params: dict
    input_hash: str
    job_type: str = "ai_diagnostic"
    requested_by: str | None = None
