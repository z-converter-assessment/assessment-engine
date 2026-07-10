"""메트릭 표시 ViewModel — dashboard snapshot + collection status + 시계열 항목."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class CpuSnapshot:
    usage_pct: float | None
    user_pct: float | None
    system_pct: float | None
    iowait_pct: float | None
    steal_pct: float | None = None  # 가상화 경합 — 하이퍼바이저가 vCPU 시간 뺏김 (Linux 전용, Windows null)


@dataclass
class MemSnapshot:
    total_bytes: int | None
    used_bytes: int | None  # total - available
    available_bytes: int | None
    cached_bytes: int | None
    buffered_bytes: int | None
    usage_pct: float | None
    # stacked bar 표시용 비율 (P5: 클라이언트 재계산 금지, metrics_calculator 산출).
    # 메모리 구성 모델(_METRIC_EXPR): Used + Available = 100, Cached/Buffers 는 Available 안 회수 가능 세부.
    # bar 구획 = used(usage_pct) | cached_pct | buffers_pct | free_pct, 합 = 100.
    cached_pct: float | None = None
    buffers_pct: float | None = None
    free_pct: float | None = None  # Available 중 cached/buffers 제외 잔여 — bar 마지막 구획


@dataclass
class DiskIoSnapshot:
    device: str
    read_iops: float | None
    write_iops: float | None
    read_kbps: float | None
    write_kbps: float | None


@dataclass
class NetIoSnapshot:
    interface: str
    rx_kbps: float | None
    tx_kbps: float | None
    rx_pps: float | None
    tx_pps: float | None


@dataclass
class MountDashSnapshot:
    mount: str
    total_gb: float | None
    used_gb: float | None
    avail_gb: float | None
    usage_pct: float | None


@dataclass
class MetricDashboard:
    collected_at: datetime | None
    cpu: CpuSnapshot | None
    cpu_run_queue: float | None  # 실행 큐 gauge (Linux procs_running / Windows Processor Queue) — os-aware 포화
    memory: MemSnapshot | None
    # 디스크 I/O 스냅샷 — device 단일 리스트. v2 시계열(server_disk_io)에 device type 축이 없어 물리/LVM/파티션
    # 분류가 불가(인벤토리 조인 없이). 표시는 전체 device flat (차트 물리필터도 현재 no-op 이라 정합).
    disk_io: list[DiskIoSnapshot]
    net_io: list[NetIoSnapshot]
    mounts: list[MountDashSnapshot]
    # 포화 신호 (latest_saturation 재사용, os-aware 표시) — cpu_run_queue(위)와 함께 스냅샷 포화 축.
    disk_await_ms: float | None = None  # 디스크 응답 지연 (op_time 델타, 양 OS).
    disk_queue: float | None = None  # 디스크 큐 깊이 gauge (pending_ops, await 폴백).
    # 하드폴트(major fault) rate — Linux refault / Windows Pages Input 통일 (paging_major_rate). 메모리 압박 축.
    mem_pages_input_rate: float | None = None
    net_retrans_pct: float | None = None  # TCP 재전송율 % (양 OS) — 네트워크 포화(1% 이상 성능 영향)


@dataclass
class CollectionStatusItem:
    last_metric_at: datetime | None
    last_inventory_at: datetime | None
    is_online: bool


@dataclass
class MetricSeriesItem:
    collected_at: datetime
    value: float | None
    dimension: str | None
