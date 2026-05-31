"""보고서 row mapper — ReportRowRaw → ReportRowItem + 보조 집계·요약 (P2).

서버 보고서 본문 행·KPI 합계·정성 요약을 본 모듈에서 합성한다. 양식 분리:
- view='customer' (양식 A): 고객 KPI · risk_level 3단계 압축 · 즉시 액션 시그널.
- view='engineer' (양식 B): USE Method 분류 6단 · saturation · variance · 16컬럼 정량.
"""

from collections import Counter, defaultdict
from datetime import UTC, date, datetime

from assessment_engine import recommendation
from assessment_engine.db.dtos.outbound import ReportRowRaw
from assessment_engine.web.services.device_filters import is_physical_disk
from assessment_engine.web.services.mappers.server import _os_display, infer_role
from assessment_engine.web.services.mappers.shared import (
    _CAPACITY_IMMINENT_DAYS,
    ReportView,
    resolve_os_eol,
)
from assessment_engine.web.services.unit_converter import bytes_to_gb, kb_to_gb
from assessment_engine.web.view_models.report import ReportRowItem, ReportTotals

# ─── 위험도 매핑 — 양식 A KPI 3단계 압축 ────────────────────────────────
# USE Method 분류 -> (risk_level, 한글 라벨, badge CSS 클래스).
# under_provisioned → 고위험 (자원 부족, 즉시 조치)
# shutdown · idle · over_provisioned → 주의 (저사용·과다 — 운영자 점검)
# optimal · insufficient_data → 정상 (또는 데이터 부족)
_RISK_FROM_RECOMMENDATION: dict[str, tuple[str, str, str]] = {
    "under_provisioned": ("high", "고위험", "rec-under_provisioned"),
    "shutdown": ("attention", "주의 필요", "rec-over_provisioned"),
    "idle": ("attention", "주의 필요", "rec-over_provisioned"),
    "over_provisioned": ("attention", "주의 필요", "rec-over_provisioned"),
    "optimal": ("normal", "정상", "rec-optimal"),
    "insufficient_data": ("normal", "정상", "rec-optimal"),
}

# ─── 보고서 row 표시 신호 색 — saturation/variance/worst_mount/reboot 단일 카탈로그 (#E8) ───
# UI badge slate 톤 (디자인 도메인 별도 — UtilizationBar 의 vivid 톤과 분리).
_REPORT_SIG_DANGER = "#b91c1c"  # saturation · worst_mount 임박 · reboot 잦음
_REPORT_SIG_WARN = "#92400e"  # variance burst
_REPORT_SIG_NEUTRAL = "#1e293b"  # 기본 텍스트 (variance 정상)
_REPORT_SIG_MUTED = "#94a3b8"  # 신호 없음 (gray light)
_REPORT_SIG_SUBTLE = "#64748b"  # subdued gray

# 보고서 row 임계 — recommendation 도메인 상수 활용 + 보고서 표시 전용 임계.
_SATURATION_BURST_RATIO = recommendation.CPU_SATURATION_LOAD_RATIO  # 1.0 — saturation 기준
_VARIANCE_BURST_RATIO = 1.5  # peak/p95 >= 1.5 — variance burst 표시 (보고서 전용 임계)
_REBOOT_UNSTABLE_COUNT = 3  # reboot_count >= 3 — Agent 불안정 신호 (#F10 attention 임계)


# ─── KPI 집계 ───


def compute_report_avg_p95(rows: list) -> tuple[float | None, float | None]:
    """ReportRowItem list에서 CPU·메모리 p95 평균을 계산 (양식 A KPI).

    None 항목은 제외 후 산술 평균. 모두 None이면 None 반환 (divide-by-zero 회피).
    P2 단일 변환 — service 안 inline 계산 대신 mapper helper로 추출해 unit test·재사용 정합.
    """
    cpu_vals = [r.cpu_p95_pct for r in rows if r.cpu_p95_pct is not None]
    mem_vals = [r.mem_p95_pct for r in rows if r.mem_p95_pct is not None]
    avg_cpu = sum(cpu_vals) / len(cpu_vals) if cpu_vals else None
    avg_mem = sum(mem_vals) / len(mem_vals) if mem_vals else None
    return avg_cpu, avg_mem


def compute_report_totals_from_raw(raws: list) -> ReportTotals:
    """ReportRowRaw list -> 묶음 자원 총량. cpu_cores·mem_total_kb·disks 합산 (P2).

    양식 A 상단의 마이그레이션 capacity 산정 입력 — "총 N대 = 총 X vCPU·Y GB·Z TB".
    """
    total_vcpus = sum(r.cpu_cores or 0 for r in raws)
    total_mem_kb = sum(r.mem_total_kb or 0 for r in raws)
    total_disk_bytes = 0
    for r in raws:
        for d in r.disks or []:
            total_disk_bytes += d.get("size_bytes") or 0
    return ReportTotals(
        total_vcpus=total_vcpus,
        total_memory_gb=round(total_mem_kb / 1024 / 1024, 1),  # KB -> GB, 소수 첫째 자리 (표시 왜곡 회피)
        total_disk_gb=int(total_disk_bytes / 10**9),  # bytes -> GB (벤더 표기 관례)
    )


def build_role_distribution(raws: list) -> dict[str, int]:
    """ReportRowRaw list -> 역할별 서버 수 dict. 양식 A 상단 표시용."""
    counter: Counter[str] = Counter()
    for r in raws:
        counter[infer_role(r.services)] += 1
    return dict(counter.most_common())


# ─── 정성 요약 (양식 A/B 분기) ───


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

    # I/O wait 신호 — p95 임계 초과 서버 카운트. 디스크 병목 = 고객 의사결정 직결. (#F10 recommendation 상수)
    iowait_threshold = recommendation.IOWAIT_UPSIZE_PCT
    n_iowait = sum(1 for r in rows if r.iowait_p95_pct is not None and r.iowait_p95_pct >= iowait_threshold)
    if n_iowait:
        hosts = [r.hostname for r in rows if r.iowait_p95_pct is not None and r.iowait_p95_pct >= iowait_threshold][:3]
        suffix = " 외" if n_iowait > 3 else ""
        bullets.append(
            f"I/O wait p95 {iowait_threshold}%+ {n_iowait}대 ({', '.join(hosts)}{suffix})"
            " — 디스크 병목. SSD/iops 상향 검토."
        )

    # Mount 임박 — _CAPACITY_IMMINENT_DAYS 안 채워질 마운트가 있는 서버 카운트
    n_mount = sum(
        1
        for r in rows
        if r.worst_mount_days_until_full is not None and r.worst_mount_days_until_full <= _CAPACITY_IMMINENT_DAYS
    )
    if n_mount:
        hosts = []
        for r in rows:
            if r.worst_mount_days_until_full is not None and r.worst_mount_days_until_full <= _CAPACITY_IMMINENT_DAYS:
                hosts.append(f"{r.hostname}({r.worst_mount} {r.worst_mount_days_until_full}일)")
                if len(hosts) >= 3:
                    break
        suffix = " 외" if n_mount > 3 else ""
        bullets.append(f"디스크 채움 임박 {n_mount}대 ({', '.join(hosts)}{suffix}) — 디스크 증설 또는 정리 검토.")

    # 재부팅 빈번 — period 안 _REBOOT_UNSTABLE_COUNT 이상
    n_reboot = sum(1 for r in rows if r.reboot_count >= _REBOOT_UNSTABLE_COUNT)
    if n_reboot:
        hosts = [f"{r.hostname}({r.reboot_count}회)" for r in rows if r.reboot_count >= _REBOOT_UNSTABLE_COUNT][:3]
        suffix = " 외" if n_reboot > 3 else ""
        bullets.append(f"재부팅 빈번 {n_reboot}대 ({', '.join(hosts)}{suffix}) — 안정성 점검 필요.")

    if view == "engineer":
        # 역할별 평균 CPU — 엔지니어가 자원 집약 역할 식별. 고객 보고서엔 정보 과다.
        role_cpu: defaultdict[str, list[float]] = defaultdict(list)
        for r in rows:
            if r.cpu_p95_pct is not None:
                role_cpu[r.role].append(r.cpu_p95_pct)
        if role_cpu:
            top_cpu_role = max(role_cpu, key=lambda k: sum(role_cpu[k]) / len(role_cpu[k]))
            top_cpu_avg = sum(role_cpu[top_cpu_role]) / len(role_cpu[top_cpu_role])
            if top_cpu_avg >= 70.0:
                bullets.append(f"{top_cpu_role} 계열 서버의 평균 CPU p95가 {top_cpu_avg:.0f}%로 높게 관찰됨.")

        # Saturation — load_15m_max / cpu_cores 임계 초과 (saturated). 큐잉 이론 시그널 — 엔지니어용.
        n_sat = sum(1 for r in rows if r.saturation_ratio is not None and r.saturation_ratio >= _SATURATION_BURST_RATIO)
        if n_sat:
            hosts = [
                f"{r.hostname}({r.saturation_ratio:.1f})"
                for r in rows
                if r.saturation_ratio is not None and r.saturation_ratio >= _SATURATION_BURST_RATIO
            ][:3]
            suffix = " 외" if n_sat > 3 else ""
            bullets.append(
                f"Saturation {n_sat}대 ({', '.join(hosts)}{suffix}) — load가 cpu_cores 초과. 처리 한계 신호."
            )

        # 변동성 큼 — cpu peak/p95 임계 초과. sizing 전략 시그널 — 엔지니어용.
        n_var = sum(
            1 for r in rows if r.cpu_variance_ratio is not None and r.cpu_variance_ratio >= _VARIANCE_BURST_RATIO
        )
        if n_var:
            hosts = [
                r.hostname
                for r in rows
                if r.cpu_variance_ratio is not None and r.cpu_variance_ratio >= _VARIANCE_BURST_RATIO
            ][:3]
            suffix = " 외" if n_var > 3 else ""
            bullets.append(
                f"CPU 부하 변동 큼 {n_var}대 ({', '.join(hosts)}{suffix})"
                " — 일시 spike 빈번. 평균보다 peak 기준 sizing 권장."
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
            shown = eol_hosts[:3]
            suffix = " 외" if len(eol_hosts) > 3 else ""
            bullets.append(
                f"OS EOL {len(eol_hosts)}대 ({', '.join(shown)}{suffix}) — 마이그레이션 전 OS 업그레이드 검토."
            )

    return bullets


# ─── 자동 진단·권고 helper (양식 B 컬럼) ───


# under trigger 키 -> 한국어 증설 권고. 표시 순서 고정(mem -> cpu -> disk). recommendation.assess
# 가 산출한 triggers(근거)를 mapper 가 문구로 변환만 한다(P2, 임계 재계산 중복 제거).
_TRIGGER_ACTION_KO: dict[str, str] = {
    "mem_saturation": "메모리 증설 (스왑 발생)",
    "mem_util": "메모리 증설",
    "cpu_util": "CPU 증설",
    "cpu_saturation": "CPU 증설 (load 포화)",
    "disk_capacity": "디스크 증설 (capacity)",
    "disk_io": "디스크 증설 (IO 병목)",
}
_TRIGGER_ACTION_ORDER = ("mem_saturation", "mem_util", "cpu_util", "cpu_saturation", "disk_capacity", "disk_io")


def _build_under_provisioned_reason(triggers: list[str]) -> str:
    """under_provisioned hit trigger -> 한국어 증설 권고 결합 (`/` 구분) — 양식 A "권고" 컬럼.

    근거(triggers)는 recommendation.assess 단일 산출 — mapper 는 키->문구 변환만(P2).
    mem_saturation(스왑) + mem_util 둘 다 hit 시 스왑 문구만(중복 회피). trigger 0건 fallback.
    """
    picked = [t for t in _TRIGGER_ACTION_ORDER if t in triggers]
    if "mem_saturation" in picked and "mem_util" in picked:
        picked.remove("mem_util")
    reasons = [_TRIGGER_ACTION_KO[t] for t in picked]
    return " / ".join(reasons) if reasons else "리소스 증설 검토"


def _build_recommendation_action(assessment: recommendation.Assessment) -> str:
    """recommendation 분류 -> 양식 A "권고" 컬럼 단일 문구 (environment·single_report 공유 단일 진실).

    under_provisioned 는 hit trigger 별 증설 권고 결합(`_build_under_provisioned_reason`), 그 외는 분류별 고정 조치.
    optimal/insufficient_data 는 조치 불필요 상태 표시 — environment "조치 필요 호스트"엔 미노출,
    서버 단일 보고서엔 노출.
    """
    rec = assessment.recommendation
    if rec == "under_provisioned":
        return _build_under_provisioned_reason(assessment.triggers)
    return {
        "over_provisioned": "자원 축소 검토",
        "idle": "용도 재평가 / 종료 검토",
        "shutdown": "종료 가능 검토",
        "optimal": "적정 운영",
        "insufficient_data": "평가 표본 부족",
    }.get(rec, "")


def _build_diagnosis(
    raw: ReportRowRaw, saturation: float | None, cpu_variance: float | None, mem_variance: float | None
) -> str:
    """saturation·variance·iowait·disk·swap·mem·cpu 종합 자동 진단 — 양식 B "판단" 컬럼.

    우선순위 (가장 시급한 신호 1개 선택):
    1. swap_used → "메모리 부족 (스왑 발생)" — paging 활성, 1차 강신호
    2. iowait_p95 >= 20% → "디스크 I/O 병목"
    3. saturation >= 1.0 → "CPU saturation (LOAD > cores)"
    4. mem_p95 >= 80% → "메모리 압박"
    5. cpu_p95 >= 70% → "CPU 압박"
    6. cpu_variance >= 1.5 또는 mem_variance >= 1.5 → "변동성 큼 (burst)"
    7. cpu_p95 <= 3% → "거의 미사용"
    8. cpu_p95 <= 30% and mem_p95 <= 50% → "여유 있음 (축소 검토)"
    9. 그 외 → "정상"
    """
    if recommendation.swap_saturation(raw.os_family, raw.swap_used):
        return "메모리 부족 (스왑 발생)"
    if raw.iowait_p95_pct is not None and raw.iowait_p95_pct >= 20:
        return "디스크 I/O 병목"
    if saturation is not None and saturation >= 1.0:
        return "CPU saturation"
    if raw.mem_p95_pct is not None and raw.mem_p95_pct >= recommendation.MEM_UPSIZE_P95_PCT:
        return "메모리 압박"
    if raw.cpu_p95_pct is not None and raw.cpu_p95_pct >= recommendation.CPU_UPSIZE_P95_PCT:
        return "CPU 압박"
    if (cpu_variance is not None and cpu_variance >= 1.5) or (mem_variance is not None and mem_variance >= 1.5):
        return "변동성 큼 (burst)"
    if raw.cpu_p95_pct is not None and raw.cpu_p95_pct <= recommendation.SHUTDOWN_CPU_P95_PCT:
        return "거의 미사용"
    if (
        raw.cpu_p95_pct is not None
        and raw.cpu_p95_pct <= recommendation.CPU_DOWNSIZE_P95_PCT
        and raw.mem_p95_pct is not None
        and raw.mem_p95_pct <= recommendation.MEM_DOWNSIZE_P95_PCT
    ):
        return "여유 있음 (축소 검토)"
    return "정상"


# ─── ReportRowRaw -> ReportRowItem (P2 단일 변환) ───


def build_resource_stats(raw: ReportRowRaw) -> recommendation.ResourceStats:
    """ReportRowRaw -> USE Method ResourceStats — report·attention mapper 공용(단일 진실).

    net baseline = server_net_io rx+tx 윈도우 평균(kBps). 둘 다 None 이면 None(idle/shutdown skip),
    하나만 있으면 다른쪽 0. os_family 전달로 swap 축 OS 분기(P2). attention 의 capacity trigger 도
    동일 stats 로 recommendation.assess 를 타 임계 재계산 중복을 제거(assess.triggers 단일 진실).
    """
    net_avg = (
        None if raw.net_rx_kbps is None and raw.net_tx_kbps is None else (raw.net_rx_kbps or 0) + (raw.net_tx_kbps or 0)
    )
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
    )


def to_report_row_item(raw: ReportRowRaw, is_online: bool, now: datetime) -> ReportRowItem:
    """ReportRowRaw(repo) + is_online + now -> ReportRowItem(ViewModel) — P2 단일 변환.

    `now`로 uptime_days 계산 (now - boot_time).
    표시 파생 (role / recommendation / risk_level / badge_class / os_display / internal_ip[0])은 모두 여기서.
    USE Method 분류(`recommendation`)는 양식 B(엔지니어용)·`risk_level`은 양식 A(고객용) KPI/표 노출.
    `diagnosis`는 양식 B "판단" 컬럼 자동 해석.
    """
    stats = build_resource_stats(raw)  # net baseline·OS 분기 포함 — report·attention 공용 단일 진실
    assessment = recommendation.assess(stats)  # 분류 + 근거(triggers) + 미관측 축 단일 평가
    rec = assessment.recommendation
    is_partial = assessment.is_partial  # P4 — saturation 축 미관측(예: Windows load) confidence 단서
    risk_level, risk_label, risk_badge_class = _RISK_FROM_RECOMMENDATION[rec]
    uptime_days: int | None = None
    if raw.boot_time is not None:
        delta = now - raw.boot_time
        uptime_days = max(0, int(delta.total_seconds() // 86400))

    saturation = None
    if raw.load_15m_max is not None and raw.cpu_cores and raw.cpu_cores > 0:
        saturation = raw.load_15m_max / raw.cpu_cores

    cpu_variance = None
    if raw.cpu_p95_pct and raw.cpu_peak_pct and raw.cpu_p95_pct > 0:
        cpu_variance = raw.cpu_peak_pct / raw.cpu_p95_pct
    mem_variance = None
    if raw.mem_p95_pct and raw.mem_peak_pct and raw.mem_p95_pct > 0:
        mem_variance = raw.mem_peak_pct / raw.mem_p95_pct
    # 인벤토리 합계 산정 (환경 엔지니어 호스트 상세 노출용)
    disk_total_gb_val: float | None = None
    if raw.disks:
        physical = [d for d in raw.disks if is_physical_disk(d.get("name", ""))]
        if physical:
            disk_total_gb_val = round(sum(bytes_to_gb(d.get("size_bytes")) or 0.0 for d in physical), 1)
    return ReportRowItem(
        server_id=raw.server_id,
        public_id=raw.public_id,
        hostname=raw.hostname,
        role=infer_role(raw.services),
        is_online=is_online,
        os_family=raw.os_family,
        is_partial=is_partial,
        os_display=_os_display(raw.os_id, raw.os_version),
        kernel_version=raw.kernel_version,
        internal_ip=raw.ip_internal[0] if raw.ip_internal else None,
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
        saturation_ratio=saturation,
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
        diagnosis=_build_diagnosis(raw, saturation, cpu_variance, mem_variance),
        recommendation_action=_build_recommendation_action(assessment),
        # P3 임계 분류 색 — 단일 의미 체계 (#E1 P3, #E8 임계 색 단일 진실).
        # 정상(임계 미만) = NEUTRAL(기본 진한 글씨) / 주의(변동성 burst) = WARN(amber) /
        # 위험(포화·capacity·잦은 재부팅) = DANGER(red). 임계 넘을 때만 색 변경, 정상값은 기본색 통일.
        saturation_color=(
            _REPORT_SIG_DANGER
            if (saturation is not None and saturation >= _SATURATION_BURST_RATIO)
            else _REPORT_SIG_NEUTRAL
        ),
        cpu_variance_color=(
            _REPORT_SIG_WARN
            if (cpu_variance is not None and cpu_variance >= _VARIANCE_BURST_RATIO)
            else _REPORT_SIG_NEUTRAL
        ),
        mem_variance_color=(
            _REPORT_SIG_WARN
            if (mem_variance is not None and mem_variance >= _VARIANCE_BURST_RATIO)
            else _REPORT_SIG_NEUTRAL
        ),
        worst_mount_days_color=(
            _REPORT_SIG_DANGER
            if (
                raw.worst_mount_days_until_full is not None
                and raw.worst_mount_days_until_full <= _CAPACITY_IMMINENT_DAYS
            )
            else _REPORT_SIG_NEUTRAL
        ),
        reboot_count_color=(_REPORT_SIG_DANGER if raw.reboot_count >= _REBOOT_UNSTABLE_COUNT else _REPORT_SIG_NEUTRAL),
        agent_restart_count_color=(
            _REPORT_SIG_DANGER if raw.agent_restart_count >= _REBOOT_UNSTABLE_COUNT else _REPORT_SIG_NEUTRAL
        ),
    )
