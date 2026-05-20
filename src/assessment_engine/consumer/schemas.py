from datetime import datetime
from ipaddress import ip_address
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MessageBase(BaseModel):
    # B4 계약 진화 정책 — `extra=ignore`로 agent가 새 필드 추가해도 엔진은 통과·무시. 자식 클래스 상속.
    model_config = ConfigDict(extra="ignore")

    machine_id: str = Field(min_length=1, max_length=64)
    agent_version: str = Field(min_length=1, max_length=32)
    collected_at: datetime
    hostname: str = Field(min_length=1, max_length=255)
    message_id: UUID
    agent_started_at: datetime
    boot_time: datetime


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------


class DiskInfo(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    size_bytes: int | None = Field(default=None, ge=0)
    type: str | None = Field(default=None, max_length=32)
    # Linux 디바이스 식별 표준 (POSIX). mount-disk 조인 키.
    # 옛 에이전트 호환 위해 옵셔널.
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
    uid: int = Field(ge=0)
    pid: int | None = None
    comm: str | None = Field(default=None, max_length=64)


class InventoryInput(MessageBase):
    message_type: Literal["inventory"]

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
        # mode="before" — Pydantic 검증 전이라 v는 임의 타입(JSON 파싱 직후). list/tuple만 허용.
        if v is None:
            return v
        if not isinstance(v, (list, tuple)):
            raise TypeError(f"expected list or tuple of IP strings, got {type(v).__name__}")
        for item in v:
            ip_address(str(item))
        return v

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
    device: str = Field(min_length=1, max_length=64)
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
    interface: str = Field(min_length=1, max_length=64)
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
    error_message: str = Field(min_length=1)
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

    공통 메타(machine_id 등)는 MessageBase. boot_time / agent_started_at은
    본 메시지에서는 항상 null (수집 캐시와 분리된 worker 컨텍스트에서 발행) —
    부모 required 필드를 nullable로 override.
    """

    message_type: Literal["task.result"]
    boot_time: datetime | None = None
    agent_started_at: datetime | None = None

    task_id: UUID
    status: Literal["success", "failure"]
    # 실패 분류. 알려진 값: url_not_allowed / download_failed / sha256_mismatch /
    # extract_failed / script_not_found / script_failed / script_timeout /
    # insufficient_disk / internal_error / already_done. 성공 시 null.
    # 새 enum 도입 시 silent pass — extra=ignore 정신과 일관, max_length만 강제.
    failure_reason: str | None = Field(default=None, max_length=32)
    exit_code: int | None = None
    duration_ms: int = Field(ge=0)
    stdout_tail: str = Field(max_length=8192)
    stderr_tail: str = Field(max_length=8192)
    completed_at: datetime
