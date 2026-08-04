"""Outbound raw DTO → Dashboard ViewModel — delta 기반 percent/rate 계산.

원칙:
- raw 누적 카운터 2시점 페어로 delta 계산 (CPU, disk_io, net_io)
- counter reset 식별 우선순위:
  1) 두 시점의 boot_time이 다르면 시스템 재부팅 → delta 계산 건너뛰기 (None)
     agent_started_at만 다르면 에이전트 재시작이고 카운터는 그대로라 정상 계산
  2) boot_time 둘 다 NULL(child 시계열)이면 d < 0 휴리스틱 fallback (CLAUDE.md #C1)
- 시점 값은 그대로 변환 (mem, mount usage) — reset 무관. 단위는 By.
"""

import re
from collections.abc import Callable
from datetime import datetime

from assessment_engine.boot_time import is_counter_reset
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
from assessment_engine.recommendation import (
    CPU_RUN_QUEUE_PER_CORE_SATURATION,
    DISK_QUEUE_PER_DISK_SATURATION,
    PROCS_BLOCKED_DSTATE_SATURATION,
    PROCS_RUNNING_PER_CORE_SATURATION,
    RS_CONNTRACK_SATURATION_RATIO,
    RS_CPU_PERCORE_HOLD_PCT,
    RS_CPU_STEAL_BIAS_PCT,
    RS_DISKIO_AWAIT_MS,
    RS_NET_DROP_PCT,
    RS_NET_RETRANS_PCT,
    WIN_PAGES_INPUT_SATURATION,
    cpu_saturation_index,
    disk_io_saturation_index,
    mem_pressure_active,
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

# PSI ratio_avg10(대기 시간 비율 %) 표시 기준선 — 판단(right-sizing) 미사용(deferred), 스냅샷 표시 전용.
# run_queue/await 는 도메인 판정 임계를 재사용하나, PSI 는 판단 축이 아니라 표시 기준을 여기서 명명.
PSI_STALL_DISPLAY_PCT = 10.0

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


def _composite_dev_id(node: JsonObject) -> str | None:
    """block_device/net_interface 노드 -> 시계열 조인 키 {id_type}:{id} (disk_io.device_id·net_io.iface_id 형식)."""
    did, dtype = node.get("id"), node.get("id_type")
    return f"{dtype}:{did}" if did and dtype else None


def _physical_dev_names(nodes: list[JsonObject] | None, keep: Callable[[JsonObject], bool]) -> dict[str, str] | None:
    """물리 계층 {조인키: 친숙 이름(name)} 맵 — keep(node) 통과분. 인벤토리 부재(None)면 필터 안 함.

    시계열 device_name/iface_name 은 null 이라 조인키로 폴백 -> 인벤토리 name(vda·enp3s0)으로 표시명 해결.
    조인키는 {id_type}:{id} 우선 + name:{name} 폴백 둘 다 등록 — 계약(agent-data.md E절) 상 inventory id_type
    (예 Windows 디스크 gptid)과 metric 컬렉터 device_id(perflib 등 별도 하위계층이라 gptid 미접근 시 name: 폴백,
    카탈로그 밖) 가 서로 다른 폴백 사슬을 골라 갈릴 수 있음 — 실측(win2025 disk_io: name:PhysicalDrive0 vs
    inventory gptid:...) 확인. 두 키 중 먼저 매치되는 쪽으로 물리 필터가 전체 드롭되지 않게 방어.
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
            continue  # 이름·조인키 둘 다 없어 등록할 키가 없다
        if cid:
            result[cid] = display
        if name:
            result[f"name:{name}"] = display
    return result


def build_dashboard(raw: DashboardRaw) -> MetricDashboard:
    cur = raw.metrics[0] if raw.metrics else None
    prev = raw.metrics[1] if len(raw.metrics) >= 2 else None

    # I/O 활동 축 = 물리 디스크·인터페이스 단일 규칙(device_filters). 인벤토리 조인키로 좁힘 — 인벤토리 부재면
    # 필터 없이 전체(None). LV/파티션·loopback/veth 통과분 이중집계 제외. name 맵으로 표시명(vda) 해결.
    phys_disks = _physical_dev_names(raw.block_devices, lambda n: is_physical_disk(n.get("type")))
    phys_ifaces = _physical_dev_names(raw.net_interfaces, lambda n: not is_virtual_interface(n.get("kind")))

    mounts = compute_mounts(raw.filesystems)
    # 실시간 카드 = 순간 도넛(이용률) + 활동(I/O). 이용률·포화 2축 분류는 14일 p95 창이라 별도 SSR 카드
    # (PeriodAssessment)로 분리 — 순간 도넛 값(disk_usage_pct)만 여기서 산출.
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
    """디스크 이용률 % = 데이터 볼륨 파일시스템 used/total 집계. compute_mounts 가 가상 fs 이미 제외 -> 합산만."""
    total = sum((m.total_gb or 0) for m in mounts if m.total_gb)
    used = sum((m.used_gb or 0) for m in mounts if m.total_gb and m.used_gb is not None)
    return round(used / total * 100, 1) if total > 0 else None


# ─── 포화 스냅샷 신호 (os-aware 서버 판정, P2) ─────────────────────────────


def _psi_supported(kernel_version: str | None) -> bool | None:
    """PSI(Pressure Stall Info) 지원 여부 — Linux 4.20+ 만 발행. 커널 미상이면 None(판정 보류)."""
    if not kernel_version:
        return None
    m = re.match(r"(\d+)\.(\d+)", kernel_version)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2))) >= (4, 20)


def _psi_signal(key: str, label: str, psi_val: float | None, win: bool, supported: bool | None) -> SaturationSignal:
    """PSI 신호 — Windows·구커널(Linux <4.20) 미지원(N/A), Linux 는 값/기준선. psi None = 미수집(no_data).

    supported=False(구커널 확정) 는 Windows 와 동일 N/A — "수집 대기" 오인 방지. None(커널 미상)은 판정 보류.
    """
    if win:
        return SaturationSignal(key=key, label=label, state="not_applicable", na_reason="Windows 미지원")
    if supported is False:
        return SaturationSignal(
            key=key, label=label, state="not_applicable", na_reason="구커널 미지원 (PSI Linux 4.20+)"
        )
    if psi_val is None:
        return SaturationSignal(key=key, label=label, state="no_data")
    return SaturationSignal(
        key=key, label=label, state="measured", value=round(psi_val, 1),
        threshold=PSI_STALL_DISPLAY_PCT, unit="%", saturated=psi_val >= PSI_STALL_DISPLAY_PCT,
        detail=f"PSI some(자원 대기로 멈춘 시간 비율), 표시 기준 {PSI_STALL_DISPLAY_PCT:.0f}%",
    )


def _ratio_signal(key: str, label: str, val: float | None, threshold: float, desc: str) -> SaturationSignal:
    """양 OS 공통 % 비율 신호(재전송·드롭) — RS_ 임계 초과 시 saturated."""
    return SaturationSignal(
        key=key, label=label,
        state="measured" if val is not None else "no_data",
        value=round(val, 2) if val is not None else None,
        threshold=float(threshold), unit="%", saturated=(val >= threshold) if val is not None else None,
        detail=f"{desc}, 임계 {threshold:g}%",
    )


def build_saturation_signals(
    *, os_family: str | None, kernel_version: str | None, run_queue_total: float | None, cores: int | None,
    steal_pct: float | None, sat: SaturationRaw, blocked: float | None = None,
) -> dict[str, list[SaturationSignal]]:
    """자원별 포화 스냅샷 신호 4축 산출 (P2). 판정은 도메인 os-aware helper·RS_ 임계 재사용(E3 재계산 금지).

    반환 {"cpu"|"mem"|"disk"|"net": [SaturationSignal]} — MetricDashboard 4 리스트에 배선.
    """
    win = os_family == "windows"
    psi_ok = _psi_supported(kernel_version)  # Linux 4.20+ = True / 구커널 = False / 미상 = None

    # CPU 실행 큐 (per-core) — cpu_saturation_index 단일 진실.
    rq_idx = cpu_saturation_index(run_queue_total, cores, os_family)
    rq_threshold = CPU_RUN_QUEUE_PER_CORE_SATURATION if win else PROCS_RUNNING_PER_CORE_SATURATION
    rq_percore = (run_queue_total / cores) if (run_queue_total is not None and cores) else None
    cpu = [
        SaturationSignal(
            key="cpu_run_queue", label="실행 큐",
            state="measured" if rq_idx is not None else "no_data",
            value=round(rq_percore, 2) if rq_percore is not None else None,
            threshold=rq_threshold, unit="per_core",
            saturated=(rq_idx >= 1.0) if rq_idx is not None else None,
            detail=("Windows Processor Queue Length" if win else "Linux procs_running")
            + f"/코어, 임계 {rq_threshold:g}",
        )
    ]
    # CPU Steal (Linux 전용).
    if win:
        cpu.append(SaturationSignal(key="cpu_steal", label="Steal", state="not_applicable", na_reason="Windows 미지원"))
    else:
        cpu.append(SaturationSignal(
            key="cpu_steal", label="Steal",
            state="measured" if steal_pct is not None else "no_data",
            value=round(steal_pct, 1) if steal_pct is not None else None,
            threshold=float(RS_CPU_STEAL_BIAS_PCT), unit="%",
            saturated=(steal_pct >= RS_CPU_STEAL_BIAS_PCT) if steal_pct is not None else None,
            detail=f"가상화 경합(steal%), 임계 {RS_CPU_STEAL_BIAS_PCT:g}%",
        ))
    cpu.append(_psi_signal("cpu_psi", "PSI", sat.psi_cpu, win, psi_ok))
    # D-state 블록(IO 대기 근본원인) — Linux 전용(cpu.blocked, Windows 미발행). 순간 gauge, delta 불요.
    if win:
        cpu.append(SaturationSignal(
            key="cpu_blocked", label="D-state 블록", state="not_applicable", na_reason="Windows 미지원",
        ))
    else:
        cpu.append(SaturationSignal(
            key="cpu_blocked", label="D-state 블록",
            state="measured" if blocked is not None else "no_data",
            value=round(blocked, 1) if blocked is not None else None,
            threshold=float(PROCS_BLOCKED_DSTATE_SATURATION), unit=None,
            saturated=(blocked >= PROCS_BLOCKED_DSTATE_SATURATION) if blocked is not None else None,
            detail=f"IO 대기(D-state) 프로세스 수 — 임계 {PROCS_BLOCKED_DSTATE_SATURATION:g}, "
            "CPU 부하가 실제로 IO 대기발인지 근본원인 근거",
        ))

    # 메모리 페이징 (os-aware) — mem_pressure_active 단일 진실.
    paging = sat.paging_major_rate
    paging_sat = mem_pressure_active(paging, os_family) if paging is not None else None
    mem = [
        SaturationSignal(
            key="mem_paging", label="페이징",
            state="measured" if paging is not None else "no_data",
            value=round(paging) if paging is not None else None,
            threshold=(WIN_PAGES_INPUT_SATURATION if win else 0.0), unit="/s", saturated=paging_sat,
            detail=(f"Windows Pages Input/sec, 임계 {WIN_PAGES_INPUT_SATURATION:g}") if win
            else "Linux 하드폴트(refault)/s, 발생(>0) 시 압박",
        ),
        _psi_signal("mem_psi", "PSI", sat.psi_mem, win, psi_ok),
    ]

    # 디스크 응답 지연 (await, 양 OS) — disk_io_saturation_index 단일 진실. Windows await 부재 시 큐 폴백.
    di_idx = disk_io_saturation_index(sat.await_ms, sat.pending_ops, os_family)
    if sat.await_ms is not None:
        disk = [SaturationSignal(
            key="disk_await", label="응답 지연", state="measured", value=round(sat.await_ms, 1),
            threshold=float(RS_DISKIO_AWAIT_MS), unit="ms",
            saturated=(di_idx >= 1.0) if di_idx is not None else None,
            detail=f"IO 응답 지연 await, 임계 {RS_DISKIO_AWAIT_MS:g}ms",
        )]
    elif win and sat.pending_ops is not None:
        disk = [SaturationSignal(
            key="disk_await", label="디스크 큐", state="measured", value=round(sat.pending_ops, 2),
            threshold=float(DISK_QUEUE_PER_DISK_SATURATION), unit="ops",
            saturated=(di_idx >= 1.0) if di_idx is not None else None,
            detail=f"Windows 큐 깊이(await 폴백), 임계 {DISK_QUEUE_PER_DISK_SATURATION:g}",
        )]
    else:
        disk = [SaturationSignal(key="disk_await", label="응답 지연", state="no_data")]
    disk.append(_psi_signal("disk_psi", "PSI(io)", sat.psi_io, win, psi_ok))

    # 네트워크 품질 — 재전송·드롭(에러성 rate, 양 OS) + conntrack(연결테이블 포화, Linux nf_conntrack 전용 —
    # agent-data.md #B "conntrack 미발행" Windows 구조적 미지원, 절대 나타나지 않을 값이라 no_data("수집 대기")
    # 아닌 not_applicable).
    ct_ratio = sat.conntrack_ratio
    if win:
        conntrack_sig = SaturationSignal(
            key="net_conntrack", label="conntrack", state="not_applicable", na_reason="Windows 미지원",
        )
    else:
        conntrack_sig = SaturationSignal(
            key="net_conntrack", label="conntrack",
            state="measured" if ct_ratio is not None else "no_data",
            value=round(ct_ratio * 100, 1) if ct_ratio is not None else None,
            threshold=RS_CONNTRACK_SATURATION_RATIO * 100, unit="%",
            saturated=(ct_ratio >= RS_CONNTRACK_SATURATION_RATIO) if ct_ratio is not None else None,
            detail=f"연결 테이블 사용률, 임계 {RS_CONNTRACK_SATURATION_RATIO * 100:.0f}%",
        )
    net = [
        _ratio_signal("net_retrans", "재전송", sat.retrans_pct, RS_NET_RETRANS_PCT, "TCP 재전송율"),
        _ratio_signal("net_drop", "드롭", sat.drop_pct, RS_NET_DROP_PCT, "패킷 드롭율"),
        conntrack_sig,
    ]

    return {"cpu": cpu, "mem": mem, "disk": disk, "net": net}


# ─── 에러 축 표시자 (Errors, 정상=0 발화 E9) ───────────────────────────────


def _error_counter(
    key: str, label: str, count: int, measured: bool, detail: str,
    *, last_at: datetime | None=None, context: str | None = None, window_label: str,
) -> ErrorSignal:
    """카운트형 에러 신호 — 미측정 no_data / 발생(>0) occurred / 정상(0) clean."""
    if not measured:
        return ErrorSignal(key=key, label=label, state="no_data", window_label=window_label, detail=detail)
    if count > 0:
        return ErrorSignal(
            key=key, label=label, state="occurred", count=count, context=context,
            last_at=last_at, window_label=window_label, detail=detail,
        )
    return ErrorSignal(key=key, label=label, state="clean", count=0, window_label=window_label, detail=detail)


def build_error_signals(
    err: ErrorFleetRaw, *, window_label: str, os_family: str | None = None
) -> list[ErrorSignal]:
    """에러 축 표시자 5종 (MCE·OOM·EDAC·NIC·디스크) — 카운트 + 종류 + 창. 정상=0 발화(E9)."""
    signals = [
        _error_counter("cpu_mce", "머신체크(MCE)", err.mce_count, err.measured,
                       "CPU/메모리 하드웨어 정정불가 오류(machine check exception)", window_label=window_label),
        _error_counter("mem_oom", "OOM Kill", err.oom_count, err.measured,
                       "메모리 부족으로 커널이 프로세스 강제 종료", window_label=window_label),
        _error_counter("net_errors", "NIC 에러", err.net_error_count, err.net_measured,
                       "네트워크 인터페이스 rx/tx 오류 프레임", window_label=window_label),
        _error_counter("disk_errors", "디스크 에러", err.disk_error_count, err.disk_err_measured,
                       "RAID degraded·파일시스템 손상·IO 오류", last_at=err.last_error_at,
                       context=(", ".join(err.disk_error_kinds) if err.disk_error_kinds else None),
                       window_label=window_label),
    ]
    # EDAC 메모리 손상 — gauge(현재값 > 0), 카운트 아님. Windows 는 WHEA 소스 미구현이라 구조적 미지원(계약
    # agent-data.md #B: "memory.hardware_corrupted null") — no_data("수집 대기")로 오인 표시 금지, not_applicable.
    edac_detail = "ECC 정정된 하드웨어 메모리 손상 바이트"
    if os_family == "windows":
        signals.append(ErrorSignal(key="mem_corrupted", label="메모리 손상(EDAC)", state="not_applicable",
                                   window_label=window_label, detail="Windows 미지원(WHEA 소스 부재)"))
    elif err.corrupted_bytes is None:
        signals.append(ErrorSignal(key="mem_corrupted", label="메모리 손상(EDAC)", state="no_data",
                                   window_label=window_label, detail=edac_detail))
    elif err.corrupted_bytes > 0:
        signals.append(ErrorSignal(key="mem_corrupted", label="메모리 손상(EDAC)", state="occurred",
                                   context=f"{err.corrupted_bytes} bytes 손상", window_label=window_label,
                                   detail=edac_detail))
    else:
        signals.append(ErrorSignal(key="mem_corrupted", label="메모리 손상(EDAC)", state="clean", count=0,
                                   window_label=window_label, detail=edac_detail))
    return signals


# ─── CPU ──────────────────────────────────────────────────────────────────


def compute_cpu(cur: MetricPairRaw | None, prev: MetricPairRaw | None) -> CpuSnapshot | None:
    if cur is None:
        return None

    def cpu_total(r: MetricPairRaw) -> float:
        # Windows 는 nice/iowait/irq/softirq/steal 이 null (OS 개념 부재) — None->0 정규화.
        # 집계 SQL 의 COALESCE 와 같은 처리다.
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
        nice_pct=pct(cur.cpu_nice_s, prev.cpu_nice_s),
    )


def compute_cpu_cores(pairs: list[CpuCoreRaw]) -> list[CpuCoreSnapshot]:
    """코어별 순간 사용률 — host 집계(compute_cpu)와 동일 산식(1 - delta(idle)/delta(total)), core_id 그룹.

    Linux 전용(Windows 는 pairs 항상 빈 list). boot_time 미보유 child 시계열이라 d<0 자연 가드(#C1 fallback,
    disk_io/net_io 와 동일 관례) — 재부팅으로 카운터 리셋되면 delta_total<=0 -> None.
    """
    by_core = _group_by_dim(pairs, key=lambda r: str(r.core_id))
    result: list[CpuCoreSnapshot] = []
    for core_id, rows in sorted(by_core.items(), key=lambda kv: int(kv[0])):
        rows_sorted = sorted(rows, key=lambda r: r.collected_at, reverse=True)
        if len(rows_sorted) < 2:
            result.append(CpuCoreSnapshot(core_id=int(core_id), usage_pct=None))
            continue
        cur, prev = rows_sorted[0], rows_sorted[1]
        vals_cur = [cur.cpu_user_s, cur.cpu_nice_s, cur.cpu_system_s, cur.cpu_idle_s,
                    cur.cpu_iowait_s, cur.cpu_irq_s, cur.cpu_softirq_s, cur.cpu_steal_s]
        vals_prev = [prev.cpu_user_s, prev.cpu_nice_s, prev.cpu_system_s, prev.cpu_idle_s,
                     prev.cpu_iowait_s, prev.cpu_irq_s, prev.cpu_softirq_s, prev.cpu_steal_s]
        delta_total = sum(v for v in vals_cur if v is not None) - sum(v for v in vals_prev if v is not None)
        if delta_total <= 0 or cur.cpu_idle_s is None or prev.cpu_idle_s is None:
            result.append(CpuCoreSnapshot(core_id=int(core_id), usage_pct=None))
            continue
        idle_pct = max(0.0, (cur.cpu_idle_s - prev.cpu_idle_s) / delta_total * 100)
        usage = round(max(0.0, 100.0 - idle_pct), 1)
        # hot precompute — 임계 단일 진실(RS_CPU_PERCORE_HOLD_PCT). 클라(cpu.js)는 플래그만 읽어 P4 임계 재선언 제거.
        result.append(CpuCoreSnapshot(core_id=int(core_id), usage_pct=usage, hot=usage >= RS_CPU_PERCORE_HOLD_PCT))
    return result


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


def compute_disk_io(pairs: list[DiskIoRaw], dev_names: dict[str, str] | None = None) -> list[DiskIoSnapshot]:
    """device_id별 그룹 -> 페어 rate. dev_names(=block_devices type=disk 조인키->이름) 주면 물리 디스크만 통과.

    None(인벤토리 부재)이면 전체 flat(폴백). LV/파티션/RAID 통과분은 물리 필터로 이중집계 제외. 표시명은 맵 name.
    """
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


def compute_net_io(pairs: list[NetIoRaw], iface_names: dict[str, str] | None = None) -> list[NetIoSnapshot]:
    """iface_id별 그룹 -> 페어 rate. iface_names(=net_interfaces physical/bond_master 조인키->이름) 주면 물리만.

    None(인벤토리 부재)이면 전체 노출(폴백). loopback/veth/bridge/bond_member 는 물리 필터로 제외. 표시명은 맵 name.
    """
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
