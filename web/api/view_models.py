from dataclasses import dataclass, field


@dataclass
class ServerItem:
    id: int
    hostname: str
    cpu_cores: int
    mem_total_mb: int
    updated_at: str
    cpu_user_pct: float
    mem_used_mb: int
    load_1m: float


@dataclass
class MetricHistoryItem:
    collected_at: str
    cpu_user_pct: float
    cpu_system_pct: float
    cpu_iowait_pct: float
    mem_used_mb: int
    swap_used_mb: int
    load_1m: float
    disk_usage: list = field(default_factory=list)