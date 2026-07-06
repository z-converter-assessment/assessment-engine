from datetime import datetime
from ipaddress import ip_address, ip_interface
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MessageBase(BaseModel):
    # 계약 진화 정책 (#B) — `extra=ignore`로 agent가 새 필드 추가해도 엔진은 통과·무시. 자식 클래스 상속.
    model_config = ConfigDict(extra="ignore")

    # agent_id — 호스트 식별 단일 키 (UUID v4). 첫 실행 시 생성·영구저장 — MAC/machine_id 재발급과 무관 불변.
    # DB UNIQUE·MQ 라우팅(agent.tasks.{agent_id}) 키. task.result 한정 null override (worker 컨텍스트, task_id 매칭).
    agent_id: UUID
    # composite_id — SHA-256(machine_id + MAC). 감사·표시용 강등 (clone collision 진단). 식별·라우팅 미사용.
    # agent 는 digest 실패 시 "" 발행 가능 (wire 는 "" 허용) — 아래 validator 가 None 으로 정규화.
    composite_id: str | None = Field(default=None, max_length=64)
    # machine_id — raw machine-id, 표시 전용.
    machine_id: str | None = Field(default=None, max_length=64)
    agent_version: str = Field(min_length=1, max_length=32)
    collected_at: datetime
    hostname: str = Field(min_length=1, max_length=255)
    message_id: UUID
    # agent_started_at — 발행 프로세스 기동 시각. agent 가 항상 측정·발행 (task.result 만 null override).
    agent_started_at: datetime
    # boot_time — 부팅 시각. 판독 불가 시 null (wire nullable, null 이면 counter reset 은 delta 휴리스틱).
    boot_time: datetime | None = None
    # OS family — 모든 메시지 진입 시점 OS 분기 단일 진실. agent 가 전 메시지에 항상 발행 (required).
    os_family: Literal["linux", "windows"]

    @field_validator("composite_id", mode="before")
    @classmethod
    def _empty_composite_to_none(cls, v: object) -> object:
        return None if v == "" else v


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------


class DiskInfo(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    size_bytes: int | None = Field(default=None, ge=0)
    type: str | None = Field(default=None, max_length=32)
    # major/minor — Linux 디바이스 식별 (POSIX). mount-disk 조인 키.
    major: int | None = Field(default=None, ge=0)
    minor: int | None = Field(default=None, ge=0)
    # kind — agent 공용 분류기 (physical/partition/lvm/raid/virtual). 물리 판정 단일 신호 (#E2).
    kind: str | None = Field(default=None, max_length=32)


class InventoryMountInfo(BaseModel):
    # inventory mount = 구조(structure)만 — 동적 free/avail 은 metrics(MetricsMountInfo) 전담.
    mount: str = Field(min_length=1, max_length=255)
    total_bytes: int | None = Field(default=None, ge=0)
    fstype: str | None = Field(default=None, max_length=32)
    # major/minor — inventory 만 발행. mount-disk 조인 키(device_filters.find_parent_disk).
    major: int | None = Field(default=None, ge=0)
    minor: int | None = Field(default=None, ge=0)
    # kind — data/boot/image (가상 fs 는 agent pre-drop). 데이터 볼륨 판정 = kind=="data" (#E2).
    kind: str | None = Field(default=None, max_length=32)


class InventoryServiceInfo(BaseModel):
    unit: str = Field(min_length=1, max_length=255)
    sub: str = Field(min_length=1, max_length=64)
    # pid — services <-> listen_ports 정확 join 키. Windows dwProcessId / Linux systemctl MainPID.
    # 비-systemd(EL6)·NT5 는 null (플랫폼 차이) -> classify 가 comm~name 휴리스틱 fallback.
    pid: int | None = Field(default=None, ge=0)
    exe: str | None = Field(default=None, max_length=255)


class InventoryListenPortInfo(BaseModel):
    proto: Literal["tcp", "tcp6", "udp", "udp6"]
    addr: str = Field(min_length=1, max_length=64)
    # wire 계약(wire.schema.json)이 minimum:0 이라 엔진도 ge=0 으로 그 permissive superset 유지 —
    # 엔진이 wire 가 허용하는 값을 거부하지 않는다(#B). 실측 LISTEN 포트는 >=1 이나 계약 정합 우선.
    port: int = Field(ge=0, le=65535)
    # Windows agent 는 POSIX uid 미존재로 null 발행 — nullable (Linux 만 값). #B 플랫폼 차이.
    uid: int | None = Field(default=None, ge=0)
    pid: int | None = None
    comm: str | None = Field(default=None, max_length=64)


class InterfaceInfo(BaseModel):
    """내부 네트워크 인터페이스 — bare address + prefix + family + kind 구조화 (agent 공용 iface 분류기).

    kind 는 물리/가상 taxonomy(physical/loopback/bridge/veth/bond_master/bond_member/vlan/tunnel/virtual;
    Windows 는 coarse physical/loopback/tunnel/virtual) — 토폴로지 가상망 제외 단일 신호.
    """

    name: str = Field(min_length=1, max_length=256)
    address: str = Field(min_length=1, max_length=64)
    # null = 측정 불가 (계약: 값=실측, null=측정불가). agent 가 prefix 를 못 뽑는 경로
    # (예: NT5.2 IPv6 — GetIpAddrTable 부재)에서 null 로 발행하므로 non-null 강제하면 안 된다.
    # 0 은 유효한 /0 실측이라 null 과 구분한다.
    prefix: int | None = Field(default=None, ge=0, le=128)
    family: Literal["ipv4", "ipv6"]
    kind: str = Field(min_length=1, max_length=32)
    # default route 게이트웨이 IP (없으면 null — legacy Windows NT5.2 등). 토폴로지 subnet disambiguation 신호.
    gateway: str | None = Field(default=None, max_length=64)

    @field_validator("address", mode="before")
    @classmethod
    def validate_address(cls, v: object) -> object:
        ip_address(str(v))  # bare IP (prefix 는 별도 필드) — 형식 검증만
        return v

    @field_validator("gateway", mode="before")
    @classmethod
    def validate_gateway(cls, v: object) -> object:
        if v is None or v == "":
            return None
        ip_address(str(v))  # 형식 검증만
        return v


class InventoryInput(MessageBase):
    message_type: Literal["inventory"]

    # os_family(MessageBase 상속) 활용처: task.install dispatch 분기 단일 진실 (ADR 0020).
    os_id: str | None = Field(default=None, max_length=64)
    os_version: str | None = Field(default=None, max_length=64)
    os_codename: str | None = Field(default=None, max_length=64)
    kernel_version: str | None = Field(default=None, max_length=64)
    cpu_cores: int | None = Field(default=None, gt=0)
    cpu_model: str | None = Field(default=None, max_length=255)
    mem_total_kb: int | None = Field(default=None, ge=0)
    swap_total_kb: int | None = Field(default=None, ge=0)

    # 내부 인터페이스 — 구조화(name/address/prefix/family/kind/gateway). 토폴로지·상세 표시 소스 (#E2).
    interfaces: list[InterfaceInfo] = Field(default_factory=list)
    ip_external: list[str] | None = None

    @field_validator("ip_external", mode="before")
    @classmethod
    def validate_ip_external(cls, v: object) -> object:
        # mode="before" — JSON 파싱 직후 임의 타입. list/tuple만 허용. bare IP·CIDR 둘 다 수용.
        if v is None:
            return v
        if not isinstance(v, (list, tuple)):
            raise TypeError(f"expected list or tuple of IP strings, got {type(v).__name__}")
        for item in v:
            ip_interface(str(item))
        return v

    # mac_addresses — NIC별 MAC 목록. clone collision 감사용 raw 보존 (식별은 agent_id).
    mac_addresses: list[str] = Field(default_factory=list)

    disks: list[DiskInfo] = Field(default_factory=list)
    mounts: list[InventoryMountInfo] = Field(default_factory=list)
    services: list[InventoryServiceInfo] | None = None
    listen_ports: list[InventoryListenPortInfo] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


# Windows 는 GetSystemTimes 로 user/system/idle 만 실측하고 nice/iowait/irq/softirq/steal 은 OS 개념 부재로
# null 발행 (계약 값 의미론: null = 측정 불가). 8필드 모두 nullable — 하류(inbound DTO·mapper·DB model)는
# 이미 int|None 이라 스키마만 완화하면 끝까지 정합. cpu_total 은 SQL COALESCE 성분 합으로 정규화(#C2).
class CpuStat(BaseModel):
    user: int | None = Field(default=None, ge=0)
    nice: int | None = Field(default=None, ge=0)
    system: int | None = Field(default=None, ge=0)
    idle: int | None = Field(default=None, ge=0)
    iowait: int | None = Field(default=None, ge=0)
    irq: int | None = Field(default=None, ge=0)
    softirq: int | None = Field(default=None, ge=0)
    steal: int | None = Field(default=None, ge=0)


class DiskIoInfo(BaseModel):
    device: str = Field(min_length=1, max_length=128)  # Windows 디스크 이름 여유 (방어)
    reads_completed: int | None = Field(default=None, ge=0)
    writes_completed: int | None = Field(default=None, ge=0)
    sectors_read: int | None = Field(default=None, ge=0)
    sectors_written: int | None = Field(default=None, ge=0)
    # ADR 0052 — await 원자료(ms 누적) + %util·avgqu 참고. 엔진이 delta 로 산출.
    time_reading_ms: int | None = Field(default=None, ge=0)
    time_writing_ms: int | None = Field(default=None, ge=0)
    io_ticks_ms: int | None = Field(default=None, ge=0)
    weighted_io_ms: int | None = Field(default=None, ge=0)
    major: int | None = Field(default=None, ge=0)
    minor: int | None = Field(default=None, ge=0)
    # kind — physical/partition/lvm/raid/virtual. cagg 물리 필터 = kind=="physical" (#C5).
    kind: str | None = Field(default=None, max_length=32)


class MetricsMountInfo(BaseModel):
    # metrics mount = 동적 사용량(usage)만 — 정적 major/minor 는 inventory(InventoryMountInfo) 전담.
    mount: str = Field(min_length=1, max_length=255)
    total_bytes: int | None = Field(default=None, ge=0)
    free_bytes: int | None = Field(default=None, ge=0)
    avail_bytes: int | None = Field(default=None, ge=0)
    # ADR 0052 — inode (작은 파일 폭증 시 바이트 남아도 먼저 소진). statvfs f_files/f_ffree.
    inodes_total: int | None = Field(default=None, ge=0)
    inodes_free: int | None = Field(default=None, ge=0)
    # kind — data/boot/image. cagg 데이터 볼륨 필터 = kind=="data" (#C5).
    kind: str | None = Field(default=None, max_length=32)


class NetIoInfo(BaseModel):
    # Windows 인터페이스는 WFP/QoS 필터 드라이버 체인 이름이라 매우 김 (NDIS 한계 256). raw 수용.
    interface: str = Field(min_length=1, max_length=256)
    rx_bytes: int | None = Field(default=None, ge=0)
    tx_bytes: int | None = Field(default=None, ge=0)
    rx_packets: int | None = Field(default=None, ge=0)
    tx_packets: int | None = Field(default=None, ge=0)
    rx_errors: int | None = Field(default=None, ge=0)
    tx_errors: int | None = Field(default=None, ge=0)
    # ADR 0052 — 드롭 (품질 신호). 누적.
    rx_drops: int | None = Field(default=None, ge=0)
    tx_drops: int | None = Field(default=None, ge=0)
    # kind — physical/loopback/bridge/veth/bond_*/vlan/tunnel/virtual (Windows coarse). 물리 = kind=="physical" (#C5).
    kind: str | None = Field(default=None, max_length=32)


class DiskQueueEntry(BaseModel):
    # 물리 디스크별 순간 큐 깊이 (Windows PhysicalDriveN IOCTL_DISK_PERFORMANCE.QueueDepth). device = disks[].name.
    device: str = Field(min_length=1, max_length=128)
    queue: float | None = Field(default=None, ge=0)


class SaturationInfo(BaseModel):
    """USE Method saturation raw 신호 (정규화 안 함, os-aware 임계는 recommendation). 미측정 축은 null.

    disk_queue: 물리 디스크별 큐 깊이 배열 [{device, queue}] (Windows). 엔진이 per-device max 로 축약해 판정
    (디스크당 임계 — 합/정규화 불요). 빈 배열=신호 없음. Linux 는 iowait 사용이라 미발행.
    cpu_run_queue: System\\Processor Queue Length gauge (엔진 run queue/core 판정).
    mem_paging_rate: Memory Pages/sec 누적 counter (엔진이 delta/dt 로 rate 환산). 각 축은 perflib 못 읽으면 null.
    """

    disk_queue: list[DiskQueueEntry] | None = None
    cpu_run_queue: float | None = Field(default=None, ge=0)
    mem_paging_rate: float | None = Field(default=None, ge=0)


class MetricsInput(MessageBase):
    message_type: Literal["metrics"]

    cpu_stat: CpuStat | None = None
    mem_total_kb: int | None = Field(default=None, ge=0)
    mem_free_kb: int | None = Field(default=None, ge=0)
    mem_available_kb: int | None = Field(default=None, ge=0)
    mem_buffers_kb: int | None = Field(default=None, ge=0)
    mem_cached_kb: int | None = Field(default=None, ge=0)
    swap_total_kb: int | None = Field(default=None, ge=0)
    swap_free_kb: int | None = Field(default=None, ge=0)
    load_1m: float | None = Field(default=None, ge=0.0)
    load_5m: float | None = Field(default=None, ge=0.0)
    load_15m: float | None = Field(default=None, ge=0.0)

    # ADR 0052 신 host-wide 신호 (raw, optional). psi_*·cpu_per_core 는 #B 대로 미수용(extra=ignore drop, 필요 시 추가).
    procs_running: int | None = Field(default=None, ge=0)
    procs_blocked: int | None = Field(default=None, ge=0)
    schedstat_run_wait_ns: int | None = Field(default=None, ge=0)
    pswpin: int | None = Field(default=None, ge=0)
    pswpout: int | None = Field(default=None, ge=0)
    oom_kill: int | None = Field(default=None, ge=0)
    mem_pages_input: int | None = Field(default=None, ge=0)  # Windows Pages Input raw
    tcp_retrans_segs: int | None = Field(default=None, ge=0)
    tcp_tw: int | None = Field(default=None, ge=0)
    conntrack_count: int | None = Field(default=None, ge=0)
    conntrack_max: int | None = Field(default=None, ge=0)

    disk_io: list[DiskIoInfo] = Field(default_factory=list)
    mounts: list[MetricsMountInfo] = Field(default_factory=list)
    net_io: list[NetIoInfo] = Field(default_factory=list)
    saturation: SaturationInfo | None = None
    # agent 수집 주기(초). sample_sufficiency 는 5분 버킷 기반(288/day)이라 주기<=5분이면 무관 —
    # 5분 초과 주기 엣지케이스에서 기대 버킷 보정용 (없으면 288 가정).
    collection_interval_sec: int | None = Field(default=None, gt=0)


# ---------------------------------------------------------------------------
# error
# ---------------------------------------------------------------------------


class ErrorInput(MessageBase):
    message_type: Literal["error"]
    error_code: str = Field(min_length=1, max_length=64)
    # error_message — 빈 문자열 허용 (#B extra=ignore 정신 — 발행 측 NULL 인자 fallback 으로
    # 빈 문자열 가능, engine 은 통과시키고 로깅 단에서 fallback 표기). agent 측 수정 없이 흡수.
    error_message: str
    # 실 값 collect/publish, NULL 인자 fallback 은 두 트리 모두 "agent". wire 는 non-empty 자유 문자열이라
    # Literal 로 좁히면 "agent" 도달 시 유효 메시지를 reject/DLQ — 계약대로 자유 문자열 수용 (로깅 전용, 미저장).
    failed_component: str = Field(min_length=1, max_length=32)
    # 에이전트가 retry 종료·복구 시점에만 채워서 발행 (평소엔 None)
    retry_count: int | None = Field(default=None, ge=0)
    first_failed_at: datetime | None = None
    recovered_at: datetime | None = None


# ---------------------------------------------------------------------------
# task.result — 작업 결과 보고 메시지 (routing_key=task.result)
# ---------------------------------------------------------------------------


class TaskResultInput(MessageBase):
    """원격 작업 실행 결과 수신 메시지.

    worker 가 자체 envelope 로 발행 — composite_id 미발행(MessageBase default None), boot_time/agent_started_at
    항상 null. agent_id 는 발행하나 매칭은 task_id 라 nullable override (agent_id 부재로 유효 결과를 버리지 않음).
    """

    message_type: Literal["task.result"]
    # 매칭은 task_id — agent_id 부재로 결과를 reject 하지 않도록 nullable override.
    agent_id: UUID | None = None
    # task.result 는 agent_started_at 를 항상 null 발행 (MessageBase 는 required) — nullable override.
    agent_started_at: datetime | None = None

    task_id: UUID
    status: Literal["success", "failure"]
    # 실패 분류 문자열 (성공 시 null). 새 enum 은 silent pass — extra=ignore 정신, max_length만 강제.
    failure_reason: str | None = Field(default=None, max_length=32)
    # exit_code/signal_no 는 POSIX wait status 반영 상호배타: 정상종료=exit_code 값/signal_no null,
    # 시그널종료=exit_code null/signal_no 값, 미포착=둘 다 null. signal_no 는 Windows 항상 null.
    exit_code: int | None = None
    signal_no: int | None = None
    # OS 식별자 — 성공 exit code 보정 정책(task_policy.effective_task_result)의 키. agent 가 task.result 에
    # os_family(MessageBase)·os_id·os_version 을 inventory 와 동일 소스로 발행 (Windows os_version=DisplayVersion).
    os_id: str | None = Field(default=None, max_length=64)
    os_version: str | None = Field(default=None, max_length=64)
    duration_ms: int = Field(ge=0)
    # 8192 cap 은 over-provision (agent wire 상한 4 KB). minor bump 로 tail 늘어도 무수정 흡수 (#B).
    stdout_tail: str = Field(max_length=8192)
    stderr_tail: str = Field(max_length=8192)
    completed_at: datetime
