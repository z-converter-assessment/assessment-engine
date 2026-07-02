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
    composite_id: str | None = Field(default=None, min_length=1, max_length=64)
    # machine_id — raw machine-id, 표시 전용.
    machine_id: str | None = Field(default=None, max_length=64)
    agent_version: str = Field(min_length=1, max_length=32)
    collected_at: datetime
    hostname: str = Field(min_length=1, max_length=255)
    message_id: UUID
    agent_started_at: datetime
    boot_time: datetime
    # OS family — 모든 메시지 진입 시점 OS 분기 단일 진실. agent 가 전 메시지에 항상 발행 (required).
    os_family: Literal["linux", "windows"]


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
    mount: str = Field(min_length=1, max_length=255)
    total_bytes: int | None = Field(default=None, ge=0)
    free_bytes: int | None = Field(default=None, ge=0)
    avail_bytes: int | None = Field(default=None, ge=0)
    fstype: str | None = Field(default=None, max_length=32)
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
    port: int = Field(ge=1, le=65535)
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
    prefix: int = Field(ge=0, le=128)
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
    major: int | None = Field(default=None, ge=0)
    minor: int | None = Field(default=None, ge=0)
    # kind — physical/partition/lvm/raid/virtual. cagg 물리 필터 = kind=="physical" (#C5).
    kind: str | None = Field(default=None, max_length=32)


class MetricsMountInfo(BaseModel):
    mount: str = Field(min_length=1, max_length=255)
    total_bytes: int | None = Field(default=None, ge=0)
    free_bytes: int | None = Field(default=None, ge=0)
    avail_bytes: int | None = Field(default=None, ge=0)
    major: int | None = Field(default=None, ge=0)
    minor: int | None = Field(default=None, ge=0)
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
    failed_component: Literal["collect", "publish"]
    # 에이전트가 retry 종료·복구 시점에만 채워서 발행 (평소엔 None)
    retry_count: int | None = Field(default=None, ge=0)
    first_failed_at: datetime | None = None
    recovered_at: datetime | None = None


# ---------------------------------------------------------------------------
# task.result — 작업 결과 보고 메시지 (routing_key=task.result)
# ---------------------------------------------------------------------------


class TaskResultInput(MessageBase):
    """원격 작업 실행 결과 수신 메시지.

    공통 메타는 MessageBase. agent_id / composite_id / boot_time / agent_started_at은
    본 메시지에서는 null 가능 (수집 캐시와 분리된 worker 컨텍스트에서 발행 — 식별자 미산출) —
    부모 required 필드를 nullable로 override. 결과 매칭은 task_id 로 하므로 식별자 불필요.
    """

    message_type: Literal["task.result"]
    # worker 컨텍스트라 식별자(agent_id/composite_id)·부팅 메타 null 가능 — 결과 매칭은 task_id.
    agent_id: UUID | None = None
    composite_id: str | None = Field(default=None, min_length=1, max_length=64)
    boot_time: datetime | None = None
    agent_started_at: datetime | None = None

    task_id: UUID
    status: Literal["success", "failure"]
    # 실패 분류 문자열 (성공 시 null). 새 enum 은 silent pass — extra=ignore 정신, max_length만 강제.
    failure_reason: str | None = Field(default=None, max_length=32)
    exit_code: int | None = None
    # OS 식별자 — 성공 exit code 보정 정책(task_policy.effective_task_result)의 키. agent 가 task.result 에
    # os_family(MessageBase)·os_id·os_version 을 inventory 와 동일 소스로 발행 (Windows os_version=DisplayVersion).
    os_id: str | None = Field(default=None, max_length=64)
    os_version: str | None = Field(default=None, max_length=64)
    duration_ms: int = Field(ge=0)
    # 8192 cap 은 over-provision (agent wire 상한 4 KB). minor bump 로 tail 늘어도 무수정 흡수 (#B).
    stdout_tail: str = Field(max_length=8192)
    stderr_tail: str = Field(max_length=8192)
    completed_at: datetime
