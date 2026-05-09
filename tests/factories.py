"""테스트 데이터 빌더. factory_boy 같은 무거운 라이브러리 대신 단순 함수.

본 프로젝트 규모(단일 도메인·dataclass DTO)에는 함수 빌더가 정석:
- 명시적인 default
- 파라미터로 일부 필드만 override
- factory_boy의 SubFactory/LazyAttribute 같은 추상화는 과도
"""
from datetime import datetime, timezone

from assessment_engine.db.repositories.inbound import (
    DiskIoEntry,
    MountUsageEntry,
    NetIoEntry,
    ServerInventoryCreate,
    ServerMetricCreate,
)

_DEFAULT_BOOT_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DEFAULT_AGENT_STARTED_AT = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)


def make_inventory(
    *,
    machine_id: str = "test-machine-id-0001",
    hostname: str = "test-host-01",
    agent_version: str = "1.0.0",
    collected_at: datetime | None = None,
    cpu_cores: int | None = 4,
    mem_total_kb: int | None = 8 * 1024 * 1024,
    boot_time: datetime | None = _DEFAULT_BOOT_TIME,
    agent_started_at: datetime | None = _DEFAULT_AGENT_STARTED_AT,
    disks: list[dict] | None = None,
    mounts: list[dict] | None = None,
    services: list[dict] | None = None,
    listen_ports: list[dict] | None = None,
) -> ServerInventoryCreate:
    """기본값은 placeholder가 아닌 '정상' inventory — 미지정 시 실제와 유사한 값."""
    return ServerInventoryCreate(
        machine_id=machine_id,
        hostname=hostname,
        agent_version=agent_version,
        collected_at=collected_at or datetime.now(timezone.utc),
        os_id="ubuntu",
        os_version="22.04",
        os_codename="jammy",
        kernel_version="5.15.0",
        cpu_cores=cpu_cores,
        cpu_model="test-cpu",
        mem_total_kb=mem_total_kb,
        swap_total_kb=2 * 1024 * 1024,
        boot_time=boot_time,
        agent_started_at=agent_started_at,
        ip_internal=["10.0.0.1"],
        ip_external=None,
        disks=disks if disks is not None else [
            {"name": "sda", "size_bytes": 100 * 10**9, "type": "disk", "major": 8, "minor": 0},
        ],
        mounts=mounts if mounts is not None else [
            {"mount": "/", "fstype": "ext4", "total_bytes": 50 * 10**9, "major": 8, "minor": 1},
        ],
        services=services,
        listen_ports=listen_ports if listen_ports is not None else [],
    )


def make_metrics(
    *,
    collected_at: datetime,
    boot_time: datetime | None = _DEFAULT_BOOT_TIME,
    agent_started_at: datetime | None = _DEFAULT_AGENT_STARTED_AT,
    cpu_user: int = 1000,
    cpu_nice: int = 0,
    cpu_system: int = 200,
    cpu_idle: int = 8000,
    cpu_iowait: int = 50,
    cpu_irq: int = 0,
    cpu_softirq: int = 0,
    cpu_steal: int = 0,
    mem_total_kb: int = 8 * 1024 * 1024,
    mem_free_kb: int = 4 * 1024 * 1024,
    mem_available_kb: int = 5 * 1024 * 1024,
    mem_buffers_kb: int = 200 * 1024,
    mem_cached_kb: int = 1 * 1024 * 1024,
    swap_total_kb: int = 2 * 1024 * 1024,
    swap_free_kb: int = 2 * 1024 * 1024,
    load_1m: float = 0.5,
    load_5m: float = 0.4,
    load_15m: float = 0.3,
    disk_io: list[DiskIoEntry] | None = None,
    mounts: list[MountUsageEntry] | None = None,
    net_io: list[NetIoEntry] | None = None,
) -> ServerMetricCreate:
    """raw 누적값. 시간 흐름 시뮬은 호출자가 collected_at + 누적 카운터 증가로."""
    return ServerMetricCreate(
        collected_at=collected_at,
        boot_time=boot_time,
        agent_started_at=agent_started_at,
        cpu_user=cpu_user, cpu_nice=cpu_nice, cpu_system=cpu_system,
        cpu_idle=cpu_idle, cpu_iowait=cpu_iowait, cpu_irq=cpu_irq,
        cpu_softirq=cpu_softirq, cpu_steal=cpu_steal,
        mem_total_kb=mem_total_kb, mem_free_kb=mem_free_kb,
        mem_available_kb=mem_available_kb, mem_buffers_kb=mem_buffers_kb,
        mem_cached_kb=mem_cached_kb, swap_total_kb=swap_total_kb, swap_free_kb=swap_free_kb,
        load_1m=load_1m, load_5m=load_5m, load_15m=load_15m,
        disk_io=disk_io if disk_io is not None else [
            DiskIoEntry(device="sda", reads_completed=100, writes_completed=50,
                        sectors_read=2000, sectors_written=1000),
        ],
        mounts=mounts if mounts is not None else [
            MountUsageEntry(mount="/", total_bytes=50 * 10**9,
                            free_bytes=20 * 10**9, avail_bytes=18 * 10**9),
        ],
        net_io=net_io if net_io is not None else [
            NetIoEntry(interface="eth0", rx_bytes=1_000_000, tx_bytes=500_000,
                       rx_packets=1000, tx_packets=500, rx_errors=0, tx_errors=0),
        ],
    )