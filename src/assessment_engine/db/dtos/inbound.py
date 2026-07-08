from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ServerInventoryCreate:
    # message_id는 consumer 멱등성 체크 전용 — DTO 미포함.
    agent_id: str  # 식별 단일 키 (UUID str) — DB agent_id UNIQUE·MQ 라우팅
    composite_id: str | None  # SHA-256(machine_id+MAC). 감사·표시용 강등 (식별 미사용)
    machine_id: str | None  # raw machine-id, 표시 전용
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
    interfaces: list[dict]  # JSONB — [{name, address, prefix, family, kind, gateway}]
    ip_external: list[str] | None
    mac_addresses: list[str]  # NIC MAC 목록 (clone collision 감사용, 식별 미사용)
    disks: list[dict]  # JSONB — [{name, size_bytes, type, major, minor, kind}]
    mounts: list[dict]  # JSONB — [{mount, fstype, total_bytes, major, minor, kind}]
    services: list[dict] | None  # JSONB — [{unit, sub, pid, exe}] | None (non-systemd host)
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
    kind: str | None = None
    # ADR 0052 await 원자료(ms 누적) + 참고
    time_reading_ms: int | None = None
    time_writing_ms: int | None = None
    io_ticks_ms: int | None = None
    weighted_io_ms: int | None = None


@dataclass
class NetIoEntry:
    interface: str
    rx_bytes: int | None
    tx_bytes: int | None
    rx_packets: int | None
    tx_packets: int | None
    rx_errors: int | None
    tx_errors: int | None
    kind: str | None = None
    # ADR 0052 드롭 (품질)
    rx_drops: int | None = None
    tx_drops: int | None = None


@dataclass
class MountUsageEntry:
    mount: str
    total_bytes: int | None
    free_bytes: int | None
    avail_bytes: int | None
    kind: str | None = None
    # ADR 0052 inode
    inodes_total: int | None = None
    inodes_free: int | None = None


@dataclass
class CpuCoreEntry:
    # per-core jiffies — core_id = cpu_per_core[] 위치 인덱스(/proc/stat cpu0..N). server_cpu_core 저장.
    core_id: int
    cpu_user: int | None
    cpu_nice: int | None
    cpu_system: int | None
    cpu_idle: int | None
    cpu_iowait: int | None
    cpu_irq: int | None
    cpu_softirq: int | None
    cpu_steal: int | None


# ─── Task DTO ──────────────────────────────────────────────────────────────


@dataclass
class TaskCreate:
    """task 등록 시 — web router → repository."""

    target_server_id: int
    target_agent_id: str  # 발행 대상 agent_id (UUID str) — MQ 라우팅·감사 기록
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
    signal_no: int | None  # 시그널 사망 시 시그널 번호 (exit_code 와 상호배타)
    install_verified: bool | None  # 실제 설치 신호(데몬 기동+등록) — 판정 1순위, 미보고 시 None
    duration_ms: int
    stdout_tail: str
    stderr_tail: str
    completed_at: datetime


@dataclass
class ServerMetricCreate:
    # agent_id는 consumer 가 server_id 해석에 사용 — 본 DTO 미포함.
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

    # saturation (USE Method raw 신호, os-aware 임계는 recommendation) — 미측정 축 None
    sat_disk_queue: float | None
    sat_cpu_run_queue: float | None
    sat_mem_paging_rate: float | None

    # 시계열 nested 행 매핑 (disk_io·mounts·net_io·cpu_per_core)
    disk_io: list[DiskIoEntry]
    mounts: list[MountUsageEntry]
    net_io: list[NetIoEntry]
    cpu_per_core: list[CpuCoreEntry] = field(default_factory=list)

    # ADR 0052 신 host-wide 신호 (nullable, optional — agent 미발행 시 None)
    procs_running: int | None = None
    procs_blocked: int | None = None
    schedstat_run_wait_ns: int | None = None
    pswpin: int | None = None
    pswpout: int | None = None
    oom_kill: int | None = None
    mem_pages_input: int | None = None
    tcp_retrans_segs: int | None = None
    tcp_tw: int | None = None
    conntrack_count: int | None = None
    conntrack_max: int | None = None
    # Windows disk await 원자료 (device 합산, 100ns/count 누적) — 엔진 counter_agg delta 로 await 산출
    sat_disk_read_time: int | None = None
    sat_disk_write_time: int | None = None
    sat_disk_read_count: int | None = None
    sat_disk_write_count: int | None = None
    sat_disk_idle_time: int | None = None  # %util 참고 (raw 저장만)
    sat_disk_query_time: int | None = None  # 델타 분모 참고 (raw 저장만)
    # PSI some total (us 누적) — 관측·검증용, raw 저장만 (분류 미사용)
    psi_cpu_some_total: int | None = None
    psi_mem_some_total: int | None = None
    psi_io_some_total: int | None = None
    collection_interval_sec: int | None = None  # agent 설정 수집 주기(초) — raw 보존


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
