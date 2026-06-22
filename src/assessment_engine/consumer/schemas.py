from datetime import datetime
from ipaddress import ip_interface
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MessageBase(BaseModel):
    # 계약 진화 정책 (#B) — `extra=ignore`로 agent가 새 필드 추가해도 엔진은 통과·무시. 자식 클래스 상속.
    model_config = ConfigDict(extra="ignore")

    composite_id: str = Field(min_length=1, max_length=64)
    # machine_id — raw machine-id, 표시 전용 (식별·라우팅은 composite_id).
    machine_id: str | None = Field(default=None, max_length=64)
    agent_version: str = Field(min_length=1, max_length=32)
    collected_at: datetime
    hostname: str = Field(min_length=1, max_length=255)
    message_id: UUID
    agent_started_at: datetime
    boot_time: datetime
    # OS family — 모든 메시지 진입 시점 OS 분기 단일 진실. nullable — task.result Linux worker 미발행 비대칭 흡수.
    os_family: Literal["linux", "windows"] | None = None


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


class InventoryMountInfo(BaseModel):
    mount: str = Field(min_length=1, max_length=255)
    total_bytes: int | None = Field(default=None, ge=0)
    free_bytes: int | None = Field(default=None, ge=0)
    avail_bytes: int | None = Field(default=None, ge=0)
    fstype: str | None = Field(default=None, max_length=32)
    major: int | None = Field(default=None, ge=0)
    minor: int | None = Field(default=None, ge=0)


class InventoryServiceInfo(BaseModel):
    unit: str = Field(min_length=1, max_length=255)
    sub: str = Field(min_length=1, max_length=64)


class InventoryListenPortInfo(BaseModel):
    proto: Literal["tcp", "tcp6", "udp", "udp6"]
    addr: str = Field(min_length=1, max_length=64)
    port: int = Field(ge=1, le=65535)
    # Windows agent 는 POSIX uid 미존재로 null 발행 — nullable (Linux 만 값). #B 플랫폼 차이.
    uid: int | None = Field(default=None, ge=0)
    pid: int | None = None
    comm: str | None = Field(default=None, max_length=64)


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

    ip_internal: list[str] = Field(default_factory=list)
    ip_external: list[str] | None = None

    @field_validator("ip_internal", "ip_external", mode="before")
    @classmethod
    def validate_ip_list(cls, v: object) -> object:
        # mode="before" — JSON 파싱 직후 임의 타입. list/tuple만 허용.
        # ip_interface 는 bare IP 와 CIDR 둘 다 수용 — agent 가 ip_internal 을 CIDR 로 발행(#B).
        if v is None:
            return v
        if not isinstance(v, (list, tuple)):
            raise TypeError(f"expected list or tuple of IP strings, got {type(v).__name__}")
        for item in v:
            ip_interface(str(item))
        return v

    # mac_addresses — NIC별 MAC 목록. clone collision 감사용 raw 보존 (식별은 composite_id).
    mac_addresses: list[str] = Field(default_factory=list)

    disks: list[DiskInfo] = Field(default_factory=list)
    mounts: list[InventoryMountInfo] = Field(default_factory=list)
    services: list[InventoryServiceInfo] | None = None
    listen_ports: list[InventoryListenPortInfo] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


class CpuStat(BaseModel):
    user: int = Field(ge=0)
    nice: int = Field(ge=0)
    system: int = Field(ge=0)
    idle: int = Field(ge=0)
    iowait: int = Field(ge=0)
    irq: int = Field(ge=0)
    softirq: int = Field(ge=0)
    steal: int = Field(ge=0)


class DiskIoInfo(BaseModel):
    device: str = Field(min_length=1, max_length=128)  # Windows 디스크 이름 여유 (방어)
    reads_completed: int | None = Field(default=None, ge=0)
    writes_completed: int | None = Field(default=None, ge=0)
    sectors_read: int | None = Field(default=None, ge=0)
    sectors_written: int | None = Field(default=None, ge=0)
    major: int | None = Field(default=None, ge=0)
    minor: int | None = Field(default=None, ge=0)


class MetricsMountInfo(BaseModel):
    mount: str = Field(min_length=1, max_length=255)
    total_bytes: int | None = Field(default=None, ge=0)
    free_bytes: int | None = Field(default=None, ge=0)
    avail_bytes: int | None = Field(default=None, ge=0)
    major: int | None = Field(default=None, ge=0)
    minor: int | None = Field(default=None, ge=0)


class NetIoInfo(BaseModel):
    # Windows 인터페이스는 WFP/QoS 필터 드라이버 체인 이름이라 매우 김 (NDIS 한계 256). raw 수용.
    interface: str = Field(min_length=1, max_length=256)
    rx_bytes: int | None = Field(default=None, ge=0)
    tx_bytes: int | None = Field(default=None, ge=0)
    rx_packets: int | None = Field(default=None, ge=0)
    tx_packets: int | None = Field(default=None, ge=0)
    rx_errors: int | None = Field(default=None, ge=0)
    tx_errors: int | None = Field(default=None, ge=0)


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

    공통 메타(composite_id 등)는 MessageBase. composite_id / boot_time / agent_started_at은
    본 메시지에서는 null 가능 (수집 캐시와 분리된 worker 컨텍스트에서 발행 — composite_id 미산출) —
    부모 required 필드를 nullable로 override. 결과 매칭은 task_id 로 하므로 composite_id 불필요.
    """

    message_type: Literal["task.result"]
    composite_id: str | None = Field(default=None, min_length=1, max_length=64)
    boot_time: datetime | None = None
    agent_started_at: datetime | None = None

    task_id: UUID
    status: Literal["success", "failure"]
    # 실패 분류 문자열 (성공 시 null). 새 enum 은 silent pass — extra=ignore 정신, max_length만 강제.
    failure_reason: str | None = Field(default=None, max_length=32)
    exit_code: int | None = None
    # OS 버전 식별자 (Windows worker = CurrentBuildNumber, 예 "20348"). 성공 exit code 보정
    # 정책(task_policy.effective_task_result)의 키. Linux worker 미발행이라 nullable.
    os_version: str | None = Field(default=None, max_length=64)
    duration_ms: int = Field(ge=0)
    # 8192 cap 은 over-provision (agent wire 상한 4 KB). minor bump 로 tail 늘어도 무수정 흡수 (#B).
    stdout_tail: str = Field(max_length=8192)
    stderr_tail: str = Field(max_length=8192)
    completed_at: datetime
