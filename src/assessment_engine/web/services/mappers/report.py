"""보고서 row mapper — ReportRowRaw -> ReportRowItem + 보조 집계·요약.

customer/engineer 양식 차등은 `docs/explanation/products/server-report.md`.
"""

from collections import Counter
from typing import TYPE_CHECKING

from assessment_engine.domain import right_sizing
from assessment_engine.domain.service_classifier import SIGNATURE_CATEGORIES, detect_listen_categories
from assessment_engine.json_types import json_list
from assessment_engine.web.services.device_filters import disk_total_bytes, is_virtual_interface
from assessment_engine.web.services.mappers.assessment_display import build_host_confidence_notes
from assessment_engine.web.services.mappers.constants import (
    _VARIANCE_BURST_RATIO,
    BADGE_CLASS,
    OS_FAMILY_LABEL_KO,
    RISK_LEVEL_ORDER,
)
from assessment_engine.web.services.mappers.os_eol import (
    lookup_os_eol,
    os_eol_display,
)
from assessment_engine.web.services.mappers.resource_stats import build_resource_stats
from assessment_engine.web.services.mappers.server import (
    _os_display,
    _services_or_none,
    _to_listen_port_item,
    infer_role,
    workload_category_counter,
    workload_services_by_category,
)
from assessment_engine.web.services.unit_converter import bytes_to_gb, bytes_to_gib
from assessment_engine.web.view_models.report import (
    ReportListenItem,
    ReportRowItem,
    ReportTotals,
    ReportWorkloadGroup,
)

if TYPE_CHECKING:
    from datetime import datetime

    from assessment_engine.db.dtos.outbound import ReportRowRaw


_RISK_FROM_RECOMMENDATION: dict[str, tuple[str, str, str]] = {
    "under_provisioned": ("high", "고위험", "rec-under_provisioned"),
    "idle": ("attention", "주의 필요", "rec-over_provisioned"),
    "over_provisioned": ("attention", "주의 필요", "rec-over_provisioned"),
    "optimal": ("normal", "정상", "rec-optimal"),
    "insufficient_data": ("normal", "정상", "rec-optimal"),
}


_NET_STATUS_LABEL: dict[str, str] = {"quality_ok": "정상", "congested": "혼잡", "unmeasured": "미측정"}


def compute_report_avg_p95(rows: list[ReportRowItem]) -> tuple[float | None, float | None]:
    cpu_vals = [r.cpu_p95_pct for r in rows if r.cpu_p95_pct is not None]
    mem_vals = [r.mem_p95_pct for r in rows if r.mem_p95_pct is not None]
    avg_cpu = sum(cpu_vals) / len(cpu_vals) if cpu_vals else None
    avg_mem = sum(mem_vals) / len(mem_vals) if mem_vals else None
    return avg_cpu, avg_mem


def compute_report_totals_from_raw(raws: list[ReportRowRaw]) -> ReportTotals:
    total_vcpus = sum(r.cpu_cores or 0 for r in raws)
    total_mem_bytes = sum(r.mem_total_bytes or 0 for r in raws)
    total_disk_bytes = sum(disk_total_bytes(r.block_devices or []) for r in raws)
    return ReportTotals(
        total_vcpus=total_vcpus,
        total_memory_gb=bytes_to_gib(total_mem_bytes) or 0.0,
        total_disk_gb=int(bytes_to_gb(total_disk_bytes) or 0),
    )


def build_role_distribution(raws: list[ReportRowRaw]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for r in raws:
        counter[infer_role(r.services, r.listen_ports)] += 1
    return dict(counter.most_common())


def build_selection_context(items: list[ReportRowItem], role_distribution: dict[str, int]) -> tuple[str, str]:
    """N대 보고서 선택 맥락 — (os_summary "Linux 2 | Windows 1", workload_summary "web 2, db 1").

    N 이 작아 분포 막대 대신 한 줄 텍스트로 요약한다.
    """
    os_counter: Counter[str] = Counter((it.os_family or "unknown") for it in items)
    os_summary = " | ".join(
        f"{OS_FAMILY_LABEL_KO.get(k, k)} {v}" for k, v in sorted(os_counter.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    workload_summary = (
        ", ".join(f"{cat} {n}" for cat, n in sorted(role_distribution.items(), key=lambda kv: (-kv[1], kv[0])))
        or "분류된 워크로드 없음"
    )
    return os_summary, workload_summary


def sort_rows_for_report(items: list[ReportRowItem]) -> list[ReportRowItem]:
    return sorted(
        items,
        key=lambda it: (RISK_LEVEL_ORDER.get(it.risk_level, 99), -(it.cpu_p95_pct or 0.0), it.hostname),
    )


def _build_recommendation_action(host: right_sizing.HostAssessment, stats: right_sizing.ResourceStats) -> str:
    return right_sizing.host_recommendation_action(host, stats)


def _build_diagnosis(
    host: right_sizing.HostAssessment,
    raw: ReportRowRaw,
    cpu_variance: float | None,
    mem_variance: float | None,
) -> str:
    mem = host.resources["memory"]
    cpu = host.resources["cpu"]
    if mem.status == "under" and ("mem_saturation" in mem.triggers or "mem_oom" in mem.triggers):
        return "메모리 부족 (페이징 과다)" if raw.os_family == "windows" else "메모리 부족 (스왑 발생)"
    if host.resources["disk_io"].status == "io_bound":
        return "디스크 I/O 병목"
    if cpu.status == "under" and "cpu_saturation" in cpu.triggers:
        return "CPU 포화"
    if mem.status == "under" and "mem_util" in mem.triggers:
        return "메모리 압박"
    if cpu.status == "under" and "cpu_util" in cpu.triggers:
        return "CPU 압박"
    if host.resources["disk_capacity"].status == "filling":
        return "디스크 용량 임박"
    if host.network_congested:
        return "네트워크 혼잡"

    cpu_burst = (
        cpu_variance is not None
        and cpu_variance >= _VARIANCE_BURST_RATIO
        and raw.cpu_peak_pct is not None
        and raw.cpu_peak_pct > right_sizing.CPU_BURST_PEAK_FLOOR_PCT
    )
    mem_burst = (
        mem_variance is not None
        and mem_variance >= _VARIANCE_BURST_RATIO
        and raw.mem_peak_pct is not None
        and raw.mem_peak_pct > right_sizing.MEM_BURST_PEAK_FLOOR_PCT
    )
    if cpu_burst or mem_burst:
        return "부하 변동 큼"

    if host.recommendation == "idle":
        return "거의 미사용"
    if host.recommendation == "over_provisioned":
        return "여유 있음"
    return "정상"


def _build_insufficient_reason(raw: ReportRowRaw, is_online: bool) -> str:
    """insufficient_data 호스트의 원인 진단 — 오프라인 -> 누락 메트릭 -> 표본 부족 순.

    누락 목록의 saturation 축은 os-aware 로 가른다. Windows 에 아예 없는 축을 "누락"으로 나열하면 개념
    부재를 수집 실패로 오도한다.
    """
    if not is_online:
        return "오프라인 — 에이전트 미가동"
    missing: list[str] = []
    if raw.cpu_p95_pct is None:
        missing.append("CPU")
    if raw.mem_p95_pct is None:
        missing.append("메모리")
    if raw.os_family == "windows":
        if raw.cpu_run_queue_p95 is None:
            missing.append("run queue")
    elif raw.procs_running_p95 is None:
        missing.append("실행 큐")

    if raw.disk_await_p95_ms is None:
        missing.append("디스크 응답(await)")
    if raw.worst_mount_used_pct is None:
        missing.append("디스크")
    return f"메트릭 수집 누락: {' · '.join(missing)}" if missing else "윈도우 내 표본 부족"


def _build_workload_display(
    raw: ReportRowRaw,
) -> tuple[list[ReportWorkloadGroup], list[ReportListenItem]]:
    services = _services_or_none(raw.services, raw.listen_ports) or []
    listen = [
        ReportListenItem(port=lp.port, proto=lp.proto, comm=lp.comm or "", addr=lp.addr, uid=lp.uid, pid=lp.pid)
        for lp in (_to_listen_port_item(p) for p in (raw.listen_ports or []))
    ]
    listen.sort(key=lambda x: (x.port, x.proto))
    by_cat: dict[str, list[str]] = {}
    by_cat_ports: dict[str, list[str]] = {}
    for si in services:
        if si.category == "unknown":
            continue
        name = si.display_name or si.unit
        if name:
            by_cat.setdefault(si.category, []).append(name)
        by_cat_ports.setdefault(si.category, []).extend(f"{p.port}/{p.proto}" for p in si.ports)

    for cat in detect_listen_categories(raw.listen_ports or []):
        by_cat.setdefault(cat, [])
    groups = [
        ReportWorkloadGroup(
            category=cat,
            names_label=", ".join(dict.fromkeys(by_cat[cat])),
            ports=list(dict.fromkeys(by_cat_ports.get(cat, []))),
        )
        for cat in sorted(by_cat)
    ]
    return groups, listen


def to_report_row_item(
    raw: ReportRowRaw, is_online: bool, now: datetime, has_operational_event: bool = False
) -> ReportRowItem:
    info = lookup_os_eol(raw.os_id, raw.os_version, raw.kernel_version, now.date())
    os_eol, os_eol_status = ("", "unknown") if info is None else (info.eol_iso, info.status)
    os_eol_disp = os_eol_display(os_eol_status, os_eol)

    stats = build_resource_stats(raw, disk_baseline=raw.disk_iops_baseline)

    host = right_sizing.rollup_host(stats)
    net_status_label = _NET_STATUS_LABEL.get(host.resources["network"].status, "미측정")
    workload_groups, listen_ports_detail = _build_workload_display(raw)

    workload_categories = list(workload_category_counter(raw.services, raw.listen_ports).keys())
    signature_workload_categories = [c for c in workload_categories if c in SIGNATURE_CATEGORIES]
    workload_services = workload_services_by_category(raw.services, raw.listen_ports)
    rec = host.recommendation
    is_partial = right_sizing.has_unmeasured_saturation(host)
    risk_level, risk_label, risk_badge_class = _RISK_FROM_RECOMMENDATION[rec]
    uptime_days: int | None = None
    if raw.boot_time is not None:
        delta = now - raw.boot_time
        uptime_days = max(0, int(delta.total_seconds() // 86400))

    cpu_variance = None
    if raw.cpu_p95_pct and raw.cpu_peak_pct and raw.cpu_p95_pct > 0:
        cpu_variance = raw.cpu_peak_pct / raw.cpu_p95_pct
    mem_variance = None
    if raw.mem_p95_pct and raw.mem_peak_pct and raw.mem_p95_pct > 0:
        mem_variance = raw.mem_peak_pct / raw.mem_p95_pct
    _disk_bytes = disk_total_bytes(raw.block_devices or [])
    disk_total_gb_val: float | None = round(bytes_to_gb(_disk_bytes) or 0.0, 1) if _disk_bytes else None
    return ReportRowItem(
        server_id=raw.server_id,
        public_id=raw.public_id,
        hostname=raw.hostname,
        role=infer_role(raw.services, raw.listen_ports),
        is_online=is_online,
        os_family=raw.os_family,
        is_partial=is_partial,
        confidence_notes=build_host_confidence_notes(host),
        os_display=_os_display(raw.os_id, raw.os_version, raw.kernel_version, raw.product_name),
        kernel_version=raw.kernel_version,
        os_eol=os_eol,
        os_eol_status=os_eol_status,
        os_eol_label=os_eol_disp.label,
        os_eol_css=os_eol_disp.css,
        os_eol_title=os_eol_disp.title,
        os_eol_sort=os_eol_disp.sort,
        has_operational_event=has_operational_event,
        internal_ip=next(
            (
                a.get("address")
                for i in raw.net_interfaces or []
                if not is_virtual_interface(i.get("kind"))
                for a in json_list(i, "addresses")
                if a.get("family") == "ipv4"
            ),
            None,
        ),
        cpu_cores=raw.cpu_cores,
        mem_total_gb=bytes_to_gib(raw.mem_total_bytes),
        disk_total_gb=disk_total_gb_val,
        cpu_p95_pct=raw.cpu_p95_pct,
        cpu_avg_pct=raw.cpu_avg_pct,
        cpu_peak_pct=raw.cpu_peak_pct,
        mem_p95_pct=raw.mem_p95_pct,
        mem_avg_pct=raw.mem_avg_pct,
        mem_peak_pct=raw.mem_peak_pct,
        cpu_run_queue_p95=raw.cpu_run_queue_p95,
        mem_swap_paging=raw.mem_swap_paging,
        root_cause_label=right_sizing.root_cause_display(host),
        net_status_label=net_status_label,
        net_congested=host.network_congested,
        recommendation=rec,
        recommendation_label=right_sizing.RECOMMENDATION_LABEL_KO[rec],
        badge_class=BADGE_CLASS[rec],
        risk_level=risk_level,
        risk_label=risk_label,
        risk_badge_class=risk_badge_class,
        iowait_p95_pct=raw.iowait_p95_pct,
        iowait_peak_pct=raw.iowait_peak_pct,
        worst_mount_used_pct=raw.worst_mount_used_pct,
        disk_capacity_driving_mount=raw.disk_capacity_driving_mount,
        disk_capacity_runway_days=(
            int(raw.disk_capacity_runway_days) if raw.disk_capacity_runway_days is not None else None
        ),
        disk_inode_driving_mount=raw.disk_inode_driving_mount,
        disk_inode_runway_days=int(raw.disk_inode_runway_days) if raw.disk_inode_runway_days is not None else None,
        uptime_days=uptime_days,
        reboot_count=raw.reboot_count,
        agent_restart_count=raw.agent_restart_count,
        cpu_variance_ratio=cpu_variance,
        mem_variance_ratio=mem_variance,
        disk_iops_baseline=raw.disk_iops_baseline,
        disk_iops_p95=raw.disk_iops_p95,
        disk_iops_peak=raw.disk_iops_peak,
        disk_throughput_kbps=raw.disk_throughput_kbps,
        disk_throughput_kbps_p95=raw.disk_throughput_kbps_p95,
        disk_throughput_kbps_peak=raw.disk_throughput_kbps_peak,
        net_rx_kbps=raw.net_rx_kbps,
        net_rx_kbps_p95=raw.net_rx_kbps_p95,
        net_rx_kbps_peak=raw.net_rx_kbps_peak,
        net_tx_kbps=raw.net_tx_kbps,
        net_tx_kbps_p95=raw.net_tx_kbps_p95,
        net_tx_kbps_peak=raw.net_tx_kbps_peak,
        diagnosis=(
            _build_insufficient_reason(raw, is_online)
            if rec == "insufficient_data"
            else (("" if is_online else "오프라인 · ") + _build_diagnosis(host, raw, cpu_variance, mem_variance))
        ),
        recommendation_action=_build_recommendation_action(host, stats),
        workload_groups=workload_groups,
        workload_categories=workload_categories,
        signature_workload_categories=signature_workload_categories,
        workload_services=workload_services,
        listen_ports_detail=listen_ports_detail,
    )
