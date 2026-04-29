from __future__ import annotations
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ServerDomain:
    id: uuid.UUID
    hostname: str
    created_at: datetime
    updated_at: datetime


@dataclass
class ServerMetricDomain:
    id: uuid.UUID
    server_id: uuid.UUID
    recorded_at: datetime
    created_at: datetime
    nproc: Optional[int]
    mem_total_mb: Optional[int]
    disks: Optional[list]
    ip_internal: Optional[list]
    ip_external: Optional[list]
