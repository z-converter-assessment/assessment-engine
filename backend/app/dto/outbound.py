from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass
class ServerDTO:
    id: UUID
    hostname: str
    created_at: datetime
    updated_at: datetime


@dataclass
class ServerMetricDTO:
    id: UUID
    server_id: UUID
    recorded_at: datetime
    created_at: datetime
    nproc: int
    mem_total_mb: int
    disks: list
    ip_internal: list
    ip_external: list