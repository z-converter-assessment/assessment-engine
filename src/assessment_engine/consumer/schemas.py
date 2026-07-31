"""인바운드 wire Pydantic 스키마 — 계약 = docs/reference/contracts/agent-data.md + wire.schema.json.

metrics/inventory = envelope + system.* datapoint-array + inventory 배열. task.result/error = envelope + 평면 body.
검증은 진입점 1회 (#F3). `extra=ignore` 로 v2 내부 forward-compat.
"""

from datetime import datetime
from ipaddress import ip_address, ip_interface
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from assessment_engine.contract import CONTRACT_VERSION

_CONTRACT_MAJOR = CONTRACT_VERSION.split(".", 1)[0]


class MessageBase(BaseModel):
    # 계약 진화 (#B) — extra=ignore 로 agent 신규 필드 통과·무시. 자식 상속.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    # 통일 계약 버전(engine 레포 기준, contract.CONTRACT_VERSION). 형식 major.minor.
    # wire/assessment/export/task.install 4계약 공통 단일 값.
    schema_version: str = Field(pattern=r"^\d+\.\d+$")
    # agent_id — 호스트 식별 단일 키(불변 UUID). DB UNIQUE·MQ 라우팅. task.result 한정 nullable(task_id 매칭).
    agent_id: UUID
    message_id: UUID
    collected_at: datetime
    # composite_id — SHA-256 감사·표시용(식별 미사용). "" -> None 정규화.
    composite_id: str | None = Field(default=None, max_length=64)
    machine_id: str | None = Field(default=None, max_length=64)
    agent_version: str | None = Field(default=None, min_length=1, max_length=32)
    # boot_time — 부팅 시각(판독 불가 시 null). counter reset 게이트.
    boot_time: datetime | None = None
    # agent_started_at — 발행 프로세스 기동 시각. task.result 만 항상 null.
    agent_started_at: datetime | None = None
    os_family: Literal["linux", "windows"]

    @field_validator("schema_version")
    @classmethod
    def _gate_major(cls, v: str) -> str:
        # minor 는 additive 변경 추적용이라 수신을 막지 않는다 — 규약은 contracts/agent-data.md.
        if v.split(".", 1)[0] != _CONTRACT_MAJOR:
            raise ValueError(f"schema_version major mismatch: {v} (engine speaks {_CONTRACT_MAJOR}.x)")
        return v

    @field_validator("composite_id", mode="before")
    @classmethod
    def _empty_composite_to_none(cls, v: object) -> object:
        return None if v == "" else v


# ---------------------------------------------------------------------------
# system.* datapoint-array (metrics)
# ---------------------------------------------------------------------------


class Datapoint(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # attr — 차원 구분(device/state/direction/resource/scope/window/source/cpu/kind/class). 생략=단일 스칼라.
    attr: dict[str, str | int] = Field(default_factory=dict)
    # value — number 또는 null(측정불가, 0 과 구분).
    value: float | None = None


class Metric(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["counter", "gauge"]
    unit: str = Field(min_length=1, max_length=32)
    points: list[Datapoint] = Field(default_factory=list)


# 네임스페이스 = {metric명: Metric}. metric명(cpu.time 등)은 dict 키라 dot 무관.
Namespace = dict[str, Metric]


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


class MetricsInput(MessageBase):
    message_type: Literal["metrics"]

    # 필수 4 네임스페이스 (키 present, 값은 object|null 허용).
    system_cpu: Namespace | None = Field(alias="system.cpu")
    system_memory: Namespace | None = Field(alias="system.memory")
    system_disk: Namespace | None = Field(alias="system.disk")
    system_network: Namespace | None = Field(alias="system.network")
    # 옵셔널 네임스페이스. Windows system.pressure 는 키 present + 값 null.
    system_paging: Namespace | None = Field(default=None, alias="system.paging")
    system_filesystem: Namespace | None = Field(default=None, alias="system.filesystem")
    system_pressure: Namespace | None = Field(default=None, alias="system.pressure")
    system_cgroup: Namespace | None = Field(default=None, alias="system.cgroup")


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------


class BlockDeviceInfo(BaseModel):
    """정규화 평면 DAG 노드. parent=부모 id(root=null), id/id_type=안정키(E절). 복수 부모면 노드 반복."""

    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=32)  # disk/part/lvm/crypt/raid/mpath/dynamic/volume/swap
    size_bytes: int | None = Field(default=None, ge=0)
    fstype: str | None = Field(default=None, max_length=64)
    mountpoint: str | None = Field(default=None, max_length=255)
    parent: str | None = Field(default=None, max_length=128)
    id: str | None = Field(default=None, max_length=128)
    id_type: str | None = Field(default=None, max_length=16)
    # 레이아웃 상세 (reproduction, agent 확장 — 자연 노드타입에만 emit, 미해당은 부재). 엔진 read 시 정규화(raid_level).
    partition_table: str | None = Field(default=None, max_length=8)  # gpt|mbr
    sector_size: int | None = Field(default=None, ge=0)
    serial: str | None = Field(default=None, max_length=128)
    wwn: str | None = Field(default=None, max_length=64)
    rotational: bool | None = None
    part_number: int | None = Field(default=None, ge=0)
    part_start_bytes: int | None = Field(default=None, ge=0)
    part_type: str | None = Field(default=None, max_length=64)  # GPT GUID / MBR 0x hex
    part_name: str | None = Field(default=None, max_length=128)
    part_flags: list[str] | None = None
    fs_uuid: str | None = Field(default=None, max_length=64)
    fs_label: str | None = Field(default=None, max_length=128)
    block_size: int | None = Field(default=None, ge=0)
    mount_options: list[str] | None = None
    fs_freq: int | None = None
    fs_passno: int | None = None
    lvm_vg: str | None = Field(default=None, max_length=128)
    lvm_lv: str | None = Field(default=None, max_length=128)
    lvm_segtype: str | None = Field(default=None, max_length=32)
    lvm_stripes: int | None = Field(default=None, ge=0)
    lvm_stripe_size_kib: int | None = Field(default=None, ge=0)
    raid_level: int | str | None = None  # raw(raid5/linear/int) — 엔진 read 시 int|null 정규화
    raid_chunk_kib: int | None = Field(default=None, ge=0)
    raid_metadata: str | None = Field(default=None, max_length=16)
    raid_uuid: str | None = Field(default=None, max_length=64)
    crypt_type: str | None = Field(default=None, max_length=16)  # luks1|luks2


class NetAddressInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    address: str = Field(min_length=1, max_length=64)
    prefix: int | None = Field(default=None, ge=0, le=128)
    family: Literal["ipv4", "ipv6"]
    origin: str | None = Field(default=None, max_length=16)  # static|dhcp (reproduction, agent 확장)

    @field_validator("address", mode="before")
    @classmethod
    def _validate_address(cls, v: object) -> object:
        ip_address(str(v))  # bare IP 형식 검증
        return v


class RouteInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    dest: str = Field(min_length=1, max_length=64)  # CIDR
    via: str | None = Field(default=None, max_length=64)


class NetInterfaceInfo(BaseModel):
    """안정키 id=MAC + 다중 IP addresses[]. name 은 표시용."""

    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=256)
    id: str | None = Field(default=None, max_length=64)
    id_type: str | None = Field(default=None, max_length=16)
    kind: str | None = Field(default=None, max_length=32)
    speed_mbps: int | None = Field(default=None, ge=0)
    addresses: list[NetAddressInfo] = Field(default_factory=list)
    gateway: str | None = Field(default=None, max_length=64)
    # 레이아웃 상세 (reproduction, agent 확장). bond_mode raw(엔진 정규화).
    mtu: int | None = Field(default=None, ge=0)
    dns: list[str] | None = None
    routes: list[RouteInfo] | None = None
    bond_mode: str | None = Field(default=None, max_length=32)
    vlan_id: int | None = Field(default=None, ge=0)

    @field_validator("gateway", mode="before")
    @classmethod
    def _validate_gateway(cls, v: object) -> object:
        if v is None or v == "":
            return None
        ip_address(str(v))
        return v


class LvmVgInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=128)
    size_bytes: int | None = Field(default=None, ge=0)
    free_bytes: int | None = Field(default=None, ge=0)  # 확장 여력(3계층째)
    data_percent: float | None = Field(default=None, ge=0)
    metadata_percent: float | None = Field(default=None, ge=0)
    # 레이아웃 상세 (reproduction, agent 확장). pv_ids = 구성 PV 의 block_device id.
    vg_uuid: str | None = Field(default=None, max_length=64)
    extent_size_bytes: int | None = Field(default=None, ge=0)
    pv_ids: list[str] | None = None


class InventoryServiceInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    unit: str = Field(min_length=1, max_length=255)
    sub: str = Field(min_length=1, max_length=64)
    pid: int | None = Field(default=None, ge=0)
    exe: str | None = Field(default=None, max_length=255)


class InventoryListenPortInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    proto: Literal["tcp", "tcp6", "udp", "udp6"]
    addr: str = Field(min_length=1, max_length=64)
    # wire minimum:0 permissive superset 유지(#B).
    port: int = Field(ge=0, le=65535)
    uid: int | None = Field(default=None, ge=0)  # Windows null
    pid: int | None = None
    comm: str | None = Field(default=None, max_length=64)


class BootInfo(BaseModel):
    """OS 재현 boot 서술자 (Linux /proc/cmdline; Windows null). grub_install_target 은 agent 1차 null."""

    model_config = ConfigDict(extra="ignore")
    kernel_cmdline: str | None = Field(default=None, max_length=4096)
    root_ref_type: str | None = Field(default=None, max_length=16)  # uuid|label|partuuid|path
    grub_install_target: str | None = Field(default=None, max_length=128)


class NonblockMountInfo(BaseModel):
    """블록장치 없는 마운트(tmpfs/nfs/cifs/9p/fuse.*/bind) — Linux 전용. fstab 재생성 팩트."""

    model_config = ConfigDict(extra="ignore")
    source: str = Field(min_length=1, max_length=512)
    target: str = Field(min_length=1, max_length=512)
    fstype: str | None = Field(default=None, max_length=64)
    options: list[str] | None = None
    fs_freq: int | None = None
    fs_passno: int | None = None


class InventoryInput(MessageBase):
    message_type: Literal["inventory"]

    hostname: str = Field(min_length=1, max_length=255)
    os_id: str | None = Field(default=None, max_length=64)
    os_version: str | None = Field(default=None, max_length=64)
    os_codename: str | None = Field(default=None, max_length=64)
    kernel_version: str | None = Field(default=None, max_length=64)
    cpu_model: str | None = Field(default=None, max_length=255)
    cpu_cores: int | None = Field(default=None, gt=0)
    mem_total_bytes: int | None = Field(default=None, ge=0)  # 단위 By(bytes)
    ip_external: list[str] | None = None

    # OS 재현 서술자 (flat 최상위) — reproduction OUTPUT 계약. agent 발행(collect_inventory), timezone 은 IANA 원문.
    arch: str | None = Field(default=None, max_length=32)  # uname machine (x86_64/aarch64)
    bits: int | None = Field(default=None, ge=0)  # 32|64
    boot_firmware: str | None = Field(default=None, max_length=8)  # uefi|bios
    secure_boot: bool | None = None
    edition: str | None = Field(default=None, max_length=64)  # Windows EditionID (Linux null)
    # CurrentVersion ProductName 원문(Windows only, Linux null, agent 버퍼 128B) — 교정 없이 실측 그대로.
    product_name: str | None = Field(default=None, max_length=128)
    timezone: str | None = Field(default=None, max_length=64)  # IANA
    rtc_utc: bool | None = None
    boot: BootInfo | None = None
    nonblock_mounts: list[NonblockMountInfo] | None = None

    block_devices: list[BlockDeviceInfo] = Field(default_factory=list)
    net_interfaces: list[NetInterfaceInfo] = Field(default_factory=list)
    lvm_vgs: list[LvmVgInfo] = Field(default_factory=list)  # Linux 전용(Windows 미발행)
    services: list[InventoryServiceInfo] | None = None
    listen_ports: list[InventoryListenPortInfo] = Field(default_factory=list)

    @field_validator("ip_external", mode="before")
    @classmethod
    def _validate_ip_external(cls, v: object) -> object:
        if v is None:
            return v
        if not isinstance(v, (list, tuple)):
            raise TypeError(f"expected list of IP strings, got {type(v).__name__}")
        for item in v:
            ip_interface(str(item))
        return v


# ---------------------------------------------------------------------------
# error
# ---------------------------------------------------------------------------


class ErrorInput(MessageBase):
    message_type: Literal["error"]
    error_code: str = Field(min_length=1, max_length=64)
    error_message: str
    # 자유 문자열 수용(wire permissive, Literal 로 좁히면 유효 메시지 DLQ). 로깅 전용.
    failed_component: str = Field(min_length=1, max_length=32)
    retry_count: int | None = Field(default=None, ge=0)
    first_failed_at: datetime | None = None
    recovered_at: datetime | None = None


# ---------------------------------------------------------------------------
# task.result
# ---------------------------------------------------------------------------


class TaskResultInput(MessageBase):
    """작업 결과. worker 발행 — composite_id 미발행, boot_time/agent_started_at 항상 null. task_id 매칭."""

    message_type: Literal["task.result"]
    agent_id: UUID | None = None  # 매칭은 task_id — agent_id 부재로 결과 reject 안 함

    hostname: str = Field(min_length=1, max_length=255)
    os_id: str | None = Field(default=None, max_length=64)
    os_version: str | None = Field(default=None, max_length=64)
    os_codename: str | None = Field(default=None, max_length=64)

    # task_id — wire 계약상 free string(예 "task-abc123"). agent_id(불변 UUID)와 달리 UUID 강제 안 함.
    task_id: str = Field(min_length=1, max_length=128)
    # v2 free string(minLength 1) — 새 status 값 silent pass.
    status: str = Field(min_length=1, max_length=32)
    failure_reason: str | None = Field(default=None, max_length=32)
    # exit_code/signal_no POSIX wait status 상호배타. signal_no Windows 항상 null.
    exit_code: int | None = None
    signal_no: int | None = None
    # 실제 설치 신호 — 데몬 기동+ZDM 등록 점검 결과. exit_code 보다 우선(True->success/False->failure). 미발행 시 None.
    task_policy: bool | None = None
    duration_ms: int = Field(ge=0)
    stdout_tail: str = Field(max_length=8192)
    stderr_tail: str = Field(max_length=8192)
    completed_at: datetime
