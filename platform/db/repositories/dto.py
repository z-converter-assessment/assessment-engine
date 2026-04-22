from dataclasses import dataclass
from datetime import datetime


@dataclass
class ServerDTO:
    id: int
    hostname: str
    created_at: datetime
    updated_at: datetime


@dataclass
class ServerMetricDTO:
    id: int
    server_id: int
    recorded_at: datetime
    created_at: datetime
    nproc: int
    mem_total_mb: int
    disks: list
    ip_internal: list
    ip_external: list