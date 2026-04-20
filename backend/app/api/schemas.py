from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DiskInfo:
    name: str
    size: str


@dataclass
class ServerListItem:
    id: uuid.UUID
    hostname: str
    updated_at: str
    nproc: Optional[int]
    mem_total_mb: Optional[int]
    disks: list[DiskInfo] = field(default_factory=list)
    ip_internal: list[str] = field(default_factory=list)
    ip_external: list[str] = field(default_factory=list)


@dataclass
class ServerDetail:
    id: uuid.UUID
    hostname: str
    updated_at: str
    nproc: Optional[int]
    mem_total_mb: Optional[int]
    disks: list[DiskInfo] = field(default_factory=list)
    ip_internal: list[str] = field(default_factory=list)
    ip_external: list[str] = field(default_factory=list)


@dataclass
class MetricHistoryItem:
    recorded_at: str
    nproc: Optional[int]
    mem_total_mb: Optional[int]
    disks: list[DiskInfo] = field(default_factory=list)
    ip_internal: list[str] = field(default_factory=list)
    ip_external: list[str] = field(default_factory=list)