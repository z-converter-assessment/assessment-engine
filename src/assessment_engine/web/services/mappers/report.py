"""보고서 row mapper — ReportRowRaw → ReportRowItem + 보조 집계·요약 (P2).

서버 보고서 본문 행·KPI 합계·정성 요약을 본 모듈에서 합성한다. 양식 분리:
- view='customer' (양식 A): 고객 KPI · risk_level 3단계 압축 · 즉시 액션 시그널.
- view='engineer' (양식 B): USE Method 분류 6단 · 진단(diagnosis) · 권고 · confidence 단서.
"""

from collections import Counter, defaultdict
from datetime import UTC, date, datetime

from assessment_engine import recommendation
from assessment_engine.db.dtos.outbound import ReportRowRaw
from assessment_engine.service_classifier import detect_listen_categories
from assessment_engine.web.services.device_filters import disk_total_bytes
from assessment_engine.web.services.mappers.server import (
    _os_display,
    _services_or_none,
    _to_listen_port_item,
    infer_role,
)
from assessment_engine.web.services.mappers.shared import (
    _CAPACITY_IMMINENT_DAYS,
    OS_FAMILY_LABEL_KO,
    RISK_LEVEL_ORDER,
    ReportView,
    build_confidence_notes,
    resolve_os_eol,
    saturation_axis_displays,
)
from assessment_engine.web.services.unit_converter import bytes_to_gb, kb_to_gb
from assessment_engine.web.view_models.report import (
    ReportListenItem,
    ReportRowItem,
    ReportServiceUnit,
    ReportTotals,
    ReportWorkloadGroup,
    SaturationAxis,
)

# ─── 위험도 매핑 — 양식 A KPI 3단계 압축 ────────────────────────────────
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

# 보고서 row 임계 — recommendation 도메인 상수 활용 + 보고서 표시 전용 임계.
_VARIANCE_BURST_RATIO = 1.5  # peak/p95 >= 1.5 — variance burst 표시 (보고서 전용 임계)
_REBOOT_UNSTABLE_COUNT = 3  # reboot_count >= 3 — Agent 불안정 신호 (#F10 attention 임계)


# ─── KPI 집계 ───


def compute_report_avg_p95(rows: list) -> tuple[float | None, float | None]:
    """ReportRowItem list에서 CPU·메모리 p95 평균을 계산 (양식 A KPI).

    None 항목은 제외 후 산술 평균. 모두 None이면 None 반환 (divide-by-zero 회피).
    """
    cpu_vals = [r.cpu_p95_pct for r in rows if r.cpu_p95_pct is not None]
    mem_vals = [r.mem_p95_pct for r in rows if r.mem_p95_pct is not None]
    avg_cpu = sum(cpu_vals) / len(cpu_vals) if cpu_vals else None
    avg_mem = sum(mem_vals) / len(mem_vals) if mem_vals else None
    return avg_cpu, avg_mem


def compute_report_totals_from_raw(raws: list) -> ReportTotals:
    """ReportRowRaw list -> 묶음 자원 총량. cpu_cores·mem_total_kb·디스크 총량 합산 (P2).

    양식 A 상단의 마이그레이션 capacity 산정 입력 — "총 N대 = 총 X vCPU·Y GB·Z TB".
    디스크는 `disk_total_bytes` 단일 산식 — 환경 overview·세부 목록·export 와 동일 (Windows fallback 포함).
    """
    total_vcpus = sum(r.cpu_cores or 0 for r in raws)
    total_mem_kb = sum(r.mem_total_kb or 0 for r in raws)
    total_disk_bytes = sum(disk_total_bytes(r.disks or [], r.inventory_mounts or []) for r in raws)
    return ReportTotals(
        total_vcpus=total_vcpus,
        total_memory_gb=round(total_mem_kb / 1024 / 1024, 1),
        total_disk_gb=int(bytes_to_gb(total_disk_bytes) or 0),
    )


def build_role_distribution(raws: list) -> dict[str, int]:
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
    os_summary = " / ".join(
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


# ─── 정성 요약 (양식 A/B 분기) ───


def _top_phrase(labels: list[str]) -> str:
    """요약 불릿의 호스트 나열 — 상위 3개 + 초과 시 ' 외' (P2, 6개 신호 공통 반복 제거)."""
    return f"{', '.join(labels[:3])}{' 외' if len(labels) > 3 else ''}"


def build_report_summary_bullets(
    rows: list, raws: list | None = None, view: ReportView = "customer", today: date | None = None
) -> list[str]:
    """자동 분석 요약 문장 생성 — 정량 신호 기반 정성 요약 (P2).

    view 분기:
    - customer(양식 A): 고객 의사결정 직결 시그널만. 고위험·주의·디스크 임박·I/O 병목·재부팅·OS EOL.
    - engineer(양식 B): customer 시그널 + 엔지니어 분석 시그널 (역할별 평균 CPU·Saturation·CPU 변동성).

    호출자는 ReportRowItem list + 선택적 ReportRowRaw list 전달.
    raws가 있으면 OS EOL 신호 생성 (raws.os_id/os_version 사용).
    빈 리스트면 ["대상 서버 없음"] 반환.
    """
    if not rows:
        return ["대상 서버 없음."]

    bullets: list[str] = []
    # 자원 부족 / 효율화 권장 줄 — KPI grid 에서 이미 카운트 노출. summary_bullets 에서 중복 제거 (사용자 의도).

    # 디스크 I/O 포화 신호 — OS별 정규화(disk_io_saturated: Linux iowait / Windows disk_queue). 디스크 병목 = 고객 의사결정 직결.
    # build_resource_stats 는 ReportRowRaw 필요(cpu_sufficiency 등 raw 축) — raws 있을 때만 산출 (OS EOL 신호와 동일 게이트).
    if raws:
        disk_sat_raws = [r for r in raws if recommendation.disk_io_saturated(build_resource_stats(r))]
        if disk_sat_raws:
            phrase = _top_phrase([r.hostname for r in disk_sat_raws])
            bullets.append(f"디스크 I/O 포화 {len(disk_sat_raws)}대 ({phrase}) — 디스크 병목.")

    # Mount 임박 — _CAPACITY_IMMINENT_DAYS 안 채워질 마운트가 있는 서버
    mount_hosts = [
        f"{r.hostname}({r.worst_mount} {r.worst_mount_days_until_full}일)"
        for r in rows
        if r.worst_mount_days_until_full is not None and r.worst_mount_days_until_full <= _CAPACITY_IMMINENT_DAYS
    ]
    if mount_hosts:
        bullets.append(f"디스크 채움 임박 {len(mount_hosts)}대 ({_top_phrase(mount_hosts)}).")

    # 재부팅 빈번 — period 안 _REBOOT_UNSTABLE_COUNT 이상
    reboot_hosts = [f"{r.hostname}({r.reboot_count}회)" for r in rows if r.reboot_count >= _REBOOT_UNSTABLE_COUNT]
    if reboot_hosts:
        bullets.append(f"재부팅 빈번 {len(reboot_hosts)}대 ({_top_phrase(reboot_hosts)}).")

    if view == "engineer":
        # 역할별 평균 CPU — 엔지니어가 자원 집약 역할 식별. 고객 보고서엔 정보 과다. (#F10 recommendation 상수)
        role_cpu: defaultdict[str, list[float]] = defaultdict(list)
        for r in rows:
            if r.cpu_p95_pct is not None:
                role_cpu[r.role].append(r.cpu_p95_pct)
        if role_cpu:
            top_cpu_role = max(role_cpu, key=lambda k: sum(role_cpu[k]) / len(role_cpu[k]))
            top_cpu_avg = sum(role_cpu[top_cpu_role]) / len(role_cpu[top_cpu_role])
            if top_cpu_avg >= recommendation.CPU_UPSIZE_P95_PCT:
                bullets.append(f"{top_cpu_role} 계열 서버의 평균 CPU p95가 {top_cpu_avg:.0f}%로 높게 관찰됨.")

        # CPU 포화 — os-aware cpu_saturated(Linux load/cores / Windows run queue/cores). 분류 cpu_saturation trigger
        # 와 동일 신호(임계 재계산 0)라 run queue 로 under_provisioned 분류된 Windows 호스트가 요약에서 누락되지
        # 않는다(B1). build_resource_stats 필요 -> raws 있을 때만(disk_io bullet 와 동일 게이트).
        if raws:
            sat_hosts = [r.hostname for r in raws if recommendation.cpu_saturated(build_resource_stats(r))]
            if sat_hosts:
                bullets.append(
                    f"CPU 포화 {len(sat_hosts)}대 ({_top_phrase(sat_hosts)}) — run queue/load 가 코어 처리 한계 초과."
                )

        # 변동성 큼 — cpu peak/p95 임계 초과 + peak 가 sizing 유의미 수준(downsize 헤드룸선 초과)일 때만.
        # 미세값 지터(저부하 호스트) 오탐 방지 — _build_diagnosis 의 variance gate 와 동일 기준. sizing 전략 시그널.
        var_hosts = [
            r.hostname
            for r in rows
            if r.cpu_variance_ratio is not None
            and r.cpu_variance_ratio >= _VARIANCE_BURST_RATIO
            and r.cpu_peak_pct is not None
            and r.cpu_peak_pct > recommendation.CPU_DOWNSIZE_P95_PCT
        ]
        if var_hosts:
            bullets.append(
                f"CPU 부하 변동 큼 {len(var_hosts)}대 ({_top_phrase(var_hosts)}) — 일시 spike 빈번 (부하 변동성 큼)."
            )

    # OS EOL 신호 — raws 있을 때만. attention 카드와 동일 판정(resolve_os_eol): Windows build /
    # Linux os_version + EOL 경과 한정. today 미주입 시 현재 UTC (caller 주입 권장).
    if raws:
        eol_today = today or datetime.now(UTC).date()
        eol_hosts: list[str] = []
        for r in raws:
            result = resolve_os_eol(r.os_id, r.os_version, r.kernel_version, eol_today)
            if result:
                eol_iso, label = result
                eol_hosts.append(f"{r.hostname}({label}, EOL {eol_iso})")
        if eol_hosts:
            bullets.append(f"OS EOL {len(eol_hosts)}대 ({_top_phrase(eol_hosts)}) — 보안 패치 중단됨.")

    return bullets


# ─── 자동 진단·권고 helper (양식 B 컬럼) ───


def _build_recommendation_action(assessment: recommendation.Assessment, stats: recommendation.ResourceStats) -> str:
    """recommendation 분류 -> "권고" 컬럼 단일 문구. 조치 semantic 은 recommendation.recommend_action 단일 진실.

    under_provisioned 는 근본원인 기반 처방(`recommendation.under_prescription`), 그 외는 도메인 조치 층
    (유휴는 강도로 즉시 종료/통합 분기). customer "조치 필요 호스트"(high 만)엔 optimal/insufficient_data 미노출.
    """
    rec = assessment.recommendation
    if rec == "under_provisioned":
        # root 기반 처방 — 근본원인(root_cause)과 정합, 하류 증상 삼중 처방 방지 (recommendation.under_prescription).
        return recommendation.under_prescription(recommendation.rollup_host(stats))
    return recommendation.recommend_action(rec, stats)


def _build_diagnosis(
    raw: ReportRowRaw,
    stats: recommendation.ResourceStats,
    cpu_variance: float | None,
    mem_variance: float | None,
) -> str:
    """saturation·variance·iowait·disk·swap·mem·cpu 종합 자동 진단 — 엔지니어 "진단" 칼럼.

    saturation 3축은 os-aware helper 단일 진실 경유(assess 와 동일 신호) — Windows 도 CPU run queue·
    메모리 페이징·디스크 큐로 진단된다(분류가 under 인데 진단이 "정상"으로 어긋나는 것 방지).
    우선순위 (가장 시급한 신호 1개 선택, 임계는 recommendation 상수·_VARIANCE_BURST_RATIO 단일 진실):
    1. mem_saturated (os-aware: Linux swap page-out / Windows Pages Input/sec 하드폴트) → "메모리 부족" — 1차 강신호
    2. disk_io_saturated (os-aware: Linux await>20ms / Windows Avg Disk Queue Length) → "디스크 I/O 병목"
    3. cpu_saturated (os-aware: Linux procs_running/cores / Windows run queue/cores) → "CPU 포화"
    4. mem_p95 >= MEM_UPSIZE_P95_PCT → "메모리 압박"
    5. cpu_p95 >= CPU_UPSIZE_P95_PCT → "CPU 압박"
    6. worst_mount >= DISK_CAPACITY_UPSIZE_PCT → "디스크 용량 임박" (disk_capacity under 근거 노출)
    7. cpu/mem variance >= _VARIANCE_BURST_RATIO AND peak 가 sizing 유의미 수준(downsize 헤드룸선 초과) → "부하 변동 큼"
    8. cpu_p95 <= IDLE_CPU_P95_PCT → "거의 미사용"
    9. cpu_p95 <= CPU_DOWNSIZE_P95_PCT and mem_p95 <= MEM_DOWNSIZE_P95_PCT → "여유 있음"
    10. 그 외 → "정상"
    """
    if recommendation.mem_saturated(stats):
        return "메모리 부족 (페이징 과다)" if raw.os_family == "windows" else "메모리 부족 (스왑 발생)"
    if recommendation.disk_io_saturated(stats):
        return "디스크 I/O 병목"
    if recommendation.cpu_saturated(stats):
        return "CPU 포화"
    if raw.mem_p95_pct is not None and raw.mem_p95_pct >= recommendation.MEM_UPSIZE_P95_PCT:
        return "메모리 압박"
    if raw.cpu_p95_pct is not None and raw.cpu_p95_pct >= recommendation.CPU_UPSIZE_P95_PCT:
        return "CPU 압박"
    # 디스크 용량 임박 — worst mount >= 85%. assess 의 disk_capacity trigger 와 동일 축(임계 재계산 없음).
    # 이 분기가 없으면 disk_capacity 단독 under 호스트(CPU/메모리 한가한 파일·백업 서버)가 진단에서
    # "여유 있음"으로 새어 분류(자원 부족)·권고(디스크 증설)와 모순된다.
    if raw.worst_mount_used_pct is not None and raw.worst_mount_used_pct >= recommendation.DISK_CAPACITY_UPSIZE_PCT:
        return "디스크 용량 임박"
    # 부하 변동 큼 — peak/p95 비율이 커도 peak 가 sizing 유의미 수준(downsize 헤드룸선 초과)일 때만 발화.
    # 미세값 지터(예: peak 1.3%)의 큰 비율은 sizing 신호가 아니라 노이즈 — "거의 미사용"(7)을 가로채지 않게 gate.
    cpu_burst = (
        cpu_variance is not None
        and cpu_variance >= _VARIANCE_BURST_RATIO
        and raw.cpu_peak_pct is not None
        and raw.cpu_peak_pct > recommendation.CPU_DOWNSIZE_P95_PCT
    )
    mem_burst = (
        mem_variance is not None
        and mem_variance >= _VARIANCE_BURST_RATIO
        and raw.mem_peak_pct is not None
        and raw.mem_peak_pct > recommendation.MEM_DOWNSIZE_P95_PCT
    )
    if cpu_burst or mem_burst:
        return "부하 변동 큼"
    if raw.cpu_p95_pct is not None and raw.cpu_p95_pct <= recommendation.IDLE_CPU_P95_PCT:
        return "거의 미사용"
    if (
        raw.cpu_p95_pct is not None
        and raw.cpu_p95_pct <= recommendation.CPU_DOWNSIZE_P95_PCT
        and raw.mem_p95_pct is not None
        and raw.mem_p95_pct <= recommendation.MEM_DOWNSIZE_P95_PCT
    ):
        return "여유 있음"
    return "정상"


def _build_insufficient_reason(raw: ReportRowRaw, is_online: bool) -> str:
    """insufficient_data 호스트의 원인 순차 진단 — 진단 컬럼 단일 진실 (별도 카드 폐기, 호스트 권고 통합).

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
        if raw.disk_queue_p95 is None:
            missing.append("디스크 큐")
    else:
        if raw.procs_running_p95 is None:
            missing.append("실행 큐")
        if raw.disk_await_p95_ms is None:
            missing.append("디스크 응답(await)")
    if raw.worst_mount_used_pct is None:
        missing.append("디스크")
    return f"메트릭 수집 누락: {' · '.join(missing)}" if missing else "윈도우 내 표본 부족"


def _build_saturation_axes(stats: recommendation.ResourceStats) -> list[SaturationAxis]:
    """USE Saturation 3축 os-aware 평가 행 — single_report '포화 축 평가' 카드(P2/P3 precompute).

    표시값(신호·값·임계)은 `shared.saturation_axis_displays` 단일 진실(attention capacity 지표와 공유 —
    표기 drift 차단). 판정(포화/정상/미관측)은 os-aware helper 경유(임계 재계산 0): None=미관측.
    """

    def _st(sat: bool | None) -> tuple[str, str]:
        if sat is None:
            return "미관측", "text-meta"
        return ("포화", "text-strong") if sat else ("정상", "")

    sats = [
        recommendation.cpu_saturated(stats),
        recommendation.mem_saturated(stats),
        recommendation.disk_io_saturated(stats),
    ]
    return [
        SaturationAxis(d.axis, d.signal, d.value, d.threshold, *_st(sat))
        for d, sat in zip(saturation_axis_displays(stats), sats, strict=True)
    ]


# ─── ReportRowRaw -> ReportRowItem (P2 단일 변환) ───


def build_resource_stats(raw: ReportRowRaw) -> recommendation.ResourceStats:
    """ReportRowRaw -> USE Method ResourceStats — report·attention mapper 공용(단일 진실).

    net baseline = server_net_io rx+tx 윈도우 평균(kB/s). 둘 다 None 이면 None(유휴 skip),
    하나만 있으면 다른쪽 0. os_family 전달로 swap 축 OS 분기(P2). attention 의 capacity trigger 도
    동일 stats 로 recommendation.assess 를 타 임계 재계산 중복을 제거(assess.triggers 단일 진실).
    """
    net_avg = (
        None if raw.net_rx_kbps is None and raw.net_tx_kbps is None else (raw.net_rx_kbps or 0) + (raw.net_tx_kbps or 0)
    )
    # 표본 충분성 — 측정된 축(p95 not None)의 sufficiency 만 모아 min(보수적). 둘 다 부재면 None(판정 무관).
    suffs = [
        s
        for p95, s in ((raw.cpu_p95_pct, raw.cpu_sufficiency), (raw.mem_p95_pct, raw.mem_sufficiency))
        if p95 is not None and s is not None
    ]
    return recommendation.ResourceStats(
        cpu_p95_pct=raw.cpu_p95_pct,
        cpu_peak_pct=raw.cpu_peak_pct,
        cpu_load_15m_max=raw.load_15m_max,
        cpu_cores=raw.cpu_cores,
        mem_p95_pct=raw.mem_p95_pct,
        swap_used=raw.swap_used,
        disk_used_pct=raw.worst_mount_used_pct,
        iowait_p95_pct=raw.iowait_p95_pct,
        net_avg_kbps=net_avg,
        os_family=raw.os_family,
        sample_sufficiency=min(suffs) if suffs else None,
        # Windows 디스크 saturation — 가장 바쁜 디스크의 큐 p95 (disk_io_saturated os-aware 소비, 정규화 불요).
        disk_queue_p95=raw.disk_queue_p95,
        # Windows CPU saturation — Processor Queue Length p95 / Memory 는 Pages Input/sec rate p95 (os-aware 소비).
        cpu_run_queue_p95=raw.cpu_run_queue_p95,
        mem_pages_input_rate_p95=raw.mem_pages_input_rate_p95,
        # ─── ADR 0052 신 모델(rollup_host) 입력 — report_aggregate 산출 raw 를 도메인 축으로 배선 ───
        # 가장 바쁜 코어 p95 — 단일스레드 병목 판정(RS_CPU_PERCORE_HOLD). Windows·구 agent 는 None(graceful skip).
        cpu_percore_p95_max=raw.cpu_percore_p95_max,
        procs_blocked_p95=raw.procs_blocked_p95,
        # Linux CPU 포화 신호(load 대체) + OOM 메모리 증거 — cpu_saturated·assess_memory os-aware 소비.
        procs_running_p95=raw.procs_running_p95,
        oom_occurred=raw.oom_occurred,
        mem_swap_paging=raw.mem_swap_paging,
        mem_total_mb=(raw.mem_total_kb // 1024 if raw.mem_total_kb is not None else None),
        disk_await_p95_ms=raw.disk_await_p95_ms,
        disk_capacity_runway_days=raw.disk_capacity_runway_days,
        disk_inode_runway_days=raw.disk_inode_runway_days,
        disk_capacity_target_gb=raw.disk_capacity_target_gb,
        net_retrans_pct=raw.net_retrans_pct,
        net_drop_pct=raw.net_drop_pct,
        history_hours=raw.history_hours,
        cpu_burst_ratio=raw.cpu_burst_ratio,
        # 이용률 상승 추세 — 임계 이진화는 도메인 단일(regr_slope %/day raw -> bool). 다운사이즈 정상성 게이트.
        util_trend_rising=recommendation.util_trend_rising_from_slopes(raw.cpu_trend_slope, raw.mem_trend_slope),
        cpu_steal_p95_pct=raw.cpu_steal_p95_pct,
    )


def _build_workload_display(
    raw: ReportRowRaw,
) -> tuple[list[ReportWorkloadGroup], list[ReportServiceUnit], list[ReportListenItem]]:
    """개별 서버 보고서 구동 서비스 표시 precompute (P2) — 차등 구성.

    customer: 워크로드 카테고리별 제품명 묶음 (의미 중심, 포트 숨김).
    engineer: 등록 서비스(systemd unit) 전체 표 + listen 포트 전체 표 (사실 중심, 최대 상세).
    service_classifier 단일 진실 (#E7) — listen-only 카테고리는 detect_listen_categories 로 보강(이름 미상).
    """
    services = _services_or_none(raw.services, raw.listen_ports) or []
    # engineer — 등록 unit 전체 (unknown 포함, 최대 상세). 귀속 포트 join 은 mapper precompute (P3).
    units = [
        ReportServiceUnit(
            unit=si.unit or "(이름 없음)",
            category=si.category,
            ports_label=", ".join(f"{p.port}/{p.proto}" for p in si.ports),
        )
        for si in services
    ]
    units.sort(key=lambda u: (u.category, u.unit))
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
    return groups, units, listen


def to_report_row_item(raw: ReportRowRaw, is_online: bool, now: datetime) -> ReportRowItem:
    """ReportRowRaw(repo) + is_online + now -> ReportRowItem(ViewModel) — P2 단일 변환.

    `now`로 uptime_days 계산 (now - boot_time).
    표시 파생 (role / recommendation / risk_level / badge_class / os_display / internal_ip[0])은 모두 여기서.
    USE Method 분류(`recommendation`)는 양식 B(엔지니어용)·`risk_level`은 양식 A(고객용) KPI/표 노출.
    `diagnosis`는 양식 B "판단" 컬럼 자동 해석.
    """
    stats = build_resource_stats(raw)  # net baseline·OS 분기 포함 — report·attention 공용 단일 진실
    assessment = recommendation.assess(stats)  # 분류 + 근거(triggers) + 미관측 축 단일 평가
    workload_groups, service_units, listen_ports_detail = _build_workload_display(raw)
    rec = assessment.recommendation
    is_partial = assessment.is_partial  # P4 — saturation 축 미관측(예: Windows perflib 미발행) confidence 단서
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
    # 인벤토리 디스크 총량 — 물리 disks 우선, 비면(Windows) 파일시스템 mounts fallback.
    # device_filters.disk_total_bytes 단일 산식 (개별·환경 보고서와 동일, Windows 포함 일관).
    _disk_bytes = disk_total_bytes(raw.disks or [], raw.inventory_mounts or [])
    disk_total_gb_val: float | None = round(bytes_to_gb(_disk_bytes) or 0.0, 1) if _disk_bytes else None
    return ReportRowItem(
        server_id=raw.server_id,
        public_id=raw.public_id,
        hostname=raw.hostname,
        role=infer_role(raw.services, raw.listen_ports),
        is_online=is_online,
        os_family=raw.os_family,
        is_partial=is_partial,
        confidence_notes=build_confidence_notes(assessment),
        os_display=_os_display(raw.os_id, raw.os_version),
        kernel_version=raw.kernel_version,
        internal_ip=next(
            (i["address"] for i in raw.interfaces or [] if i.get("kind") == "physical" and i.get("family") == "ipv4"),
            None,
        ),
        cpu_cores=raw.cpu_cores,
        mem_total_gb=kb_to_gb(raw.mem_total_kb),
        disk_total_gb=disk_total_gb_val,
        cpu_p95_pct=raw.cpu_p95_pct,
        cpu_avg_pct=raw.cpu_avg_pct,
        cpu_peak_pct=raw.cpu_peak_pct,
        mem_p95_pct=raw.mem_p95_pct,
        mem_avg_pct=raw.mem_avg_pct,
        mem_peak_pct=raw.mem_peak_pct,
        load_15m_max=raw.load_15m_max,
        swap_used=raw.swap_used,
        recommendation=rec,
        recommendation_label=recommendation.LABEL_KO[rec],
        badge_class=recommendation.BADGE_CLASS[rec],
        risk_level=risk_level,
        risk_label=risk_label,
        risk_badge_class=risk_badge_class,
        iowait_p95_pct=raw.iowait_p95_pct,
        iowait_peak_pct=raw.iowait_peak_pct,
        worst_mount=raw.worst_mount,
        worst_mount_used_pct=raw.worst_mount_used_pct,
        worst_mount_days_until_full=raw.worst_mount_days_until_full,
        uptime_days=uptime_days,
        reboot_count=raw.reboot_count,
        agent_restart_count=raw.agent_restart_count,
        saturation_axes=_build_saturation_axes(stats),
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
            else (("" if is_online else "오프라인 · ") + _build_diagnosis(raw, stats, cpu_variance, mem_variance))
        ),
        recommendation_action=_build_recommendation_action(assessment, stats),
        workload_groups=workload_groups,
        service_units=service_units,
        listen_ports_detail=listen_ports_detail,
    )
