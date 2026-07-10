"""Outbound raw DTO → Dashboard ViewModel — delta 기반 percent/rate 계산.

원칙:
- raw 누적 카운터 2시점 페어로 delta 계산 (CPU, disk_io, net_io)
- counter reset 식별 우선순위:
  1) 두 시점의 boot_time이 다르면 시스템 재부팅 → delta 계산 건너뛰기 (None)
     agent_started_at만 다르면 에이전트 재시작이고 카운터는 그대로라 정상 계산
  2) boot_time 둘 다 NULL(child 시계열)이면 d < 0 휴리스틱 fallback (CLAUDE.md #C1)
- 시점 값은 그대로 변환 (mem, mount usage) — reset 무관. 단위는 By (v2).
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
from assessment_engine.web.services.device_filters import is_data_volume
from assessment_engine.web.services.unit_converter import bytes_to_gb, usage_pct
from assessment_engine.web.view_models.metric import (
    CpuSnapshot,
    DiskIoSnapshot,
    MemSnapshot,
    MetricDashboard,
    MountDashSnapshot,
    NetIoSnapshot,
)

# ─── 공통 helper ──────────────────────────────────────────────────────────


def _group_by_dim[T](rows: list[T], key: Callable[[T], str]) -> dict[str, list[T]]:
    """raw 시계열 행을 dimension(device_id·iface_id 등)별로 묶는다."""
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


def _delta_kbps(cur: int | None, prev: int | None, dt: float) -> float | None:
    """누적 byte 카운터 두 시점의 처리량 (kB/s). io_*_bytes·rx/tx_bytes 는 By 단위 -> /1024.

    nullable raw 라 None 가드 (io_*_bytes/rx/tx_bytes 는 nullable).
    """
    if cur is None or prev is None:
        return None
    d = cur - prev
    if d < 0:
        return None
    return round(d / 1024 / dt, 1)


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

    # 실행 큐는 코어당 정규화(포화 임계 '코어당 1 이상'과 정합, 보고서·환경 집계와 동일 기준, P2 서버측 파생).
    run_queue_per_core = (
        round(cur.cpu_run_queue / cur.cpu_logical_count, 2)
        if (cur and cur.cpu_run_queue is not None and cur.cpu_logical_count)
        else None
    )
    return MetricDashboard(
        collected_at=cur.collected_at if cur else None,
        cpu=compute_cpu(cur, prev),
        cpu_run_queue=run_queue_per_core,
        memory=compute_mem(cur),
        disk_io=compute_disk_io(raw.disk_io),
        net_io=compute_net_io(raw.net_io),
        mounts=compute_mounts(raw.filesystems),
    )


# ─── CPU ──────────────────────────────────────────────────────────────────


def compute_cpu(cur: MetricPairRaw | None, prev: MetricPairRaw | None) -> CpuSnapshot | None:
    if cur is None:
        return None

    def cpu_total(r: MetricPairRaw) -> float:
        # Windows 는 nice/iowait/irq/softirq/steal 이 null (OS 개념 부재) — None->0 정규화(#C2 SQL COALESCE 와 동일).
        # Windows total = user+system+idle (GetSystemTimes 전체 스케줄러 시간과 일치). cpu_stat 전부 부재면 0 ->
        # delta<=0 로 자연히 N/A. 성분 하나가 null 이라고 total 을 null 로 만들면 Windows CPU 가 항상 N/A 가 된다.
        vals = [
            r.cpu_user_s, r.cpu_nice_s, r.cpu_system_s, r.cpu_idle_s,
            r.cpu_iowait_s, r.cpu_irq_s, r.cpu_softirq_s, r.cpu_steal_s,
        ]
        return sum(v for v in vals if v is not None)

    if prev is None:
        return CpuSnapshot(usage_pct=None, user_pct=None, system_pct=None, iowait_pct=None)

    # 시스템 재부팅 → cpu 누적 시간 0으로 리셋 → delta 계산 무의미.
    if is_counter_reset(cur.boot_time, prev.boot_time):
        return CpuSnapshot(usage_pct=None, user_pct=None, system_pct=None, iowait_pct=None)

    delta_total = cpu_total(cur) - cpu_total(prev)
    if delta_total <= 0:
        return CpuSnapshot(usage_pct=None, user_pct=None, system_pct=None, iowait_pct=None)

    def pct(c: float | None, p: float | None) -> float | None:
        if c is None or p is None:
            return None
        return round(max(0.0, (c - p) / delta_total * 100), 1)

    idle_pct = pct(cur.cpu_idle_s, prev.cpu_idle_s)
    return CpuSnapshot(
        usage_pct=round(max(0.0, 100.0 - idle_pct), 1) if idle_pct is not None else None,
        user_pct=pct(cur.cpu_user_s, prev.cpu_user_s),
        system_pct=pct(cur.cpu_system_s, prev.cpu_system_s),
        iowait_pct=pct(cur.cpu_iowait_s, prev.cpu_iowait_s),
        steal_pct=pct(cur.cpu_steal_s, prev.cpu_steal_s),
    )


# ─── Memory (시점 값, By) ──────────────────────────────────────────────────


def compute_mem(cur: MetricPairRaw | None) -> MemSnapshot | None:
    if cur is None or cur.mem_limit_bytes is None:
        return None

    # used = limit - available (mem_used_bytes 존재하나 스택바 invariant used+available=100 보존 위해 limit-available.
    # mem_used_bytes 는 total-free-buff/cache 라 limit-available 과 불일치 -> bar 합 100 붕괴).
    # max(0,...) 클램프 — cgroup memory.limit < 호스트 MemAvailable 인 컨테이너에서 음수 방지.
    used = max(0, cur.mem_limit_bytes - cur.mem_available_bytes) if cur.mem_available_bytes is not None else None
    used_pct = usage_pct(used, cur.mem_limit_bytes)

    # 정의서 메모리 구성 모델(types._ENV_SCALAR_WEIGHTED): Used + Available = 100 (서로 겹치지 않는 두 축),
    # Cached/Buffers 는 Available(회수 가능) 영역 안의 세부. 따라서 used 위 남은 공간(=available_pct)
    # 안에서만 cached -> buffers 순으로 표시하고, 그 잔여를 free 로 채워 bar 합을 정확히 100 으로 맞춘다.
    remaining_after_used = 100.0 - (used_pct or 0.0)  # = available_pct
    cached_pct = _clip_to_remaining(
        usage_pct(cur.mem_cached_bytes, cur.mem_limit_bytes),
        remaining_after_used,
    )
    remaining_after_cached = remaining_after_used - (cached_pct or 0.0)
    buffers_pct = _clip_to_remaining(
        usage_pct(cur.mem_buffered_bytes, cur.mem_limit_bytes),
        remaining_after_cached,
    )
    free_pct = round(max(0.0, remaining_after_cached - (buffers_pct or 0.0)), 1)

    return MemSnapshot(
        total_bytes=cur.mem_limit_bytes,
        used_bytes=used,
        available_bytes=cur.mem_available_bytes,
        cached_bytes=cur.mem_cached_bytes,
        buffered_bytes=cur.mem_buffered_bytes,
        usage_pct=used_pct,
        cached_pct=cached_pct,
        buffers_pct=buffers_pct,
        free_pct=free_pct,
    )


# ─── Disk I/O / Net I/O — 누적 카운터 페어 → rate ─────────────────────────


def compute_disk_io(pairs: list[DiskIoRaw]) -> list[DiskIoSnapshot]:
    """device_id별로 그룹 → 페어 rate. v2 는 device type 축 부재라 물리/LVM/파티션 분류 없이 전체 flat."""
    by_device = _group_by_dim(pairs, key=lambda r: r.device_id)
    return [_disk_io_snapshot(rows) for _did, rows in sorted(by_device.items())]


def _disk_io_snapshot(rows: list[DiskIoRaw]) -> DiskIoSnapshot:
    display = rows[0].device_name or rows[0].device_id
    if len(rows) < 2:
        return DiskIoSnapshot(device=display, read_iops=None, write_iops=None, read_kbps=None, write_kbps=None)
    cur, prev = rows[0], rows[1]
    dt = (cur.collected_at - prev.collected_at).total_seconds()
    if dt <= 0:
        return DiskIoSnapshot(device=display, read_iops=None, write_iops=None, read_kbps=None, write_kbps=None)
    # 시스템 재부팅 → 디스크 I/O 카운터 리셋 → delta 무의미. child 시계열은 boot_time null -> d<0 fallback.
    if is_counter_reset(cur.boot_time, prev.boot_time):
        return DiskIoSnapshot(device=display, read_iops=None, write_iops=None, read_kbps=None, write_kbps=None)
    return DiskIoSnapshot(
        device=display,
        read_iops=_delta_rate(cur.ops_read, prev.ops_read, dt),
        write_iops=_delta_rate(cur.ops_write, prev.ops_write, dt),
        read_kbps=_delta_kbps(cur.io_read_bytes, prev.io_read_bytes, dt),
        write_kbps=_delta_kbps(cur.io_write_bytes, prev.io_write_bytes, dt),
    )


def compute_net_io(pairs: list[NetIoRaw]) -> list[NetIoSnapshot]:
    """iface_id별 그룹 -> 페어 rate. v2 NetIoRaw 에 kind 축 부재라 IF 전체 노출(가상 필터는 inventory 조인)."""
    by_iface = _group_by_dim(pairs, key=lambda r: r.iface_id)
    return [_net_io_snapshot(rows) for _iid, rows in sorted(by_iface.items())]


def _net_io_snapshot(rows: list[NetIoRaw]) -> NetIoSnapshot:
    display = rows[0].iface_name or rows[0].iface_id
    if len(rows) < 2:
        return NetIoSnapshot(interface=display, rx_kbps=None, tx_kbps=None, rx_pps=None, tx_pps=None)
    cur, prev = rows[0], rows[1]
    dt = (cur.collected_at - prev.collected_at).total_seconds()
    if dt <= 0:
        return NetIoSnapshot(interface=display, rx_kbps=None, tx_kbps=None, rx_pps=None, tx_pps=None)
    # 시스템 재부팅 → 네트워크 카운터 리셋 → delta 무의미.
    if is_counter_reset(cur.boot_time, prev.boot_time):
        return NetIoSnapshot(interface=display, rx_kbps=None, tx_kbps=None, rx_pps=None, tx_pps=None)
    return NetIoSnapshot(
        interface=display,
        rx_kbps=_delta_kbps(cur.rx_bytes, prev.rx_bytes, dt),
        tx_kbps=_delta_kbps(cur.tx_bytes, prev.tx_bytes, dt),
        rx_pps=_delta_rate(cur.rx_packets, prev.rx_packets, dt),
        tx_pps=_delta_rate(cur.tx_packets, prev.tx_packets, dt),
    )


# ─── Mount usage (시점 값 + 가상 마운트 필터) ─────────────────────────────


def compute_mounts(mounts: list[MountUsageRaw]) -> list[MountDashSnapshot]:
    result: list[MountDashSnapshot] = []
    for m in sorted(mounts, key=lambda x: x.mountpoint):
        if not is_data_volume(m.fstype, m.mountpoint):
            continue
        total = (m.used_bytes + m.free_bytes) if (m.used_bytes is not None and m.free_bytes is not None) else None
        result.append(
            MountDashSnapshot(
                mount=m.mountpoint,
                total_gb=bytes_to_gb(total),
                used_gb=bytes_to_gb(m.used_bytes),
                avail_gb=bytes_to_gb(m.free_bytes),
                usage_pct=usage_pct(m.used_bytes, total),
            )
        )
    return result
