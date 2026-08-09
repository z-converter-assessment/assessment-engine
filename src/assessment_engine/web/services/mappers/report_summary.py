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
    return f"{', '.join(labels[:3])}{' 외' if len(labels) > 3 else ''}"


def build_report_summary_bullets(
    rows: list[ReportRowItem],
    raws: list[ReportRowRaw] | None = None,
    view: ReportView = "customer",
    today: date | None = None,
) -> list[str]:
    if not rows:
        return ["대상 서버 없음."]

    bullets: list[str] = []

    if raws:
        disk_sat_raws = [
            r
            for r in raws
            if right_sizing.is_disk_io_saturated(build_resource_stats(r, disk_baseline=r.disk_iops_baseline))
        ]
        if disk_sat_raws:
            phrase = _top_phrase([r.hostname for r in disk_sat_raws])
            bullets.append(f"디스크 I/O 포화 {len(disk_sat_raws)}대 ({phrase}) — 디스크 병목.")

    if raws:
        mount_hosts: list[str] = []
        for r in raws:
            capacity = right_sizing.assess_disk_capacity(build_resource_stats(r, disk_baseline=r.disk_iops_baseline))
            if capacity.status != "filling":
                continue
            candidate: tuple[float, str | None, str] | None = None
            if r.disk_capacity_runway_days is not None:
                candidate = (r.disk_capacity_runway_days, r.disk_capacity_driving_mount, "용량")
            if r.disk_inode_runway_days is not None and (candidate is None or r.disk_inode_runway_days < candidate[0]):
                candidate = (r.disk_inode_runway_days, r.disk_inode_driving_mount, "inode")
            if candidate is None:
                continue
            runway, mount, axis = candidate
            mount_hosts.append(f"{r.hostname}({mount or '?'} {axis} {int(runway)}일)")
        if mount_hosts:
            bullets.append(f"디스크 채움 임박 {len(mount_hosts)}대 ({_top_phrase(mount_hosts)}).")

    reboot_hosts = [f"{r.hostname}({r.reboot_count}회)" for r in rows if r.reboot_count >= _REBOOT_UNSTABLE_COUNT]
    if reboot_hosts:
        bullets.append(f"재부팅 빈번 {len(reboot_hosts)}대 ({_top_phrase(reboot_hosts)}).")

    if view == "engineer":
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

        if raws:
            sat_hosts = [
                r.hostname
                for r in raws
                if right_sizing.is_cpu_saturated(build_resource_stats(r, disk_baseline=r.disk_iops_baseline))
            ]
            if sat_hosts:
                bullets.append(
                    f"CPU 포화 {len(sat_hosts)}대 ({_top_phrase(sat_hosts)}) — run queue/load 가 코어 처리 한계 초과."
                )

        var_hosts = [
            r.hostname
            for r in rows
            if r.cpu_variance_ratio is not None
            and r.cpu_variance_ratio >= _VARIANCE_BURST_RATIO
            and r.cpu_peak_pct is not None
            and r.cpu_peak_pct > right_sizing.CPU_BURST_PEAK_FLOOR_PCT
        ]
        if var_hosts:
            bullets.append(
                f"CPU 부하 변동 큼 {len(var_hosts)}대 ({_top_phrase(var_hosts)}) — 일시 spike 빈번 (부하 변동성 큼)."
            )

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
