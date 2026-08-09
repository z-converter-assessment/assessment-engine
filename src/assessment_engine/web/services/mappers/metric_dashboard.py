"""Outbound raw DTO -> Dashboard ViewModel — 누적 카운터 2시점 페어의 delta 기반 percent/rate 계산.

reset 판정은 `is_counter_reset`(boot_time) 단일 경유 — agent_started_at 만 달라진 에이전트 재시작은
/proc 카운터가 그대로라 정상 계산한다. boot_time 은 server_metrics 만 실어 child 시계열(disk_io·net_io)에서는
이 판정이 늘 False 이고 `_delta_*` 의 `d < 0` 가드가 거기서는 유일한 reset 방어다 — 중복으로 보고 지우면
재부팅 직후 rate 가 깨진다. 시점 값(mem·mount usage)은 reset 무관, raw 단위는 By.
"""

import re
from typing import TYPE_CHECKING

from assessment_engine.domain.boot_time import is_counter_reset
from assessment_engine.domain.right_sizing import (
    CONNTRACK_SATURATION_RATIO,
    CPU_PERCORE_HOLD_PCT,
    CPU_RUN_QUEUE_PER_CORE_SATURATION,
    CPU_STEAL_BIAS_PCT,
    DISK_QUEUE_PER_DISK_SATURATION,
    DISKIO_AWAIT_MS,
    NET_DROP_PCT,
    NET_RETRANS_PCT,
    PROCS_BLOCKED_DSTATE_SATURATION,
    PROCS_RUNNING_PER_CORE_SATURATION,
    WIN_PAGES_INPUT_SATURATION,
)
from assessment_engine.web.services.device_filters import (
    is_data_volume,
    is_physical_disk,
    is_virtual_interface,
)
from assessment_engine.web.services.unit_converter import bytes_to_gb, usage_pct
from assessment_engine.web.view_models.metric import (
    CpuCoreSnapshot,
    CpuSnapshot,
    DiskIoSnapshot,
    ErrorSignal,
    MemSnapshot,
    MetricDashboard,
    MountDashSnapshot,
    NetIoSnapshot,
    SaturationSignal,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from assessment_engine.db.dtos.outbound import (
        CpuCoreRaw,
        DashboardRaw,
        DiskIoRaw,
        ErrorFleetRaw,
        MetricPairRaw,
        MountUsageRaw,
        NetIoRaw,
        SaturationRaw,
    )
    from assessment_engine.json_types import JsonObject


PSI_STALL_DISPLAY_PCT = 10.0


def _group_by_dim[T](rows: list[T], key: Callable[[T], str]) -> dict[str, list[T]]:
    by_dim: dict[str, list[T]] = {}
    for r in rows:
        by_dim.setdefault(key(r), []).append(r)
    return by_dim


def _delta_rate(cur: int | None, prev: int | None, dt: float) -> float | None:
    if cur is None or prev is None:
        return None
    d = cur - prev
    if d < 0:
        return None
    return round(d / dt, 1)


def _delta_kbps(cur: int | None, prev: int | None, dt: float) -> float | None:
    if cur is None or prev is None:
        return None
    d = cur - prev
    if d < 0:
        return None
    return round(d / 1024 / dt, 1)


def _clip_to_remaining(raw_pct: float | None, remaining_room: float) -> float | None:
    """raw 비율을 [0, remaining_room] 으로 자른다 — Linux available 이 cached/buffers 를 일부 품어 단순 합산은 100% 초과."""
    if raw_pct is None:
        return None
    return round(min(max(0.0, remaining_room), raw_pct), 1)


def _composite_dev_id(node: JsonObject) -> str | None:
    """block_device/net_interface 노드 -> 시계열 조인 키 `{id_type}:{id}` (disk_io.device_id·net_io.iface_id 형식)."""
    did, dtype = node.get("id"), node.get("id_type")
    return f"{dtype}:{did}" if did and dtype else None


def _physical_dev_names(nodes: list[JsonObject] | None, keep: Callable[[JsonObject], bool]) -> dict[str, str] | None:
    """물리 계층 {조인키: 표시 이름} 맵 — keep(node) 통과분. 인벤토리 부재(None)면 필터 안 함.

    시계열 device_name/iface_name 이 null 이라 표시명은 인벤토리 name(vda·enp3s0)으로 해결한다.
    조인키는 `{id_type}:{id}` 와 `name:{name}` 을 둘 다 등록한다 — inventory id_type(Windows 디스크 gptid)과
    metric 컬렉터 device_id(perflib 는 gptid 미접근 시 name: 폴백)가 서로 다른 폴백 사슬을 골라 갈릴 수 있고
    (실측 win2025 disk_io `name:PhysicalDrive0` vs inventory `gptid:...`), 한쪽만 등록하면 물리 필터가 전체를
    드롭한다. 계약은 agent-data.md E절.
    """
    if not nodes:
        return None
    result: dict[str, str] = {}
    for n in nodes:
        if not keep(n):
            continue
        name = n.get("name")
        cid = _composite_dev_id(n)
        display: str | None = name or cid
        if display is None:
            continue
        if cid:
            result[cid] = display
        if name:
            result[f"name:{name}"] = display
    return result


def build_dashboard(raw: DashboardRaw) -> MetricDashboard:
    cur = raw.metrics[0] if raw.metrics else None
    prev = raw.metrics[1] if len(raw.metrics) >= 2 else None

    phys_disks = _physical_dev_names(raw.block_devices, lambda n: is_physical_disk(n.get("type")))
    phys_ifaces = _physical_dev_names(raw.net_interfaces, lambda n: not is_virtual_interface(n.get("kind")))

    mounts = compute_mounts(raw.filesystems)

    return MetricDashboard(
        collected_at=cur.collected_at if cur else None,
        cpu=compute_cpu(cur, prev),
        memory=compute_mem(cur),
        disk_io=compute_disk_io(raw.disk_io, phys_disks),
        net_io=compute_net_io(raw.net_io, phys_ifaces),
        mounts=mounts,
        disk_usage_pct=_aggregate_disk_usage(mounts),
        cpu_cores=compute_cpu_cores(raw.cpu_cores),
    )


def _aggregate_disk_usage(mounts: list[MountDashSnapshot]) -> float | None:
    total = sum((m.total_gb or 0) for m in mounts if m.total_gb)
    used = sum((m.used_gb or 0) for m in mounts if m.total_gb and m.used_gb is not None)
    return round(used / total * 100, 1) if total > 0 else None


def _psi_supported(kernel_version: str | None) -> bool | None:
    """PSI(Pressure Stall Info) 지원 여부 — Linux 4.20+ 만 발행. 커널 미상이면 None(판정 보류)."""
    if not kernel_version:
        return None
    m = re.match(r"(\d+)\.(\d+)", kernel_version)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2))) >= (4, 20)


def _psi_signal(key: str, label: str, psi_val: float | None, win: bool, supported: bool | None) -> SaturationSignal:
    """PSI 신호 — 구커널 확정(supported=False)은 Windows 와 같은 N/A 로 둔다("수집 대기" 오인 방지)."""
    if win:
        return SaturationSignal(key=key, label=label, state="not_applicable", na_reason="Windows 미지원")
    if supported is False:
        return SaturationSignal(
            key=key, label=label, state="not_applicable", na_reason="구커널 미지원 (PSI Linux 4.20+)"
        )
    if psi_val is None:
        return SaturationSignal(key=key, label=label, state="no_data")
    return SaturationSignal(
        key=key,
        label=label,
        state="measured",
        value=round(psi_val, 1),
        threshold=PSI_STALL_DISPLAY_PCT,
        unit="%",
        saturated=psi_val >= PSI_STALL_DISPLAY_PCT,
        detail=f"PSI some(자원 대기로 멈춘 시간 비율), 표시 기준 {PSI_STALL_DISPLAY_PCT:.0f}%",
    )


def _ratio_signal(key: str, label: str, val: float | None, threshold: float, desc: str) -> SaturationSignal:
    return SaturationSignal(
        key=key,
        label=label,
        state="measured" if val is not None else "no_data",
        value=round(val, 2) if val is not None else None,
        threshold=float(threshold),
        unit="%",
        saturated=(val >= threshold) if val is not None else None,
        detail=f"{desc}, 임계 {threshold:g}%",
    )


def build_saturation_signals(
    *,
    os_family: str | None,
    kernel_version: str | None,
    run_queue_total: float | None,
    cores: int | None,
    steal_pct: float | None,
    sat: SaturationRaw,
    blocked: float | None = None,
) -> dict[str, list[SaturationSignal]]:
    win = os_family == "windows"
    psi_ok = _psi_supported(kernel_version)

    rq_threshold = CPU_RUN_QUEUE_PER_CORE_SATURATION if win else PROCS_RUNNING_PER_CORE_SATURATION
    rq_percore = (run_queue_total / cores) if (run_queue_total is not None and cores) else None
    cpu = [
        SaturationSignal(
            key="cpu_run_queue",
            label="실행 큐",
            state="measured" if rq_percore is not None else "no_data",
            value=round(rq_percore, 2) if rq_percore is not None else None,
            threshold=rq_threshold,
            unit="per_core",
            saturated=(rq_percore >= rq_threshold) if rq_percore is not None else None,
            detail=("Windows Processor Queue Length" if win else "Linux procs_running")
            + f"/코어, 임계 {rq_threshold:g}",
        )
    ]
    if win:
        cpu.append(SaturationSignal(key="cpu_steal", label="Steal", state="not_applicable", na_reason="Windows 미지원"))
    else:
        cpu.append(
            SaturationSignal(
                key="cpu_steal",
                label="Steal",
                state="measured" if steal_pct is not None else "no_data",
                value=round(steal_pct, 1) if steal_pct is not None else None,
                threshold=float(CPU_STEAL_BIAS_PCT),
                unit="%",
                saturated=(steal_pct >= CPU_STEAL_BIAS_PCT) if steal_pct is not None else None,
                detail=f"가상화 경합(steal%), 임계 {CPU_STEAL_BIAS_PCT:g}%",
            )
        )
    cpu.append(_psi_signal("cpu_psi", "PSI", sat.psi_cpu, win, psi_ok))
    # Windows 는 cpu.blocked 를 미발행한다.
    if win:
        cpu.append(
            SaturationSignal(
                key="cpu_blocked",
                label="D-state 블록",
                state="not_applicable",
                na_reason="Windows 미지원",
            )
        )
    else:
        cpu.append(
            SaturationSignal(
                key="cpu_blocked",
                label="D-state 블록",
                state="measured" if blocked is not None else "no_data",
                value=round(blocked, 1) if blocked is not None else None,
                threshold=float(PROCS_BLOCKED_DSTATE_SATURATION),
                unit=None,
                saturated=(blocked >= PROCS_BLOCKED_DSTATE_SATURATION) if blocked is not None else None,
                detail=f"IO 대기(D-state) 프로세스 수 — 임계 {PROCS_BLOCKED_DSTATE_SATURATION:g}, "
                "CPU 부하가 실제로 IO 대기발인지 근본원인 근거",
            )
        )

    paging = sat.paging_major_rate
    paging_sat = (
        paging >= WIN_PAGES_INPUT_SATURATION
        if os_family == "windows" and paging is not None
        else (paging > 0 if paging is not None else None)
    )
    mem = [
        SaturationSignal(
            key="mem_paging",
            label="페이징",
            state="measured" if paging is not None else "no_data",
            value=round(paging) if paging is not None else None,
            threshold=(WIN_PAGES_INPUT_SATURATION if win else 0.0),
            unit="/s",
            saturated=paging_sat,
            detail=(f"Windows Pages Input/sec, 임계 {WIN_PAGES_INPUT_SATURATION:g}")
            if win
            else "Linux 하드폴트(refault)/s, 발생(>0) 시 압박",
        ),
        _psi_signal("mem_psi", "PSI", sat.psi_mem, win, psi_ok),
    ]

    # Windows 는 await 를 미발행해 큐 깊이로 폴백한다.
    if sat.await_ms is not None:
        disk = [
            SaturationSignal(
                key="disk_await",
                label="응답 지연",
                state="measured",
                value=round(sat.await_ms, 1),
                threshold=float(DISKIO_AWAIT_MS),
                unit="ms",
                saturated=sat.await_ms >= DISKIO_AWAIT_MS,
                detail=f"IO 응답 지연 await, 임계 {DISKIO_AWAIT_MS:g}ms",
            )
        ]
    elif win and sat.pending_ops is not None:
        disk = [
            SaturationSignal(
                key="disk_await",
                label="디스크 큐",
                state="measured",
                value=round(sat.pending_ops, 2),
                threshold=float(DISK_QUEUE_PER_DISK_SATURATION),
                unit="ops",
                saturated=sat.pending_ops >= DISK_QUEUE_PER_DISK_SATURATION,
                detail=f"Windows 큐 깊이(await 폴백), 임계 {DISK_QUEUE_PER_DISK_SATURATION:g}",
            )
        ]
    else:
        disk = [SaturationSignal(key="disk_await", label="응답 지연", state="no_data")]
    disk.append(_psi_signal("disk_psi", "PSI(io)", sat.psi_io, win, psi_ok))

    # conntrack 은 Linux nf_conntrack 전용이고 Windows 는 계약상 미발행(agent-data.md #B) — 절대 나타나지 않을

    ct_ratio = sat.conntrack_ratio
    if win:
        conntrack_sig = SaturationSignal(
            key="net_conntrack",
            label="conntrack",
            state="not_applicable",
            na_reason="Windows 미지원",
        )
    else:
        conntrack_sig = SaturationSignal(
            key="net_conntrack",
            label="conntrack",
            state="measured" if ct_ratio is not None else "no_data",
            value=round(ct_ratio * 100, 1) if ct_ratio is not None else None,
            threshold=CONNTRACK_SATURATION_RATIO * 100,
            unit="%",
            saturated=(ct_ratio >= CONNTRACK_SATURATION_RATIO) if ct_ratio is not None else None,
            detail=f"연결 테이블 사용률, 임계 {CONNTRACK_SATURATION_RATIO * 100:.0f}%",
        )
    net = [
        _ratio_signal("net_retrans", "재전송", sat.retrans_pct, NET_RETRANS_PCT, "TCP 재전송율"),
        _ratio_signal("net_drop", "드롭", sat.drop_pct, NET_DROP_PCT, "패킷 드롭율"),
        conntrack_sig,
    ]

    return {"cpu": cpu, "mem": mem, "disk": disk, "net": net}


def _error_counter(
    key: str,
    label: str,
    count: int,
    measured: bool,
    detail: str,
    *,
    last_at: datetime | None = None,
    context: str | None = None,
    window_label: str,
) -> ErrorSignal:
    if not measured:
        return ErrorSignal(key=key, label=label, state="no_data", window_label=window_label, detail=detail)
    if count > 0:
        return ErrorSignal(
            key=key,
            label=label,
            state="occurred",
            count=count,
            context=context,
            last_at=last_at,
            window_label=window_label,
            detail=detail,
        )
    return ErrorSignal(key=key, label=label, state="clean", count=0, window_label=window_label, detail=detail)


def build_error_signals(err: ErrorFleetRaw, *, window_label: str, os_family: str | None = None) -> list[ErrorSignal]:
    """에러 축 표시자 5종(MCE·OOM·EDAC·NIC·디스크) — 발생 0 건도 노출(E9)."""
    signals = [
        _error_counter(
            "cpu_mce",
            "머신체크(MCE)",
            err.mce_count,
            err.measured,
            "CPU/메모리 하드웨어 정정불가 오류(machine check exception)",
            window_label=window_label,
        ),
        _error_counter(
            "mem_oom",
            "OOM Kill",
            err.oom_count,
            err.measured,
            "메모리 부족으로 커널이 프로세스 강제 종료",
            window_label=window_label,
        ),
        _error_counter(
            "net_errors",
            "NIC 에러",
            err.net_error_count,
            err.net_measured,
            "네트워크 인터페이스 rx/tx 오류 프레임",
            window_label=window_label,
        ),
        _error_counter(
            "disk_errors",
            "디스크 에러",
            err.disk_error_count,
            err.disk_err_measured,
            "RAID degraded·파일시스템 손상·IO 오류",
            last_at=err.last_error_at,
            context=(", ".join(err.disk_error_kinds) if err.disk_error_kinds else None),
            window_label=window_label,
        ),
    ]
    # EDAC 은 카운트가 아니라 gauge(현재값 > 0). Windows 는 WHEA 소스 미구현이라 계약상 항상 null.
    edac_detail = "ECC 정정된 하드웨어 메모리 손상 바이트"
    if os_family == "windows":
        signals.append(
            ErrorSignal(
                key="mem_corrupted",
                label="메모리 손상(EDAC)",
                state="not_applicable",
                window_label=window_label,
                detail="Windows 미지원(WHEA 소스 부재)",
            )
        )
    elif err.corrupted_bytes is None:
        signals.append(
            ErrorSignal(
                key="mem_corrupted",
                label="메모리 손상(EDAC)",
                state="no_data",
                window_label=window_label,
                detail=edac_detail,
            )
        )
    elif err.corrupted_bytes > 0:
        signals.append(
            ErrorSignal(
                key="mem_corrupted",
                label="메모리 손상(EDAC)",
                state="occurred",
                context=f"{err.corrupted_bytes} bytes 손상",
                window_label=window_label,
                detail=edac_detail,
            )
        )
    else:
        signals.append(
            ErrorSignal(
                key="mem_corrupted",
                label="메모리 손상(EDAC)",
                state="clean",
                count=0,
                window_label=window_label,
                detail=edac_detail,
            )
        )
    return signals


def compute_cpu(cur: MetricPairRaw | None, prev: MetricPairRaw | None) -> CpuSnapshot | None:
    if cur is None:
        return None

    def cpu_total(r: MetricPairRaw) -> float:
        # Windows 는 nice/iowait/irq/softirq/steal 이 null(OS 개념 부재) — None->0 정규화(집계 SQL COALESCE 동일).
        # 그래도 분모가 성립하는 근거는 Windows total = user+system+idle 이 GetSystemTimes 전체 스케줄러 시간과
        # 같다는 데 있다. 성분 하나가 null 이라고 total 을 null 로 만들면 Windows CPU 가 항상 N/A 가 된다.
        vals = [
            r.cpu_user_s,
            r.cpu_nice_s,
            r.cpu_system_s,
            r.cpu_idle_s,
            r.cpu_iowait_s,
            r.cpu_irq_s,
            r.cpu_softirq_s,
            r.cpu_steal_s,
        ]
        return sum(v for v in vals if v is not None)

    if prev is None:
        return CpuSnapshot(usage_pct=None, user_pct=None, system_pct=None, iowait_pct=None)

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
        nice_pct=pct(cur.cpu_nice_s, prev.cpu_nice_s),
    )


def compute_cpu_cores(pairs: list[CpuCoreRaw]) -> list[CpuCoreSnapshot]:
    """코어별 순간 사용률 — Linux 전용(Windows 는 pairs 항상 빈 list).

    boot_time 미보유 child 시계열이라 재부팅 리셋은 delta_total<=0 으로 흡수한다(별도 reset 판정 없음).
    """
    by_core = _group_by_dim(pairs, key=lambda r: str(r.core_id))
    result: list[CpuCoreSnapshot] = []
    for core_id, rows in sorted(by_core.items(), key=lambda kv: int(kv[0])):
        rows_sorted = sorted(rows, key=lambda r: r.collected_at, reverse=True)
        if len(rows_sorted) < 2:
            result.append(CpuCoreSnapshot(core_id=int(core_id), usage_pct=None))
            continue
        cur, prev = rows_sorted[0], rows_sorted[1]
        vals_cur = [
            cur.cpu_user_s,
            cur.cpu_nice_s,
            cur.cpu_system_s,
            cur.cpu_idle_s,
            cur.cpu_iowait_s,
            cur.cpu_irq_s,
            cur.cpu_softirq_s,
            cur.cpu_steal_s,
        ]
        vals_prev = [
            prev.cpu_user_s,
            prev.cpu_nice_s,
            prev.cpu_system_s,
            prev.cpu_idle_s,
            prev.cpu_iowait_s,
            prev.cpu_irq_s,
            prev.cpu_softirq_s,
            prev.cpu_steal_s,
        ]
        delta_total = sum(v for v in vals_cur if v is not None) - sum(v for v in vals_prev if v is not None)
        if delta_total <= 0 or cur.cpu_idle_s is None or prev.cpu_idle_s is None:
            result.append(CpuCoreSnapshot(core_id=int(core_id), usage_pct=None))
            continue
        idle_pct = max(0.0, (cur.cpu_idle_s - prev.cpu_idle_s) / delta_total * 100)
        usage = round(max(0.0, 100.0 - idle_pct), 1)

        result.append(CpuCoreSnapshot(core_id=int(core_id), usage_pct=usage, hot=usage >= CPU_PERCORE_HOLD_PCT))
    return result


def compute_mem(cur: MetricPairRaw | None) -> MemSnapshot | None:
    if cur is None or cur.mem_limit_bytes is None:
        return None

    # mem_used_bytes(total-free-buff/cache)를 쓰지 않는다 — limit-available 과 어긋나 스택바 합이 100 을 깬다.

    used = max(0, cur.mem_limit_bytes - cur.mem_available_bytes) if cur.mem_available_bytes is not None else None
    used_pct = usage_pct(used, cur.mem_limit_bytes)

    # 구성 모델(types._ENV_SCALAR_WEIGHTED): Used + Available = 100 이고 Cached/Buffers 는 Available 안의 세부다
    # — 남은 공간 안에서만 cached -> buffers 를 쌓고 잔여를 free 로 채워야 bar 합이 정확히 100 이 된다.
    remaining_after_used = 100.0 - (used_pct or 0.0)
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


def compute_disk_io(pairs: list[DiskIoRaw], dev_names: dict[str, str] | None = None) -> list[DiskIoSnapshot]:
    if dev_names is not None:
        pairs = [p for p in pairs if p.device_id in dev_names]
    by_device = _group_by_dim(pairs, key=lambda r: r.device_id)
    return [_disk_io_snapshot(rows, dev_names) for _did, rows in sorted(by_device.items())]


def _disk_io_snapshot(rows: list[DiskIoRaw], dev_names: dict[str, str] | None = None) -> DiskIoSnapshot:
    display = (dev_names or {}).get(rows[0].device_id) or rows[0].device_name or rows[0].device_id
    if len(rows) < 2:
        return DiskIoSnapshot(device=display, read_iops=None, write_iops=None, read_kbps=None, write_kbps=None)
    cur, prev = rows[0], rows[1]
    dt = (cur.collected_at - prev.collected_at).total_seconds()
    if dt <= 0:
        return DiskIoSnapshot(device=display, read_iops=None, write_iops=None, read_kbps=None, write_kbps=None)
    if is_counter_reset(cur.boot_time, prev.boot_time):
        return DiskIoSnapshot(device=display, read_iops=None, write_iops=None, read_kbps=None, write_kbps=None)
    return DiskIoSnapshot(
        device=display,
        read_iops=_delta_rate(cur.ops_read, prev.ops_read, dt),
        write_iops=_delta_rate(cur.ops_write, prev.ops_write, dt),
        read_kbps=_delta_kbps(cur.io_read_bytes, prev.io_read_bytes, dt),
        write_kbps=_delta_kbps(cur.io_write_bytes, prev.io_write_bytes, dt),
    )


def compute_net_io(pairs: list[NetIoRaw], iface_names: dict[str, str] | None = None) -> list[NetIoSnapshot]:
    if iface_names is not None:
        pairs = [p for p in pairs if p.iface_id in iface_names]
    by_iface = _group_by_dim(pairs, key=lambda r: r.iface_id)
    return [_net_io_snapshot(rows, iface_names) for _iid, rows in sorted(by_iface.items())]


def _net_io_snapshot(rows: list[NetIoRaw], iface_names: dict[str, str] | None = None) -> NetIoSnapshot:
    display = (iface_names or {}).get(rows[0].iface_id) or rows[0].iface_name or rows[0].iface_id
    if len(rows) < 2:
        return NetIoSnapshot(interface=display, rx_kbps=None, tx_kbps=None, rx_pps=None, tx_pps=None)
    cur, prev = rows[0], rows[1]
    dt = (cur.collected_at - prev.collected_at).total_seconds()
    if dt <= 0:
        return NetIoSnapshot(interface=display, rx_kbps=None, tx_kbps=None, rx_pps=None, tx_pps=None)
    if is_counter_reset(cur.boot_time, prev.boot_time):
        return NetIoSnapshot(interface=display, rx_kbps=None, tx_kbps=None, rx_pps=None, tx_pps=None)
    return NetIoSnapshot(
        interface=display,
        rx_kbps=_delta_kbps(cur.rx_bytes, prev.rx_bytes, dt),
        tx_kbps=_delta_kbps(cur.tx_bytes, prev.tx_bytes, dt),
        rx_pps=_delta_rate(cur.rx_packets, prev.rx_packets, dt),
        tx_pps=_delta_rate(cur.tx_packets, prev.tx_packets, dt),
    )


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
