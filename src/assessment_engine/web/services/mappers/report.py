"""보고서 row mapper — ReportRowRaw → ReportRowItem + 보조 집계·요약 (P2).

서버 보고서 본문 행·KPI 합계·정성 요약을 본 모듈에서 합성한다. 양식 분리:
- view='customer' (양식 A): 고객 KPI · risk_level 3단계 압축 · 즉시 액션 시그널.
- view='engineer' (양식 B): USE Method 분류 6단 · 진단(diagnosis) · 권고 · confidence 단서.
"""

from collections import Counter
from typing import TYPE_CHECKING

from assessment_engine import recommendation
from assessment_engine.json_types import json_list
from assessment_engine.service_classifier import SIGNATURE_CATEGORIES, detect_listen_categories
from assessment_engine.web.services.device_filters import disk_total_bytes, is_virtual_interface
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
from assessment_engine.web.services.mappers.shared import (
    _VARIANCE_BURST_RATIO,
    BADGE_CLASS,
    OS_FAMILY_LABEL_KO,
    RISK_LEVEL_ORDER,
    build_host_confidence_notes,
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

# --- 위험도 매핑 — 양식 A KPI 3단계 압축 --------------------------------
# USE Method 분류 -> (risk_level, 한글 라벨, badge CSS 클래스).
# under_provisioned → 고위험 (자원 부족, 즉시 조치)
# idle · over_provisioned → 주의 (미사용·과다 — 운영자 점검)
# optimal · insufficient_data → 정상 (또는 표본 부족)
_RISK_FROM_RECOMMENDATION: dict[str, tuple[str, str, str]] = {
    "under_provisioned": ("high", "고위험", "rec-under_provisioned"),
    "idle": ("attention", "주의 필요", "rec-over_provisioned"),
    "over_provisioned": ("attention", "주의 필요", "rec-over_provisioned"),
    "optimal": ("normal", "정상", "rec-optimal"),
    "insufficient_data": ("normal", "정상", "rec-optimal"),
}

# 네트워크 상태 라벨 — assess_network status -> 표시(사이징 분류와 별개 품질 판정). attention 표와 동일 어휘.
_NET_STATUS_LABEL: dict[str, str] = {"quality_ok": "정상", "congested": "혼잡", "unmeasured": "미측정"}

# --- KPI 집계 ---


def compute_report_avg_p95(rows: list[ReportRowItem]) -> tuple[float | None, float | None]:
    """ReportRowItem list에서 CPU·메모리 p95 평균을 계산 (양식 A KPI).

    None 항목은 제외 후 산술 평균. 모두 None이면 None 반환 (divide-by-zero 회피).
    """
    cpu_vals = [r.cpu_p95_pct for r in rows if r.cpu_p95_pct is not None]
    mem_vals = [r.mem_p95_pct for r in rows if r.mem_p95_pct is not None]
    avg_cpu = sum(cpu_vals) / len(cpu_vals) if cpu_vals else None
    avg_mem = sum(mem_vals) / len(mem_vals) if mem_vals else None
    return avg_cpu, avg_mem


def compute_report_totals_from_raw(raws: list[ReportRowRaw]) -> ReportTotals:
    """ReportRowRaw list -> 묶음 자원 총량. cpu_cores·mem_total_bytes·디스크 총량 합산 (P2).

    양식 A 상단의 마이그레이션 capacity 산정 입력 — "총 N대 = 총 X vCPU·Y GB·Z TB".
    디스크는 `disk_total_bytes` 단일 산식 — 환경 overview·세부 목록·export 와 동일 (type=disk 합, 양 OS 일관).
    """
    total_vcpus = sum(r.cpu_cores or 0 for r in raws)
    total_mem_bytes = sum(r.mem_total_bytes or 0 for r in raws)
    total_disk_bytes = sum(disk_total_bytes(r.block_devices or []) for r in raws)
    return ReportTotals(
        total_vcpus=total_vcpus,
        total_memory_gb=bytes_to_gib(total_mem_bytes) or 0.0,
        total_disk_gb=int(bytes_to_gb(total_disk_bytes) or 0),
    )


def build_role_distribution(raws: list[ReportRowRaw]) -> dict[str, int]:
    """ReportRowRaw list -> 역할별 서버 수 dict. 양식 A 상단 표시용 (호스트 대표 역할, listen 보강)."""
    counter: Counter[str] = Counter()
    for r in raws:
        counter[infer_role(r.services, r.listen_ports)] += 1
    return dict(counter.most_common())


def build_selection_context(items: list[ReportRowItem], role_distribution: dict[str, int]) -> tuple[str, str]:
    """N대 보고서 선택 맥락 (P2) — OS family·워크로드 한 줄 요약 텍스트. 작은 N 맥락 (막대 대신).

    os_family_summary: "Linux 2 / Windows 1" · workload_summary: "web 2, db 1" (count DESC).
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
    """N대 비교 표 정렬 (P2) — 위험 우선(under->attention->normal), 동순위 cpu_p95 DESC, hostname ASC."""
    return sorted(
        items,
        key=lambda it: (RISK_LEVEL_ORDER.get(it.risk_level, 99), -(it.cpu_p95_pct or 0.0), it.hostname),
    )


def _build_recommendation_action(host: recommendation.HostAssessment, stats: recommendation.ResourceStats) -> str:
    """recommendation 분류 -> "권고" 컬럼 단일 문구. 조치 semantic 은 recommendation 단일 진실 (신 모델 host 기반).

    under_provisioned 는 근본원인 기반 처방(`under_prescription`, root_cause 정합·삼중 처방 방지),
    그 외는 도메인 조치 층(유휴는 강도로 즉시 종료/통합). customer "조치 필요 호스트"(high)엔 optimal 미노출.
    """
    rec = recommendation.host_status_to_recommendation(host.host_status)
    if rec == "under_provisioned":
        return recommendation.under_prescription(host)
    return recommendation.recommend_action(rec, stats)


def _build_diagnosis(
    host: recommendation.HostAssessment,
    raw: ReportRowRaw,
    cpu_variance: float | None,
    mem_variance: float | None,
) -> str:
    """엔지니어 "진단" 칼럼 — `rollup_host` 자원별 판정에서 파생하므로 배지와 정합이 보장된다.

    under 분기(1~6)는 host.resources 상태·trigger 를 직접 읽는다 — 임계를 다시 계산하지 않으므로 분류와
    어긋날 수 없다. host_status != under 면 어느 자원도 under/io_bound/filling 이 아니므로 1~6 을 건너뛴다.
    우선순위 (가장 시급한 신호 1개):
    1. 메모리 under + 페이징/OOM (os-aware: Linux swap page-out / Windows Pages Input/sec) → "메모리 부족"
    2. disk_io io_bound (os-aware: Linux await>20ms / Windows await·큐) → "디스크 I/O 병목"
    3. cpu under + 실행 큐 포화 → "CPU 포화"
    4. 메모리 under + 이용률(>=90) → "메모리 압박"
    5. cpu under + 이용률(>=70) → "CPU 압박"
    6. disk_capacity filling (runway<30일 or 정적 85%/inode) → "디스크 용량 임박"
    7. network 혼잡 (품질 orthogonal, 사이징 아님) → "네트워크 혼잡"
    8. cpu/mem variance burst (peak 가 BURST_PEAK_FLOOR 초과) → "부하 변동 큼"
    9. host_status == idle (활동 3축 quiescent) → "거의 미사용"
    10. host_status == over (target<cores 다운사이즈 여유) → "여유 있음"
    11. 그 외(optimal) → "정상"
    """
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
    # 이하 비-under 호스트 — burst/idle/여유/정상. idle·여유는 배지(host_status)에서 직접 파생해 항상 정합.
    # 부하 변동 큼 — peak/p95 비율이 커도 peak 가 유의미 저부하선(BURST_PEAK_FLOOR) 초과일 때만 발화(지터 오탐 방지).
    cpu_burst = (
        cpu_variance is not None
        and cpu_variance >= _VARIANCE_BURST_RATIO
        and raw.cpu_peak_pct is not None
        and raw.cpu_peak_pct > recommendation.BURST_PEAK_FLOOR_CPU_PCT
    )
    mem_burst = (
        mem_variance is not None
        and mem_variance >= _VARIANCE_BURST_RATIO
        and raw.mem_peak_pct is not None
        and raw.mem_peak_pct > recommendation.BURST_PEAK_FLOOR_MEM_PCT
    )
    if cpu_burst or mem_burst:
        return "부하 변동 큼"
    # 미사용/여유 = 배지 host_status 그대로 (idle 은 활동 3축, over 는 target<cores 사이징) — 텍스트-배지 divergence 0.
    if host.host_status == "idle":
        return "거의 미사용"
    if host.host_status == "over":
        return "여유 있음"
    return "정상"


def _build_insufficient_reason(raw: ReportRowRaw, is_online: bool) -> str:
    """insufficient_data 호스트의 원인 순차 진단 — 진단 컬럼이 단일 진실이고 호스트 권고에 통합돼 있다.

    1순위: 오프라인 — 에이전트 미가동 (메트릭 자연스러운 부재, root cause).
    2순위: 온라인이나 메트릭 수집 누락 — 누락 메트릭 명시. saturation 축은 분류가 쓰는 os-aware 축으로
           (Linux 실행 큐 procs_running·디스크 await / Windows run queue·디스크 큐). Windows 에 없는 축을
           "누락"으로 나열하면 개념 부재를 수집 실패로 오도하므로 os-aware 로 구분.
    3순위: 모든 메트릭 있지만 표본 부족 (윈도우 미만).
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
    # 디스크 응답(await)은 양 OS 공통 포화 신호 (op_time delta).
    if raw.disk_await_p95_ms is None:
        missing.append("디스크 응답(await)")
    if raw.worst_mount_used_pct is None:
        missing.append("디스크")
    return f"메트릭 수집 누락: {' · '.join(missing)}" if missing else "윈도우 내 표본 부족"


def _build_workload_display(
    raw: ReportRowRaw,
) -> tuple[list[ReportWorkloadGroup], list[ReportListenItem]]:
    """개별 서버 보고서 구동 서비스 표시 precompute (P2) — 차등 구성.

    customer: 워크로드 카테고리별 제품명 묶음 (의미 중심, 포트 숨김).
    engineer: listen 포트 전체 표 (사실 중심, 최대 상세 — Listen 포트 카드).
    service_classifier 단일 진실 (#E7) — listen-only 카테고리는 detect_listen_categories 로 보강(이름 미상).
    """
    services = _services_or_none(raw.services, raw.listen_ports) or []
    # engineer — listen 포트 전체 (raw 표시본)
    listen = [
        ReportListenItem(port=lp.port, proto=lp.proto, comm=lp.comm or "", addr=lp.addr, uid=lp.uid, pid=lp.pid)
        for lp in (_to_listen_port_item(p) for p in (raw.listen_ports or []))
    ]
    listen.sort(key=lambda x: (x.port, x.proto))
    # customer — 카테고리별 제품명 묶음 (unknown 제외) + listen-only 카테고리 보강(이름 미상).
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
    """ReportRowRaw(repo) + is_online + now -> ReportRowItem(ViewModel) — P2 단일 변환.

    `now`로 uptime_days 계산 (now - boot_time) + OS EOL 판정 기준 시각(now.date()) — 정적 스냅샷이라
    렌더 시점 "오늘" 아닌 보고서 발행 기준(anchor/generated_at)으로 고정(#C1 스냅샷 불변).
    표시 파생 (role / recommendation / risk_level / badge_class / os_display / internal_ip[0])은 모두 여기서.
    USE Method 분류(`recommendation`)는 양식 B(엔지니어용)·`risk_level`은 양식 A(고객용) KPI/표 노출.
    `diagnosis`는 양식 B "판단" 컬럼 자동 해석.
    has_operational_event — 호출자가 보고서 창(window) 기준 latest_errors 로 사전 판정해 주입(세부 서버
    목록 전용, N+1 회피 위해 여기선 조회 안 함).
    """
    info = lookup_os_eol(raw.os_id, raw.os_version, raw.kernel_version, now.date())
    os_eol, os_eol_status = ("", "unknown") if info is None else (info.eol_iso, info.status)
    os_eol_disp = os_eol_display(os_eol_status, os_eol)
    # 보고서 경로는 `_assemble_report_raws` 가 disk baseline 을 채워 온 raw 를 받는다 (유일한 주입 경로).
    stats = build_resource_stats(raw, disk_baseline=raw.disk_iops_baseline)
    # rollup_host 1회 산출 — badge·진단·권고·confidence 전부 이 종합에서 파생한다 (화면 간 분류 정합).
    host = recommendation.rollup_host(stats)
    # 네트워크 상태 — 사이징과 별개 품질 판정(정상/혼잡/미측정). assess_network status 를 라벨로.
    net_status_label = _NET_STATUS_LABEL.get(host.resources["network"].status, "미측정")
    workload_groups, listen_ports_detail = _build_workload_display(raw)
    # 특징 워크로드 카테고리·서비스명(baseline 제외) — 환경 개요 뱃지와 동일 소스. 서비스 구성 집계 정합용.
    workload_categories = list(workload_category_counter(raw.services, raw.listen_ports).keys())
    signature_workload_categories = [c for c in workload_categories if c in SIGNATURE_CATEGORIES]
    workload_services = workload_services_by_category(raw.services, raw.listen_ports)
    rec = recommendation.host_status_to_recommendation(host.host_status)
    # P4 — 포화 축 미관측(예: Windows perflib 미발행/구세대 viostor) confidence 단서 (포화 축 한정 단일 진실).
    is_partial = recommendation.host_saturation_unmeasured(host)
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
    # 인벤토리 디스크 총량 — block_devices type=disk 합(disk_total_bytes 단일 산식, 개별·환경 보고서 일관, 양 OS).
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
                if not is_virtual_interface(i.get("kind"))  # physical + bond_master (topology·상세와 동일 술어)
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
        root_cause_label=recommendation.root_cause_display(host),
        net_status_label=net_status_label,
        net_congested=host.network_congested,
        recommendation=rec,
        recommendation_label=recommendation.LABEL_KO[rec],
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
        # 표본 부족 호스트는 USE 진단(메트릭 기반) 대신 원인 진단 — fall-through '정상' 오표시 방지.
        # 오프라인 호스트는 진단에 "오프라인" 접두 — 분류는 윈도우 측정 기반 유지, 현재 미가동 맥락만 보강.
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
