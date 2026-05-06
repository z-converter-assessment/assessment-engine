from dataclasses import dataclass
from datetime import datetime


@dataclass
class ServerInventoryCreate:
    machine_id: str
    hostname: str
    agent_version: str
    collected_at: datetime
    os_id: str | None
    os_version: str | None
    os_codename: str | None
    kernel_version: str | None
    cpu_cores: int | None
    cpu_model: str | None
    mem_total_kb: int | None
    swap_total_kb: int | None
    boot_time: datetime | None
    ip_internal: list[str]
    ip_external: list[str] | None
    disks: list[dict]         # [{name, size_bytes, type}]
    mounts: list[dict]        # [{mount, fstype, total_bytes}]
    services: list[dict] | None   # [{unit, sub}] | null (non-systemd host)
    listen_ports: list[dict]  # [{proto, addr, port, uid, pid, comm}]


@dataclass
class ServerMetricCreate:
    collected_at: datetime
    cpu_user: int | None
    cpu_nice: int | None
    cpu_system: int | None
    cpu_idle: int | None
    cpu_iowait: int | None
    cpu_irq: int | None
    cpu_softirq: int | None
    cpu_steal: int | None
    mem_total_kb: int | None
    mem_free_kb: int | None
    mem_available_kb: int | None
    mem_buffers_kb: int | None
    mem_cached_kb: int | None
    swap_total_kb: int | None
    swap_free_kb: int | None
    load_1m: float | None
    load_5m: float | None
    load_15m: float | None
    disk_io: list[dict]   # [{device, reads_completed, writes_completed, sectors_read, sectors_written}]
    mounts: list[dict]    # [{mount, total_bytes, free_bytes, avail_bytes}]
    net_io: list[dict]    # [{interface, rx_bytes, tx_bytes, rx_packets, tx_packets, rx_errors, tx_errors}]