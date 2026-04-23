from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class MessageBase(BaseModel):
    agent_version: str = Field(min_length=1, max_length=32)
    collected_at: datetime
    hostname: str = Field(min_length=1, max_length=255)
    message_id: UUID


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------

class DiskInfo(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    size_bytes: int = Field(ge=0)


class InventoryDiskUsage(BaseModel):
    mount: str = Field(min_length=1, max_length=255)
    total_mb: int = Field(ge=0)
    used_mb: int = Field(ge=0)
    avail_mb: int = Field(ge=0)


class InventoryInput(MessageBase):
    message_type: Literal["inventory"]

    # Required — syscall/파일 보장
    machine_id: str = Field(min_length=1, max_length=64)
    kernel_version: str = Field(min_length=1, max_length=64)
    cpu_cores: int = Field(gt=0)
    mem_total_mb: int = Field(gt=0)

    # Optional — 환경에 따라 수집 불가
    os_id: str | None = Field(default=None, max_length=64)
    os_version: str | None = Field(default=None, max_length=64)
    cpu_model: str | None = Field(default=None, max_length=255)
    ip_external: list[str] | None = None  # None=수집 실패, []=external IP 없음

    # Empty-OK — 빈 배열도 유효
    ip_internal: list[str] = Field(default_factory=list)
    disks: list[DiskInfo] = Field(default_factory=list)
    disk_usage: list[InventoryDiskUsage] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

class MetricsDiskUsage(BaseModel):
    mount: str = Field(min_length=1, max_length=255)
    usage_pct: float = Field(ge=0.0, le=100.0)


class MetricsInput(MessageBase):
    message_type: Literal["metrics"]

    # Required — 모든 타겟 커널에서 보장
    cpu_user_pct: float = Field(ge=0.0, le=100.0)
    cpu_system_pct: float = Field(ge=0.0, le=100.0)
    cpu_iowait_pct: float = Field(ge=0.0, le=100.0)
    mem_used_mb: int = Field(ge=0)
    swap_used_mb: int = Field(ge=0)
    load_1m: float = Field(ge=0.0)

    # Optional — CentOS 7.0~7.1 fallback 실패 or 기동 직후 delta 없음
    mem_available_mb: int | None = Field(default=None, ge=0)
    disk_read_iops: int | None = Field(default=None, ge=0)
    disk_write_iops: int | None = Field(default=None, ge=0)
    disk_read_bytes: int | None = Field(default=None, ge=0)
    disk_write_bytes: int | None = Field(default=None, ge=0)
    net_rx_bytes: int | None = Field(default=None, ge=0)
    net_tx_bytes: int | None = Field(default=None, ge=0)

    # Empty-OK
    disk_usage: list[MetricsDiskUsage] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# error
# ---------------------------------------------------------------------------

class ErrorInput(MessageBase):
    message_type: Literal["error"]
    error_code: str = Field(min_length=1, max_length=64)
    error_message: str = Field(min_length=1)
    failed_component: Literal["collect", "publish"]
    timestamp: datetime


# ---------------------------------------------------------------------------
# discriminated union — 핸들러에서 routing key 별 분기 전 타입 안전 파싱용
# ---------------------------------------------------------------------------

AgentMessage = Annotated[
    InventoryInput | MetricsInput | ErrorInput,
    Field(discriminator="message_type"),
]