"""보고서 row mapper — ReportRowRaw → ReportRowItem + 보조 집계·요약 (P2).

서버 보고서 본문 행·KPI 합계·정성 요약을 본 모듈에서 합성한다. 양식 분리:
- view='customer' (양식 A): 고객 KPI · risk_level 3단계 압축 · 즉시 액션 시그널.
- view='engineer' (양식 B): USE Method 분류 6단 · 진단(diagnosis) · 권고 · confidence 단서.
"""

from collections import Counter, defaultdict
from datetime import UTC, date, datetime

from assessment_engine import recommendation
from assessment_engine.db.dtos.outbound import ReportRowRaw
from assessment_engine.service_classifier import SIGNATURE_CATEGORIES, detect_listen_categories
from assessment_engine.web.services.device_filters import disk_total_bytes, is_virtual_interface
from assessment_engine.web.services.mappers.server import (
    _os_display,
    _services_or_none,
    _to_listen_port_item,
    infer_role,
    workload_category_counter,
    workload_services_by_category,
)
from assessment_engine.web.services.mappers.shared import (
    _DONUT_SEGMENT_DEFS,
    _DONUT_SEGMENT_FROM_REC,
    OS_FAMILY_LABEL_KO,
    RISK_LEVEL_ORDER,
    ReportView,
    build_host_confidence_notes,
    lookup_os_eol,
    resolve_os_eol,
    saturation_axis_displays,
)
from assessment_engine.web.services.unit_converter import bytes_to_gb, bytes_to_gib
from assessment_engine.web.view_models.metric import (
    ErrorSignal,
    PeriodAssessment,
    PeriodErrorRow,
    PeriodExtraGroup,
    PeriodResource,
    PeriodSignalRow,
)
from assessment_engine.web.view_models.report import (
    ReportListenItem,
    ReportRowItem,
    ReportTotals,
    ReportWorkloadGroup,
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

# 네트워크 상태 라벨 — assess_network status -> 표시(사이징 분류와 별개 품질 판정). attention 표와 동일 어휘.
_NET_STATUS_LABEL: dict[str, str] = {"quality_ok": "정상", "congested": "혼잡", "unmeasured": "미측정"}

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

    # 디스크 용량 임박 — 분류(assess_disk_capacity filling)와 동일 신호(구동 마운트 runway). 배지와 정합.
    if raws:
        mount_hosts = [
            f"{r.hostname}({r.disk_capacity_driving_mount or '?'} {int(r.disk_capacity_runway_days)}일)"
            for r in raws
            if recommendation.assess_disk_capacity(build_resource_stats(r)).status == "filling"
            and r.disk_capacity_runway_days is not None
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

        # 변동성 큼 — cpu peak/p95 비율 초과 + peak 가 유의미 저부하선(BURST_PEAK_FLOOR) 초과일 때만.
        # 미세값 지터(저부하 호스트) 오탐 방지 — _build_diagnosis 의 burst gate 와 동일 기준. sizing 전략 시그널.
        var_hosts = [
            r.hostname
            for r in rows
            if r.cpu_variance_ratio is not None
            and r.cpu_variance_ratio >= _VARIANCE_BURST_RATIO
            and r.cpu_peak_pct is not None
            and r.cpu_peak_pct > recommendation.BURST_PEAK_FLOOR_CPU_PCT
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


def _build_recommendation_action(
    host: recommendation.HostAssessment, stats: recommendation.ResourceStats
) -> str:
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
    """엔지니어 "진단" 칼럼 — 신 모델(rollup_host) 자원별 판정에서 파생 -> 배지와 보장 정합 (ADR 0052 이관).

    under 분기(1~6)는 host.resources 상태·trigger 에서 직접 읽어 분류와 어긋나지 않는다(구 assess 의 임계
    재계산 제거). host_status != under 면 어느 자원도 under/io_bound/filling 이 아니므로 1~6 을 건너뛴다.
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
    else:
        if raw.procs_running_p95 is None:
            missing.append("실행 큐")
    # 디스크 응답(await)은 양 OS 공통 포화 신호 (v2 op_time delta).
    if raw.disk_await_p95_ms is None:
        missing.append("디스크 응답(await)")
    if raw.worst_mount_used_pct is None:
        missing.append("디스크")
    return f"메트릭 수집 누락: {' · '.join(missing)}" if missing else "윈도우 내 표본 부족"


# ─── 서버 세부 '최근 N일' 평가 카드 (이용률+포화 2축, 14일 p95) — right-sizing 분류 기준 ───


def _pct_str(v: float | None) -> str:
    return f"{v:.1f}%" if v is not None else "N/A"


# 자원별 status -> (소제목 옆 verdict 라벨, 색). 문제 자원(부족·용량임박·I/O병목=빨강 / 혼잡=주황 / 과다=파랑)만
# 색으로 부각, 정상·유휴·미측정은 muted 회색. 라벨은 카드용 간결형(RS_STATUS_LABEL_KO 동계열).
_VERDICT_LABEL = {
    "under": "부족", "over": "과다", "filling": "용량 임박", "io_bound": "I/O 병목",
    "congested": "혼잡", "idle": "유휴", "unmeasured": "미측정", "insufficient": "표본 부족",
}
_VERDICT_COLOR = {
    "under": "#dc2626", "filling": "#dc2626", "io_bound": "#dc2626",
    "congested": "#d97706", "over": "var(--color-title)",
}


def _verdict(status: str) -> tuple[str, str]:
    return _VERDICT_LABEL.get(status, "정상"), _VERDICT_COLOR.get(status, "#94a3b8")


def _period_error_rows(errors: list[ErrorSignal]) -> list[PeriodErrorRow]:
    """ErrorSignal -> 카드 표시행(배지 precompute, P3). occurred=빨강 카운트 / clean=초록 이상없음 / no_data=회색
    "수집 대기"(일시적 미수집) / not_applicable=회색 "N/A"(이 OS 구조적 미지원, 예 Windows EDAC — no_data 와
    구분해 "나중에 나타날 것"으로 오인 표시 안 함).

    OOM(mem_oom)만 분류(자원 부족) 발화 신호라 명시 — 나머지 에러는 표시 전용(사이징 무관, 하드웨어 고장 등).
    """
    rows: list[PeriodErrorRow] = []
    for e in errors:
        if e.state == "occurred":
            text, cls = (f"{e.count}건" if e.count is not None else "발생"), "badge-danger"
            note = e.context or ""
        elif e.state == "clean":
            text, cls, note = "이상 없음", "badge-ok", ""
        elif e.state == "not_applicable":
            text, cls, note = "N/A", "badge-muted", ""
        else:
            text, cls, note = "수집 대기", "badge-muted", ""
        # OOM 은 1건이라도 발생(occurred)하면 "메모리 자원 부족"을 임계 이상급 빨간색으로 명시(assess_memory 즉시 under).
        # 상시 태그 아님 — 실제 발화 때만. 나머지 에러는 사이징 무관(표시 전용).
        sizing = "메모리 자원 부족" if (e.key == "mem_oom" and e.state == "occurred") else ""
        rows.append(
            PeriodErrorRow(key=e.key, label=e.label, badge_text=text, badge_class=cls, note=note, sizing_signal=sizing)
        )
    return rows


def _extra_row(label: str, val: float | None, unit: str, thr: float | None = None, over: bool = False) -> PeriodSignalRow:
    """신뢰도 카드 공용 로우 빌더 — CPU/메모리 등 자원별 extra_groups 가 공유(P2 표현 단일 소스)."""
    value = f"{val:.1f}{unit}" if val is not None else "N/A"
    threshold = f"임계 {thr:g}{unit}" if thr is not None else ""  # 임계 없는 정보성 값 — 괄호 자체 생략(_prows)
    return PeriodSignalRow(label=label, value=value, threshold=threshold, over=over, measured=val is not None)


def _confidence_rows(stats: recommendation.ResourceStats) -> list[PeriodSignalRow]:
    """관측 시간·표본 충분성 — host-level 신뢰도 입력(_base_confidence 공용, ADR 0052). 자원마다 별 신뢰도
    카드에 반복 노출하는 게 설계 의도(per-resource ConfidenceNote) — 값 자체는 host 공통이라 자원 간 동일.
    """
    rec = recommendation
    hours = stats.history_hours
    suff = stats.sample_sufficiency
    return [
        PeriodSignalRow(
            label="관측 시간", value=(f"{hours:.0f}h" if hours is not None else "N/A"),
            threshold=f"최소 {rec.RS_CONFIDENCE_MIN_HOURS:g}h", measured=hours is not None,
            over=hours is not None and hours < rec.RS_CONFIDENCE_MIN_HOURS,
        ),
        PeriodSignalRow(
            label="표본 충분성", value=(f"{suff * 100:.0f}%" if suff is not None else "N/A"),
            threshold=f"최소 {rec.RS_DOWNSIZE_MIN_SUFFICIENCY * 100:g}%", measured=suff is not None,
            over=suff is not None and suff < rec.RS_DOWNSIZE_MIN_SUFFICIENCY,
        ),
    ]


def _cpu_extra_groups(stats: recommendation.ResourceStats) -> list[PeriodExtraGroup]:
    """CPU 상세 탭 "신뢰도" 카드 — U/S 2축 헤드라인 수치를 얼마나 믿을지 보완하는 원신호, 성격별 2그룹(#F9 완전
    노출). 대등한 두 독립 축이 아니라 전부 "신뢰도" 우산 아래 성격 구분 — 부하 신호도 사이징/근본원인 판정
    게이트(코어별 최대=단일스레드 보호, D-state=IO발 로드 오귀속 방지)라 결국 U/S 수치 해석 맥락이다.

    "부하 신호" = 피크·코어별 최대·D-state 블록 (피크는 임계 없는 정보성, over 항상 False). "통계 신뢰도" =
    버스트·steal 편향·관측 시간·표본 충분성 — confidence/근본원인 판정에 쓰이는 실제 임계 재사용(재계산 0).
    """
    rec = recommendation
    percore = stats.cpu_percore_p95_max
    burst = stats.cpu_burst_ratio
    steal = stats.cpu_steal_p95_pct
    load_rows = [
        _extra_row("피크 사용률", stats.cpu_peak_pct, "%"),
        _extra_row(
            "코어별 최대 p95", percore, "%", rec.RS_CPU_PERCORE_HOLD_PCT,
            over=percore is not None and percore >= rec.RS_CPU_PERCORE_HOLD_PCT,
        ),
        _extra_row(
            "D-state 블록 p95", stats.procs_blocked_p95, "", rec.PROCS_BLOCKED_DSTATE_SATURATION,
            over=stats.procs_blocked_p95 is not None and stats.procs_blocked_p95 >= rec.PROCS_BLOCKED_DSTATE_SATURATION,
        ),
    ]
    confidence_rows = [
        _extra_row(
            "버스트 비율(p95/median)", burst, "x", rec.RS_BURST_RATIO_MAX,
            over=burst is not None and burst > rec.RS_BURST_RATIO_MAX,
        ),
        _extra_row(
            "Steal 편향 p95", steal, "%", rec.RS_CPU_STEAL_BIAS_PCT,
            over=steal is not None and steal >= rec.RS_CPU_STEAL_BIAS_PCT,
        ),
        *_confidence_rows(stats),
    ]
    return [
        PeriodExtraGroup("부하 신호", load_rows),
        PeriodExtraGroup("통계 신뢰도", confidence_rows),
    ]


def _mem_extra_groups(stats: recommendation.ResourceStats) -> list[PeriodExtraGroup]:
    """메모리 상세 탭 "신뢰도" 카드 — CPU와 동일 개념(U/S 헤드라인 보완 원신호, 성격별 2그룹, #F9 완전 노출).

    "부하 신호" = near-peak 사용률(버킷별 max 의 p95, 비탄력 피크 사이징 기준 — assess_memory 사이징에 이미
    쓰이나 지금까지 화면에 미노출이던 값, 임계 없는 정보성). "통계 신뢰도" = CPU와 동일 host-level 입력
    (_confidence_rows 공용) — steal/burst 같은 메모리 전용 편향 원자료는 ResourceStats 에 없어 그대로 생략.
    """
    load_rows = [_extra_row("Near-peak 사용률", stats.mem_near_peak_pct, "%")]
    return [
        PeriodExtraGroup("부하 신호", load_rows),
        PeriodExtraGroup("통계 신뢰도", _confidence_rows(stats)),
    ]


def _storage_extra_groups(stats: recommendation.ResourceStats) -> list[PeriodExtraGroup]:
    """스토리지 상세 탭 "신뢰도" 카드 — CPU/메모리와 동일 개념. 스토리지는 용량(disk_capacity)+I/O(disk_io)
    두 축 통합이라 "부하 신호"에 양쪽 원신호를 함께 담는다(#F9 완전 노출).

    "부하 신호" = 용량 소진 잔여일수(bytes·inode, RS_DISK_RUNWAY_DAYS 미만이면 임박)·inode 사용률(정적 가드
    RS_DISK_STATIC_GUARD_PCT, byte 사용률과 대칭)·IOPS 활동량(baseline, 임계 없는 정보성 — 유휴 device 구분용)·
    확장 목표 용량(1년 수명, 사이징 참고). "통계 신뢰도" = CPU/메모리와 동일 host-level 입력(_confidence_rows).
    disk_io 의 virtio 편향(biased=True, ADR 0052)은 상시 True 라 표시 노이즈 -> host 신뢰도 노트와 동일하게 생략.
    """
    rec = recommendation

    def _runway_row(label: str, val: float | None) -> PeriodSignalRow:
        """runway=None 은 두 원인이 섞여 있어 "N/A" 단독 표기가 애매하다는 지적 반영 — 구분 표기.

        (1) 관측 span 부족(mount_calc 의 rate_min_span 미달) -> 아직 추세를 못 낸 것(진짜 미상).
        (2) span 은 충분한데 free 가 늘거나 그대로(줄지 않음) -> 추세상 안 채워짐(무한대, 안정).
        두 span 을 별도 SQL로 안 쪼개고 host-level history_hours(같은 agent, 거의 동일 수집 시작점이라
        근사 유효)로 구분 — 정확한 마운트별 span 이 필요해지면 report_aggregate 에 별도 컬럼 추가 검토.

        threshold("최소 30일") 는 RS_DISK_RUNWAY_DAYS(값이 이 밑이면 위험) 표기라 val 이 실수일 때만 의미
        있음 — N/A/안정 상태 문자열엔 그 대신 실제 관측 문턱(RS_CONFIDENCE_MIN_HOURS, "관측 시간" 신뢰도
        행과 동일 단일 진실)을 시간 단위로 명시 — 숫자가 우연히 같은 RS_DISK_RUNWAY_DAYS(일)와 혼동 방지.
        """
        stable = stats.history_hours is not None and stats.history_hours >= rec.RS_CONFIDENCE_MIN_HOURS
        if val is not None:
            value = f"{val:.0f}일"
            threshold = f"최소 {rec.RS_DISK_RUNWAY_DAYS:g}일"
        elif stable:
            value, threshold = "안정 (추세 없음)", ""
        else:
            value, threshold = "N/A (관측 부족)", f"최소 {rec.RS_CONFIDENCE_MIN_HOURS:g}h 관측"
        return PeriodSignalRow(
            label=label, value=value, threshold=threshold,
            over=val is not None and val < rec.RS_DISK_RUNWAY_DAYS,
            measured=val is not None or stable,
        )

    inode_used = stats.disk_inode_used_pct
    load_rows = [
        _runway_row("용량 소진 잔여일수", stats.disk_capacity_runway_days),
        _runway_row("inode 소진 잔여일수", stats.disk_inode_runway_days),
        _extra_row(
            "inode 사용률", inode_used, "%", rec.RS_DISK_STATIC_GUARD_PCT,
            over=inode_used is not None and inode_used >= rec.RS_DISK_STATIC_GUARD_PCT,
        ),
        _extra_row("IOPS 활동량(baseline)", stats.disk_iops_baseline, " IOPS"),
        _extra_row("확장 목표 용량(1년 수명)", stats.disk_capacity_target_gb, "GB"),
    ]
    return [
        PeriodExtraGroup("통계 신뢰도", _confidence_rows(stats)),
        PeriodExtraGroup("부하 신호", load_rows),
    ]


def _network_extra_groups(stats: recommendation.ResourceStats) -> list[PeriodExtraGroup]:
    """네트워크 상세 탭 "신뢰도" 카드 — CPU/메모리/스토리지와 동일 개념(#F9 완전 노출).

    "부하 신호" = 트래픽 baseline(net_avg_kbytes_per_s, 임계 없는 정보성) — assess_network 의 저트래픽 게이트
    (RS_NET_MIN_TRAFFIC_KBPS 미만이면 재전송·드롭 비율을 혼잡 판정에서 억제)가 왜 발동했는지 근거로 유용.
    "통계 신뢰도" = CPU/메모리/스토리지와 동일 host-level 입력(_confidence_rows).
    """
    load_rows = [_extra_row("트래픽 baseline", stats.net_avg_kbytes_per_s, " kB/s")]
    return [
        PeriodExtraGroup("부하 신호", load_rows),
        PeriodExtraGroup("통계 신뢰도", _confidence_rows(stats)),
    ]


def build_period_assessment(
    stats: recommendation.ResourceStats,
    errors: list[ErrorSignal] | None = None,
    *,
    disk_worst_mount: str | None = None,
    window_days: int | None = None,
) -> PeriodAssessment:
    """서버 세부 '최근 N일' 카드 — 자원별 이용률(p95) + 포화(os-aware) 2축 + 에러축(E) (P2 precompute).

    이용률 = p95 사용률 vs 사이징 목표 임계(RS_*, 14일 p95 도메인 — 실시간 순간과 분리). 포화 = saturation_axis_
    displays(CPU/메모리/디스크 3축, 단일 진실) + 네트워크는 stats retrans/drop/conntrack. 판정(over)은 os-aware
    helper 경유(임계 재계산 0). 에러축(E)은 전 자원 통합(USE 완결). 실시간 카드(순간)와 별 창 — 분류·판정 근거.

    disk_worst_mount — 스토리지 "사용률" 행이 worst-mount 산식(마운트 中 최댓값)임을 값에 병기하는 마운트 이름.
    ResourceStats 에는 안 둠(도메인 모델에 표시 전용 str 필드 추가 회피) — 호출부(get_period_assessment)가
    ReportRowRaw.disk_capacity_worst_mount 를 직접 전달. 실시간 카드 도넛(disk_usage_pct, 전체 마운트 가중평균)
    과 다른 산식이라는 걸 화면에서 바로 알 수 있게 함(#F9 "의도된 차이" 명시 요청 반영).
    """
    rec = recommendation
    axes = saturation_axis_displays(stats)  # [cpu, mem, disk]
    sat_labels = ["실행 큐", "페이징", "응답 지연"]  # 실시간 카드 라벨과 통일

    def _u(label: str, val: float | None, thr: float) -> PeriodSignalRow:
        return PeriodSignalRow(
            label=label, value=_pct_str(val), threshold=f"임계 {thr:g}%",
            over=val is not None and val >= thr, measured=val is not None,
        )

    # 포화 축 over = 단일 게이트(d.crossed: 신호가 자기 임계 넘음) — 값·임계와 self-consistent. 종합 dual-gate
    # 판정(이용률 AND 포화)은 분류 배지(classify_host)가 전담 — 축은 신호 자체 비정상 여부만.
    # d.threshold 는 saturation_axis_displays 원문(">= 1"·"> 20ms"·"발생 시") — 다른 화면(단일 보고서 포화 축
    # 표·참고자료 임계값)과 공유하는 표시 단일 진실이라 그 원본은 불변. 이 카드만 이용률 컬럼과 같은 "임계 X" 어투
    # 통일 — 부등호(>=·>)는 "임계"라는 말 자체가 이상(以上) 의미를 담아 중복이라 제거하고 숫자만 접두.
    def _s(label: str, d: object) -> PeriodSignalRow:
        raw = d.threshold
        if raw.startswith("발생"):
            thr = raw
        else:
            thr = "임계 " + raw.removeprefix(">= ").removeprefix("> ")
        return PeriodSignalRow(label=label, value=d.value, threshold=thr, over=d.crossed, measured=d.measured)

    def _net(label: str, val: float | None, thr: float, unit: str) -> PeriodSignalRow:
        return PeriodSignalRow(
            label=label,
            value=(f"{val:.2f}{unit}" if val is not None else "N/A"),
            threshold=f"임계 {thr:g}{unit}",
            over=val is not None and val >= thr, measured=val is not None,
        )

    cpu_u = [_u("P95 사용률", stats.cpu_p95_pct, rec.RS_CPU_UNDER_PCT)]
    mem_u = [_u("P95 사용률", stats.mem_p95_pct, rec.RS_MEM_UNDER_PCT)]
    # 스토리지 "사용률"만 다른 자원(P95 host 집계)과 달리 worst-mount 산식(가장 채워진 마운트 1개) — 실시간
    # 도넛(전체 마운트 가중평균)과 값이 다를 수 있어 라벨·값에 worst-mount 임을 명시(#F9 의도된 차이 표기).
    disk_util_val = stats.disk_used_pct
    disk_util_value = _pct_str(disk_util_val)
    if disk_util_val is not None and disk_worst_mount:
        disk_util_value = f"{disk_util_value} ({disk_worst_mount})"
    disk_u = [
        PeriodSignalRow(
            label="사용률 (worst mount)", value=disk_util_value, threshold=f"임계 {rec.RS_DISK_STATIC_GUARD_PCT:g}%",
            over=disk_util_val is not None and disk_util_val >= rec.RS_DISK_STATIC_GUARD_PCT,
            measured=disk_util_val is not None,
        )
    ]
    cpu_s = [_s(sat_labels[0], axes[0])]
    mem_s = [_s(sat_labels[1], axes[1])]
    disk_s = [_s(sat_labels[2], axes[2])]
    net_s = [
        _net("재전송", stats.net_retrans_pct, rec.RS_NET_RETRANS_PCT, "%"),
        _net("드롭", stats.net_drop_pct, rec.RS_NET_DROP_PCT, "%"),
        PeriodSignalRow(
            label="conntrack",
            value=(f"{stats.conntrack_ratio:.2f}" if stats.conntrack_ratio is not None else "N/A"),
            threshold=f"임계 {rec.RS_CONNTRACK_SATURATION_RATIO:g}",
            over=stats.conntrack_ratio is not None and stats.conntrack_ratio >= rec.RS_CONNTRACK_SATURATION_RATIO,
            measured=stats.conntrack_ratio is not None,
        ),
    ]

    def _over(rows: list[PeriodSignalRow]) -> int:
        return sum(1 for r in rows if r.over)

    # 종합·자원별 판정 — rollup_host 1회(목록 자원 적정성과 동일 단일 진실). 배지=host_status 종합, 소제목 옆
    # verdict=자원별 status(어느 자원발인지). 문제 자원만 색, 정상·유휴·미측정은 muted.
    host = rec.rollup_host(stats)
    seg_key = _DONUT_SEGMENT_FROM_REC.get(rec.host_status_to_recommendation(host.host_status), "insufficient_data")
    cls_label = rec.LABEL_KO.get(seg_key, seg_key)
    cls_color = next((c for k, _, c, _ in _DONUT_SEGMENT_DEFS if k == seg_key), "#64748b")

    def _rstat(kind: str) -> str:
        return host.resources[kind].status if kind in host.resources else "unmeasured"

    # 스토리지 = 용량(disk_capacity) + 성능/IO(disk_io) 독립 2축 — 배지 1개로 합치면(우선순위 승자만 노출)
    # 승자 아닌 축 상태가 안 보임("I/O 병목"만 뜨면 용량이 괜찮은지 판단 불가, 사용자 지적 반영). 카드 제목
    # 옆 배지를 용량/성능 2개로 분리 노출(verdict_label=용량, verdict_label2=성능) — PeriodResource 참고.
    dc, di = _rstat("disk_capacity"), _rstat("disk_io")

    error_rows = _period_error_rows(errors or [])
    # 메모리 상세 탭 전용 에러 열 — mem_ 접두 키(OOM Kill·메모리 손상 EDAC)만, 사이징 무관 나머지(MCE·NIC·디스크)
    # 는 제외(자원별 카드에 자기 자원 무관 에러 노출 금지).
    mem_error_rows = [r for r in error_rows if r.key.startswith("mem_")]

    return PeriodAssessment(
        # 실제 집계창 주입(호출자) — 서버 상세=WINDOW_DAYS(14), 단일 보고서=선택 range(period_days). 미주입 시
        # WINDOW_DAYS 폴백. 하드코딩 시 보고서 스냅샷 window_days 가 실제 창과 decouple(P2 지속경로 정합).
        window_days=window_days if window_days is not None else rec.WINDOW_DAYS,
        error_rows=error_rows,
        classification_label=cls_label,
        classification_color=cls_color,
        resources=[
            PeriodResource(
                "CPU", cpu_u, _over(cpu_u), cpu_s, _over(cpu_s), True, "cpu", *_verdict(_rstat("cpu")),
                extra_groups=_cpu_extra_groups(stats),
            ),
            PeriodResource("메모리", mem_u, _over(mem_u), mem_s, _over(mem_s), True, "memory",
                           *_verdict(_rstat("memory")), extra_groups=_mem_extra_groups(stats),
                           error_rows=mem_error_rows),
            PeriodResource("스토리지", disk_u, _over(disk_u), disk_s, _over(disk_s), True, "storage",
                           *_verdict(dc), extra_groups=_storage_extra_groups(stats),
                           verdict_label2=_verdict(di)[0], verdict_color2=_verdict(di)[1]),
            PeriodResource("네트워크", [], 0, net_s, _over(net_s), False, "network", *_verdict(_rstat("network")),
                           extra_groups=_network_extra_groups(stats)),
        ],
    )


# ─── ReportRowRaw -> ReportRowItem (P2 단일 변환) ───


def build_resource_stats(raw: ReportRowRaw) -> recommendation.ResourceStats:
    """ReportRowRaw -> USE Method ResourceStats — report·attention mapper 공용(단일 진실).

    net baseline = server_net_io rx+tx 윈도우 평균(kB/s). 둘 다 None 이면 None(유휴 skip),
    하나만 있으면 다른쪽 0. os_family 전달로 포화 축 OS 분기(P2). report·attention·서버목록·환경이
    동일 stats 로 rollup_host 를 타 화면 간 분류 정합(임계 재계산 0).
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
        # CPU 포화는 실행 큐로 판정한다 — Linux procs_running_p95, Windows cpu_run_queue_p95.
        cpu_load_15m_max=None,
        cpu_cores=raw.cpu_cores,
        mem_p95_pct=raw.mem_p95_pct,
        mem_near_peak_pct=raw.mem_near_peak_pct,
        # 필드명은 점유량이지만 싣는 값은 페이징 신호다 — 메모리 포화를 swap 점유가 아니라 refault 지속으로 본다.
        swap_used=raw.mem_swap_paging,
        disk_used_pct=raw.worst_mount_used_pct,
        iowait_p95_pct=raw.iowait_p95_pct,
        net_avg_kbytes_per_s=net_avg,
        os_family=raw.os_family,
        sample_sufficiency=min(suffs) if suffs else None,
        # Windows CPU saturation — Processor Queue Length p95 / Memory 는 Pages Input/sec rate p95 (os-aware 소비).
        cpu_run_queue_p95=raw.cpu_run_queue_p95,
        mem_pages_input_rate_p95=raw.mem_pages_input_rate_p95,
        # ─── rollup_host 입력 — report_aggregate 산출 raw 를 도메인 축으로 배선 ───
        # 가장 바쁜 코어 p95 — 단일스레드 병목 판정(RS_CPU_PERCORE_HOLD). Windows·구 agent 는 None(graceful skip).
        cpu_percore_p95_max=raw.cpu_percore_p95_max,
        procs_blocked_p95=raw.procs_blocked_p95,
        # Linux CPU 포화 신호 + OOM 메모리 증거 — cpu_saturated·assess_memory os-aware 소비.
        procs_running_p95=raw.procs_running_p95,
        oom_occurred=raw.oom_occurred,
        mem_swap_paging=raw.mem_swap_paging,
        mem_total_mb=(raw.mem_total_bytes // 1024**2 if raw.mem_total_bytes is not None else None),
        disk_await_p95_ms=raw.disk_await_p95_ms,
        disk_iops_baseline=raw.disk_iops_baseline,  # 유휴 판정 활동 축 (디스크 I/O 활동량)
        disk_capacity_runway_days=raw.disk_capacity_runway_days,
        disk_inode_runway_days=raw.disk_inode_runway_days,
        disk_inode_used_pct=raw.disk_inode_used_pct,
        disk_capacity_target_gb=raw.disk_capacity_target_gb,
        net_retrans_pct=raw.net_retrans_pct,
        net_drop_pct=raw.net_drop_pct,
        conntrack_ratio=raw.conntrack_ratio,
        history_hours=raw.history_hours,
        cpu_burst_ratio=raw.cpu_burst_ratio,
        # 이용률 상승 추세 — 임계 이진화는 도메인 단일(regr_slope %/day raw -> bool). 다운사이즈 정상성 게이트.
        # span 가드 — 이력이 추세 신뢰 바닥(RS_CONFIDENCE_MIN_HOURS) 미만이면 slope 가 boot-ramp/지터에 지배돼
        # 오탐(상승추세)이므로 추세 미판정(None). 짧은 이력은 어차피 low_precision 으로 다운사이즈 이미 보류.
        util_trend_rising=(
            recommendation.util_trend_rising_from_slopes(raw.cpu_trend_slope, raw.mem_trend_slope)
            if raw.history_hours is not None and raw.history_hours >= recommendation.RS_CONFIDENCE_MIN_HOURS
            else None
        ),
        cpu_steal_p95_pct=raw.cpu_steal_p95_pct,
    )


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
    stats = build_resource_stats(raw)  # net baseline·OS 분기 포함 — report·attention 공용 단일 진실
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
        has_operational_event=has_operational_event,
        internal_ip=next(
            (
                a.get("address")
                for i in raw.net_interfaces or []
                if not is_virtual_interface(i.get("kind"))  # physical + bond_master (topology·상세와 동일 술어)
                for a in i.get("addresses") or []
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
        badge_class=recommendation.BADGE_CLASS[rec],
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
