"""인바운드 저장 DTO (wire -> DB). mapper 가 datapoint-array 를 순회해 채운다.

단위 canonical: 시간 s(Float), 크기 By(int). device 축 = 안정 id 문자열(이름 아님). null=미측정 보존.
"""

from dataclasses import dataclass, field
from datetime import datetime

from assessment_engine.json_types import JsonObject


@dataclass
class ServerInventoryCreate:
    # message_id 는 consumer 멱등성 체크 전용 — DTO 미포함.
    agent_id: str  # 식별 단일 키(UUID str) — DB agent_id UNIQUE·MQ 라우팅
    composite_id: str | None  # SHA-256 감사·표시용 강등(식별 미사용)
    machine_id: str | None
    hostname: str
    agent_version: str | None
    collected_at: datetime
    boot_time: datetime | None
    agent_started_at: datetime | None

    os_family: str | None  # linux|windows — task.install dispatch 단일 진실
    os_id: str | None
    os_version: str | None
    os_codename: str | None
    kernel_version: str | None
    cpu_cores: int | None
    cpu_model: str | None
    mem_total_bytes: int | None  # 단위 By(bytes). swap 은 block_devices type=swap 노드

    # 정규화 스토리지/네트워크 그래프 — JSONB pass-through(단일행 upsert·history 미러 정합).
    block_devices: list[JsonObject]  # [{name,type,size_bytes,fstype,mountpoint,parent,id,id_type}]
    net_interfaces: list[JsonObject]  # [{name,id,id_type,kind,speed_mbps,addresses:[{address,prefix,family}],gateway}]
    lvm_vgs: list[JsonObject]  # [{name,size_bytes,free_bytes,data_percent,metadata_percent}] (Linux 전용)
    ip_external: list[str] | None
    services: list[JsonObject] | None  # [{unit,sub,pid,exe}] | None
    listen_ports: list[JsonObject]  # [{proto,addr,port,uid,pid,comm}]
    # 서비스 카테고리 집합 (ingest 사전계산). read 경로 뱃지 단일 진실.
    service_categories: list[str]

    # OS 재현 서술자 (flat) + boot 노드 + 비블록 마운트 — reproduction 완전화. agent 발행, 미해당은 None.
    arch: str | None = None
    bits: int | None = None
    boot_firmware: str | None = None
    secure_boot: bool | None = None
    edition: str | None = None
    # CurrentVersion ProductName 원문(Windows only, Linux null) — 교정 없이 실측 그대로(agent 측 무가공 원칙).
    # os_display 짧은 라벨(연도/세대 토큰) 파싱 소스 — mappers/os_eol.py windows_short_label_from_product_name.
    product_name: str | None = None
    timezone: str | None = None
    rtc_utc: bool | None = None
    boot: JsonObject | None = None
    nonblock_mounts: list[JsonObject] | None = None


# --- 시계열 nested 행 (datapoint-array -> dataclass 타입 보장) ---


@dataclass
class DiskIoEntry:
    # device_id = 안정 id 문자열("<scheme>:<value>"). device_name = 표시명(nullable).
    device_id: str
    device_name: str | None = None
    io_read_bytes: int | None = None  # disk.io direction=read (By counter)
    io_write_bytes: int | None = None
    ops_read: int | None = None  # disk.operations direction=read
    ops_write: int | None = None
    io_time_s: float | None = None  # disk.io_time (%util 산출, s counter)
    op_read_time_s: float | None = None  # disk.operation_time direction=read (await 산출, s counter)
    op_write_time_s: float | None = None
    pending_ops: float | None = None  # disk.pending_operations (queue gauge)


@dataclass
class NetIoEntry:
    iface_id: str  # 안정키 = MAC ("mac:..")
    iface_name: str | None = None
    rx_bytes: int | None = None
    tx_bytes: int | None = None
    rx_packets: int | None = None
    tx_packets: int | None = None
    rx_errors: int | None = None
    tx_errors: int | None = None
    rx_dropped: int | None = None
    tx_dropped: int | None = None
    link_speed_bps: int | None = None  # network.link.speed (bit/s gauge, virtio null)


@dataclass
class FilesystemEntry:
    # filesystem.usage/inodes.usage (gauge, 시점값). device_id + mountpoint 병기.
    mountpoint: str
    device_id: str | None = None
    fstype: str | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    inodes_used: int | None = None
    inodes_free: int | None = None


@dataclass
class CpuCoreEntry:
    # per-core seconds (cpu.time attr.cpu). core_id = 논리 코어 인덱스.
    core_id: int
    cpu_user_s: float | None = None
    cpu_nice_s: float | None = None
    cpu_system_s: float | None = None
    cpu_idle_s: float | None = None
    cpu_iowait_s: float | None = None
    cpu_irq_s: float | None = None
    cpu_softirq_s: float | None = None
    cpu_steal_s: float | None = None


@dataclass
class PressureEntry:
    # PSI (Linux 4.20+). NK 축 = resource x scope. window(10/60/300)는 ratio 컬럼으로 평탄화.
    resource: str  # cpu|memory|io
    scope: str  # some|full
    stall_time_s: float | None = None  # pressure.stall.time (counter s — 14일 saturation canonical)
    ratio_avg10: float | None = None  # pressure.stall.ratio window=10 (gauge, 실시간 참고)
    ratio_avg60: float | None = None
    ratio_avg300: float | None = None


@dataclass
class DiskErrorEntry:
    # disk.errors (E축, 가변 차원). 정상 시 0.
    device_id: str
    error_kind: str  # mdraid|btrfs|ext4|eventlog|ioerr
    error_class: str  # member_errors|degraded|corruption|errors_count..
    member: str | None = None
    count: int | None = None


# --- Task DTO ---


@dataclass
class TaskCreate:
    """task 등록 시 — web router -> repository."""

    target_server_id: int
    target_agent_id: str
    task_type: str
    params: JsonObject | None
    deadline_at: datetime | None = None


@dataclass
class TaskResultUpdate:
    """task 결과 보고 — MQ -> consumer -> repository. public_id = 메시지 task_id 로 Task.public_id 매칭."""

    public_id: str
    status: str
    failure_reason: str | None
    exit_code: int | None
    signal_no: int | None  # 시그널 사망 시 번호 (exit_code 와 상호배타)
    task_policy: bool | None  # 실제 설치 신호(데몬 기동+등록) — exit_code 보다 우선. 미보고 시 None
    duration_ms: int
    stdout_tail: str
    stderr_tail: str
    completed_at: datetime


@dataclass
class ServerMetricCreate:
    # agent_id 는 consumer 가 server_id 해석에 사용 — 본 DTO 미포함.
    # boot_time/agent_started_at: 시계열 행마다 저장, counter reset 정밀 식별 (#C1·#B).
    collected_at: datetime
    boot_time: datetime | None
    agent_started_at: datetime | None

    # CPU 호스트 집계 (cpu.time attr.cpu 합산, s counter)
    cpu_user_s: float | None = None
    cpu_nice_s: float | None = None
    cpu_system_s: float | None = None
    cpu_idle_s: float | None = None
    cpu_iowait_s: float | None = None
    cpu_irq_s: float | None = None
    cpu_softirq_s: float | None = None
    cpu_steal_s: float | None = None
    cpu_logical_count: int | None = None  # cpu.logical.count
    cpu_run_queue: float | None = None  # cpu.run_queue (gauge)
    cpu_blocked: float | None = None  # cpu.blocked (D-state gauge)
    cpu_mce: int | None = None  # cpu.mce (counter)

    # 메모리 (By)
    mem_free_bytes: int | None = None
    mem_cached_bytes: int | None = None
    mem_buffered_bytes: int | None = None
    mem_available_bytes: int | None = None
    mem_used_bytes: int | None = None
    mem_limit_bytes: int | None = None  # memory.limit
    mem_commit_usage_bytes: int | None = None
    mem_commit_limit_bytes: int | None = None
    mem_hardware_corrupted_bytes: int | None = None
    mem_oom_kill: int | None = None  # counter
    # paging (counter)
    paging_in: int | None = None
    paging_out: int | None = None
    paging_major: int | None = None
    # 네트워크 host-wide (counter/gauge)
    net_tcp_retransmits: int | None = None
    net_conntrack_usage: int | None = None
    net_conntrack_limit: int | None = None

    # 시계열 nested 행
    disk_io: list[DiskIoEntry] = field(default_factory=list[DiskIoEntry])
    net_io: list[NetIoEntry] = field(default_factory=list[NetIoEntry])
    filesystems: list[FilesystemEntry] = field(default_factory=list[FilesystemEntry])
    cpu_per_core: list[CpuCoreEntry] = field(default_factory=list[CpuCoreEntry])
    pressure: list[PressureEntry] = field(default_factory=list[PressureEntry])
    disk_errors: list[DiskErrorEntry] = field(default_factory=list[DiskErrorEntry])


# --- 보고서 발행 job INSERT 입력 ---


@dataclass
class DiagnosticJobCreate:
    """보고서 발행 job INSERT 입력 — id·created_at·status 는 DB default.

    job_type: customer_report / engineer_report. scope: server|environment.
    input_hash: sha256(scope + canonical(input_params) + job_type) — 캐시·active UNIQUE 키.
    """

    scope: str
    input_params: JsonObject
    input_hash: str
    job_type: str = "customer_report"
    requested_by: str | None = None
