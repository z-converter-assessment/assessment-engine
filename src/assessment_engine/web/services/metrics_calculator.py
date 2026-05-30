"""Outbound raw DTO → Dashboard ViewModel — delta 기반 percent/rate 계산.

원칙:
- raw 누적 카운터 2시점 페어로 delta 계산 (CPU, disk_io, net_io)
- counter reset 식별 우선순위:
  1) 두 시점의 boot_time이 다르면 시스템 재부팅 → delta 계산 건너뛰기 (None)
     agent_started_at만 다르면 에이전트 재시작이고 /proc 카운터는 그대로라 정상 계산
  2) boot_time 둘 다 NULL(옛 데이터)이면 d < 0 휴리스틱 fallback (CLAUDE.md #C1)
- 시점 값은 그대로 변환 (mem, swap, mount usage) — reset 무관
"""

from collections.abc import Callable

from assessment_engine.boot_time import is_counter_reset
from assessment_engine.db.dtos.outbound import (
    DashboardRaw,
    DiskIoRaw,
    MetricPairRaw,
    MountUsageRaw,
    NetIoRaw,
)
from assessment_engine.web.services.device_filters import (
    is_lvm_disk,
    is_partition,
    is_physical_disk,
    is_virtual_interface,
    is_virtual_mount,
)
from assessment_engine.web.services.unit_converter import bytes_to_gb, sector_to_kbps, usage_pct
from assessment_engine.web.view_models.metric import (
    CpuSnapshot,
    DiskIoSnapshot,
    MemSnapshot,
    MetricDashboard,
    MountDashSnapshot,
    NetIoSnapshot,
    SwapSnapshot,
)

# ─── 공통 helper ──────────────────────────────────────────────────────────


def _group_by_dim[T](rows: list[T], key: Callable[[T], str]) -> dict[str, list[T]]:
    """raw 시계열 행을 dimension(device·interface 등)별로 묶는다."""
    by_dim: dict[str, list[T]] = {}
    for r in rows:
        by_dim.setdefault(key(r), []).append(r)
    return by_dim


def _delta_rate(cur: int | None, prev: int | None, dt: float) -> float | None:
    """누적 카운터 두 시점의 시간당 변화율 (count/sec). counter reset(d<0) 시 None.

    호출자가 boot_time 비교로 reset을 미리 거른 경우 d<0은 거의 발생 안 함 (counter wrap-around 정도).
    """
    if cur is None or prev is None:
        return None
    d = cur - prev
    if d < 0:
        return None
    return round(d / dt, 1)


def _clip_to_remaining(raw_pct: float | None, remaining_room: float) -> float | None:
    """stacked bar 누적용 — raw 비율을 [0, remaining_room] 범위로 자른다.

    Linux available은 cached/buffers 일부를 포함하므로 단순 합산 시 100% 초과 가능.
    bar 시각화에서 used 위에 cached/buffers를 덧붙일 때 남은 공간만큼만 표시.
    """
    if raw_pct is None:
        return None
    return round(min(max(0.0, remaining_room), raw_pct), 1)


# ─── 진입점 ───────────────────────────────────────────────────────────────


def build_dashboard(raw: DashboardRaw) -> MetricDashboard:
    cur = raw.metrics[0] if raw.metrics else None
    prev = raw.metrics[1] if len(raw.metrics) >= 2 else None

    phys, lvm, part = compute_disk_io(raw.disk_io)
    return MetricDashboard(
        collected_at=cur.collected_at if cur else None,
        cpu=compute_cpu(cur, prev),
        load_1m=cur.load_1m if cur else None,
        load_5m=cur.load_5m if cur else None,
        load_15m=cur.load_15m if cur else None,
        memory=compute_mem(cur),
        swap=compute_swap(cur),
        disk_io_phys=phys,
        disk_io_lvm=lvm,
        disk_io_part=part,
        net_io=compute_net_io(raw.net_io),
        mounts=compute_mounts(raw.mounts),
    )


# ─── CPU ──────────────────────────────────────────────────────────────────


def compute_cpu(cur: MetricPairRaw | None, prev: MetricPairRaw | None) -> CpuSnapshot | None:
    if cur is None:
        return None

    def cpu_total(r: MetricPairRaw) -> int | None:
        vals = [r.cpu_user, r.cpu_nice, r.cpu_system, r.cpu_idle, r.cpu_iowait, r.cpu_irq, r.cpu_softirq, r.cpu_steal]
        return None if any(v is None for v in vals) else sum(vals)  # type: ignore[arg-type]

    if prev is None:
        return CpuSnapshot(usage_pct=None, user_pct=None, system_pct=None, iowait_pct=None)

    # 시스템 재부팅 → /proc/stat jiffies 0으로 리셋 → delta 계산 무의미.
    if is_counter_reset(cur.boot_time, prev.boot_time):
        return CpuSnapshot(usage_pct=None, user_pct=None, system_pct=None, iowait_pct=None)

    dt_total = cpu_total(cur)
    dp_total = cpu_total(prev)
    if dt_total is None or dp_total is None:
        return CpuSnapshot(usage_pct=None, user_pct=None, system_pct=None, iowait_pct=None)

    delta_total = dt_total - dp_total
    if delta_total <= 0:
        return CpuSnapshot(usage_pct=None, user_pct=None, system_pct=None, iowait_pct=None)

    def pct(c: int | None, p: int | None) -> float | None:
        if c is None or p is None:
            return None
        return round(max(0.0, (c - p) / delta_total * 100), 1)

    idle_pct = pct(cur.cpu_idle, prev.cpu_idle)
    return CpuSnapshot(
        usage_pct=round(max(0.0, 100.0 - idle_pct), 1) if idle_pct is not None else None,
        user_pct=pct(cur.cpu_user, prev.cpu_user),
        system_pct=pct(cur.cpu_system, prev.cpu_system),
        iowait_pct=pct(cur.cpu_iowait, prev.cpu_iowait),
    )


# ─── Memory / Swap (시점 값) ──────────────────────────────────────────────


def compute_mem(cur: MetricPairRaw | None) -> MemSnapshot | None:
    if cur is None or cur.mem_total_kb is None:
        return None

    used = (cur.mem_total_kb - cur.mem_available_kb) if cur.mem_available_kb is not None else None
    used_pct = usage_pct(used, cur.mem_total_kb)

    # stacked bar 누적: used 위에 cached, 그 위에 buffers. 남은 공간 안에서만 표시.
    remaining_after_used = 100.0 - (used_pct or 0.0)
    cached_pct = _clip_to_remaining(
        usage_pct(cur.mem_cached_kb, cur.mem_total_kb),
        remaining_after_used,
    )
    remaining_after_cached = remaining_after_used - (cached_pct or 0.0)
    buffers_pct = _clip_to_remaining(
        usage_pct(cur.mem_buffers_kb, cur.mem_total_kb),
        remaining_after_cached,
    )

    return MemSnapshot(
        total_kb=cur.mem_total_kb,
        used_kb=used,
        available_kb=cur.mem_available_kb,
        cached_kb=cur.mem_cached_kb,
        buffers_kb=cur.mem_buffers_kb,
        usage_pct=used_pct,
        cached_pct=cached_pct,
        buffers_pct=buffers_pct,
    )


def compute_swap(cur: MetricPairRaw | None) -> SwapSnapshot | None:
    if cur is None or not cur.swap_total_kb:
        return None
    used = (cur.swap_total_kb - cur.swap_free_kb) if cur.swap_free_kb is not None else None
    return SwapSnapshot(
        total_kb=cur.swap_total_kb,
        used_kb=used,
        usage_pct=usage_pct(used, cur.swap_total_kb),
    )


# ─── Disk I/O / Net I/O — 누적 카운터 페어 → rate ─────────────────────────


def compute_disk_io(
    pairs: list[DiskIoRaw],
) -> tuple[list[DiskIoSnapshot], list[DiskIoSnapshot], list[DiskIoSnapshot]]:
    """device별로 그룹 → 페어 rate → physical/lvm/partition 분류."""
    by_device = _group_by_dim(pairs, key=lambda r: r.device)

    phys: list[DiskIoSnapshot] = []
    lvm: list[DiskIoSnapshot] = []
    part: list[DiskIoSnapshot] = []

    for device, rows in sorted(by_device.items()):
        snap = _disk_io_snapshot(device, rows)
        if is_physical_disk(device):
            phys.append(snap)
        elif is_lvm_disk(device):
            lvm.append(snap)
        elif is_partition(device):
            part.append(snap)

    return phys, lvm, part


def _disk_io_snapshot(device: str, rows: list[DiskIoRaw]) -> DiskIoSnapshot:
    if len(rows) < 2:
        return DiskIoSnapshot(device=device, read_iops=None, write_iops=None, read_kbps=None, write_kbps=None)
    cur, prev = rows[0], rows[1]
    dt = (cur.collected_at - prev.collected_at).total_seconds()
    if dt <= 0:
        return DiskIoSnapshot(device=device, read_iops=None, write_iops=None, read_kbps=None, write_kbps=None)
    # 시스템 재부팅 → /proc/diskstats 카운터 리셋 → delta 무의미.
    if is_counter_reset(cur.boot_time, prev.boot_time):
        return DiskIoSnapshot(device=device, read_iops=None, write_iops=None, read_kbps=None, write_kbps=None)
    return DiskIoSnapshot(
        device=device,
        read_iops=_delta_rate(cur.reads_completed, prev.reads_completed, dt),
        write_iops=_delta_rate(cur.writes_completed, prev.writes_completed, dt),
        read_kbps=sector_to_kbps(cur.sectors_read, prev.sectors_read, dt),
        write_kbps=sector_to_kbps(cur.sectors_written, prev.sectors_written, dt),
    )


def compute_net_io(pairs: list[NetIoRaw]) -> list[NetIoSnapshot]:
    by_iface = _group_by_dim(pairs, key=lambda r: r.interface)
    # 표시 경계 필터 — 가상·시스템 인터페이스 제외 (저장은 유지). 차트(query_service)와 동일 정책.
    return [
        _net_io_snapshot(iface, rows)
        for iface, rows in sorted(by_iface.items())
        if not is_virtual_interface(iface)
    ]


def _net_io_snapshot(iface: str, rows: list[NetIoRaw]) -> NetIoSnapshot:
    if len(rows) < 2:
        return NetIoSnapshot(interface=iface, rx_kbps=None, tx_kbps=None, rx_pps=None, tx_pps=None)
    cur, prev = rows[0], rows[1]
    dt = (cur.collected_at - prev.collected_at).total_seconds()
    if dt <= 0:
        return NetIoSnapshot(interface=iface, rx_kbps=None, tx_kbps=None, rx_pps=None, tx_pps=None)
    # 시스템 재부팅 → /proc/net/dev 카운터 리셋 → delta 무의미.
    if is_counter_reset(cur.boot_time, prev.boot_time):
        return NetIoSnapshot(interface=iface, rx_kbps=None, tx_kbps=None, rx_pps=None, tx_pps=None)

    def brate(c: int, p: int) -> float | None:
        d = c - p
        return None if d < 0 else round(d / 1024 / dt, 1)

    return NetIoSnapshot(
        interface=iface,
        rx_kbps=brate(cur.rx_bytes, prev.rx_bytes),
        tx_kbps=brate(cur.tx_bytes, prev.tx_bytes),
        rx_pps=_delta_rate(cur.rx_packets, prev.rx_packets, dt),
        tx_pps=_delta_rate(cur.tx_packets, prev.tx_packets, dt),
    )


# ─── Mount usage (시점 값 + 가상 마운트 필터) ─────────────────────────────


def compute_mounts(mounts: list[MountUsageRaw]) -> list[MountDashSnapshot]:
    result: list[MountDashSnapshot] = []
    for m in sorted(mounts, key=lambda x: x.mount):
        if is_virtual_mount(None, m.mount):
            continue
        used_bytes = (m.total_bytes - m.avail_bytes) if (m.total_bytes and m.avail_bytes is not None) else None
        result.append(
            MountDashSnapshot(
                mount=m.mount,
                total_gb=bytes_to_gb(m.total_bytes),
                used_gb=bytes_to_gb(used_bytes),
                avail_gb=bytes_to_gb(m.avail_bytes),
                usage_pct=usage_pct(used_bytes, m.total_bytes),
            )
        )
    return result
