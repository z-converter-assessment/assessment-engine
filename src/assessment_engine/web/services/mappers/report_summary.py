"""보고서 자동 분석 요약 불릿 — 정량 신호에서 정성 문장으로 (P2).

여러 호스트를 훑어 "무엇이 문제인가"를 문장 몇 개로 줄인다. 소비자는 발행된 보고서 한 곳이고, 양식
(customer/engineer)에 따라 신호 선택이 갈린다.

임계 판정은 전부 `right_sizing` helper 를 경유한다 — 문장을 만들자고 여기서 임계를 다시 해석하면 같은
호스트가 표와 요약에서 다르게 읽힌다.
"""

from collections import defaultdict
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from assessment_engine.domain import right_sizing
from assessment_engine.web.services.mappers.constants import _REBOOT_UNSTABLE_COUNT, _VARIANCE_BURST_RATIO, ReportView
from assessment_engine.web.services.mappers.os_eol import resolve_os_eol
from assessment_engine.web.services.mappers.resource_stats import build_resource_stats

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import ReportRowRaw
    from assessment_engine.web.view_models.report import ReportRowItem


def _top_phrase(labels: list[str]) -> str:
    """상위 3개까지 나열하고 초과분은 ' 외' 로 접는다 (6개 신호 공통)."""
    return f"{', '.join(labels[:3])}{' 외' if len(labels) > 3 else ''}"


def build_report_summary_bullets(
    rows: list[ReportRowItem],
    raws: list[ReportRowRaw] | None = None,
    view: ReportView = "customer",
    today: date | None = None,
) -> list[str]:
    """자동 분석 요약 문장 생성 — 정량 신호 기반 정성 요약 (P2).

    customer(양식 A)는 고객 의사결정에 직결되는 시그널만, engineer(양식 B)는 거기에 엔지니어 분석 시그널
    (역할별 평균 CPU·Saturation·CPU 변동성)을 더한다. `raws` 는 ReportRowRaw 축이 필요한 신호(포화·OS EOL)의
    게이트라 없으면 그 신호를 건너뛴다. 빈 리스트면 ["대상 서버 없음"].
    """
    if not rows:
        return ["대상 서버 없음."]

    bullets: list[str] = []
    # 자원 부족 / 효율화 권장 줄은 KPI grid 에 이미 카운트로 있어 여기서 뺀다 (사용자 의도).

    # 디스크 I/O 포화 — OS별 정규화(Linux iowait / Windows disk_queue) 판정. 고객 의사결정 직결 신호.
    if raws:
        disk_sat_raws = [
            r
            for r in raws
            if right_sizing.disk_io_saturated(build_resource_stats(r, disk_baseline=r.disk_iops_baseline))
        ]
        if disk_sat_raws:
            phrase = _top_phrase([r.hostname for r in disk_sat_raws])
            bullets.append(f"디스크 I/O 포화 {len(disk_sat_raws)}대 ({phrase}) — 디스크 병목.")

    # 디스크 용량 임박 — 분류(assess_disk_capacity filling)와 동일 신호라 배지와 정합.
    if raws:
        mount_hosts = [
            f"{r.hostname}({r.disk_capacity_driving_mount or '?'} {int(r.disk_capacity_runway_days)}일)"
            for r in raws
            if right_sizing.assess_disk_capacity(build_resource_stats(r, disk_baseline=r.disk_iops_baseline)).status
            == "filling"
            and r.disk_capacity_runway_days is not None
        ]
        if mount_hosts:
            bullets.append(f"디스크 채움 임박 {len(mount_hosts)}대 ({_top_phrase(mount_hosts)}).")

    reboot_hosts = [f"{r.hostname}({r.reboot_count}회)" for r in rows if r.reboot_count >= _REBOOT_UNSTABLE_COUNT]
    if reboot_hosts:
        bullets.append(f"재부팅 빈번 {len(reboot_hosts)}대 ({_top_phrase(reboot_hosts)}).")

    if view == "engineer":
        # 역할별 평균 CPU — 자원 집약 역할 식별. 고객 보고서엔 정보 과다라 engineer 전용.
        role_cpu: defaultdict[str, list[float]] = defaultdict(list)
        for r in rows:
            if r.cpu_p95_pct is not None:
                role_cpu[r.role].append(r.cpu_p95_pct)
        if role_cpu:
            top_cpu_role = max(role_cpu, key=lambda k: sum(role_cpu[k]) / len(role_cpu[k]))
            top_cpu_avg = sum(role_cpu[top_cpu_role]) / len(role_cpu[top_cpu_role])
            if top_cpu_avg >= right_sizing.CPU_UNDER_PCT:
                bullets.append(f"{top_cpu_role} 계열 서버의 평균 CPU p95가 {top_cpu_avg:.0f}%로 높게 관찰됨.")

        # 분류 cpu_saturation trigger 와 같은 os-aware 신호 — run queue 로 under_provisioned 가 된 Windows
        # 호스트가 요약에서만 누락되는 일이 없다.
        if raws:
            sat_hosts = [
                r.hostname
                for r in raws
                if right_sizing.cpu_saturated(build_resource_stats(r, disk_baseline=r.disk_iops_baseline))
            ]
            if sat_hosts:
                bullets.append(
                    f"CPU 포화 {len(sat_hosts)}대 ({_top_phrase(sat_hosts)}) — run queue/load 가 코어 처리 한계 초과."
                )

        # peak 저부하선(BURST_PEAK_FLOOR) 게이트 — 미세값 지터로 저부하 호스트가 변동성으로 잡히는 오탐 방지.
        # _build_diagnosis 의 burst gate 와 동일 기준.
        var_hosts = [
            r.hostname
            for r in rows
            if r.cpu_variance_ratio is not None
            and r.cpu_variance_ratio >= _VARIANCE_BURST_RATIO
            and r.cpu_peak_pct is not None
            and r.cpu_peak_pct > right_sizing.BURST_PEAK_FLOOR_CPU_PCT
        ]
        if var_hosts:
            bullets.append(
                f"CPU 부하 변동 큼 {len(var_hosts)}대 ({_top_phrase(var_hosts)}) — 일시 spike 빈번 (부하 변동성 큼)."
            )

    # OS EOL — attention 카드와 동일 판정(resolve_os_eol). today 미주입 시 현재 UTC (caller 주입 권장).
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
