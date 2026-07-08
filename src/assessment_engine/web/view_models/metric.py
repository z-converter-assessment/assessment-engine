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
    total_kb: int | None
    used_kb: int | None  # total - available
    available_kb: int | None
    cached_kb: int | None
    buffers_kb: int | None
    usage_pct: float | None
    # stacked bar 표시용 비율 (P5: 클라이언트 재계산 금지, metrics_calculator 산출).
    # 메모리 구성 모델(_METRIC_EXPR): Used + Available = 100, Cached/Buffers 는 Available 안 회수 가능 세부.
    # bar 구획 = used(usage_pct) | cached_pct | buffers_pct | free_pct, 합 = 100.
    cached_pct: float | None = None
    buffers_pct: float | None = None
    free_pct: float | None = None  # Available 중 cached/buffers 제외 잔여 — bar 마지막 구획


@dataclass
class SwapSnapshot:
    total_kb: int | None
    used_kb: int | None
    usage_pct: float | None


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
    load_1m: float | None
    load_5m: float | None
    load_15m: float | None
    cpu_run_queue: float | None  # 실행 큐 gauge (Linux procs_running / Windows Processor Queue) — os-aware 포화
    memory: MemSnapshot | None
    swap: SwapSnapshot | None
    disk_io_phys: list[DiskIoSnapshot]
    disk_io_lvm: list[DiskIoSnapshot]
    disk_io_part: list[DiskIoSnapshot]
    net_io: list[NetIoSnapshot]
    mounts: list[MountDashSnapshot]
    # 포화 신호 (latest_saturation 재사용, os-aware 표시) — cpu_run_queue(위)와 함께 스냅샷 포화 축.
    disk_await_ms: float | None = None  # Linux 디스크 응답 지연 (2행 델타). Windows 는 await 미발행 -> null.
    disk_queue: float | None = None  # Windows Avg Disk Queue Length gauge. Linux 는 null.
    mem_pages_input_rate: float | None = None  # Windows Pages Input/sec rate(하드폴트). Linux 는 null.
    mem_pageout: int | None = None  # Linux swap page-out 발생분 (pswpout 델타 >0 = 메모리 압박). Windows 는 null.
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
