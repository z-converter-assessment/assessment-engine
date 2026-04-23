from dataclasses import dataclass, field


@dataclass
class DiskInfo:
    name: str
    size: str


@dataclass
class ServerItem:
    id: int
    hostname: str
    updated_at: str
    nproc: int
    mem_total_mb: int
    disks: list[DiskInfo] = field(default_factory=list)
    ip_internal: list[str] = field(default_factory=list)
    ip_external: list[str] = field(default_factory=list)


@dataclass
class MetricHistoryItem:
    recorded_at: str
    nproc: int
    mem_total_mb: int
    disks: list[DiskInfo] = field(default_factory=list)
    ip_internal: list[str] = field(default_factory=list)
    ip_external: list[str] = field(default_factory=list)