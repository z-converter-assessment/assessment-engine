"""보고서 생성 디스패치 — 비동기 워커·발행 경로 공유 단일 진실.

parent job 을 받아 발행 시점 정적 스냅샷 result dict 를 돌려준다. 워커는 성공이면
`mark_succeeded(result)`, 예외면 `mark_failed` 로 전이한다.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from assessment_engine.db.repositories.query.types import DIAGNOSTIC_DEFAULT_TIME_RANGE
from assessment_engine.web.services.report.result import REPORT_KIND_ENV, build_report_result
from assessment_engine.web.services.report.serializer import env_report_to_dict

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
    from assessment_engine.json_types import JsonObject
    from assessment_engine.web.services.diagnostic_service import DiagnosticService
    from assessment_engine.web.services.query import QueryService
    from assessment_engine.web.view_models.attention import AttentionSignals


class ReportGenerationError(Exception):
    """보고서 생성 불가 — 등록 서버 0 / 유효 서버 id 0 등. 워커가 mark_failed 로 전이."""


def attention_for_host(hostname: str, attention: AttentionSignals) -> dict[str, str]:
    """1대 호스트 운영 신호(gap/os_eol/restart) lookup — 발행 aux 캡처 + live preview 공용."""
    out: dict[str, str] = {}
    for row in attention.gap_warnings:
        if row.link_text == hostname:
            out["gap"] = row.badge_text
    for row in attention.os_eol_warnings:
        if row.link_text == hostname:
            out["os_eol"] = row.meta_text
    for row in attention.agent_unstable:
        if row.link_text == hostname:
            out["restart"] = row.badge_text
    return out


def attention_by_host(hostnames: set[str], attention: AttentionSignals) -> dict[str, dict[str, str]]:
    """선택 N대 운영 신호 lookup {hostname: {gap, os_eol, restart}} — 발행 aux + live preview 공용."""
    by_host: dict[str, dict[str, str]] = {h: {} for h in hostnames}
    for row in attention.gap_warnings:
        if row.link_text in by_host:
            by_host[row.link_text]["gap"] = row.badge_text
    for row in attention.os_eol_warnings:
        if row.link_text in by_host:
            by_host[row.link_text]["os_eol"] = row.meta_text
    for row in attention.agent_unstable:
        if row.link_text in by_host:
            by_host[row.link_text]["restart"] = row.badge_text
    return by_host


async def build_report_result_for_job(
    query_service: QueryService,
    diag_service: DiagnosticService,
    record: DiagnosticJobRecord,
) -> JsonObject:
    """parent job -> 발행 시점 정적 스냅샷 result dict. child(N대)는 내부 emit. 생성 불가 시 ReportGenerationError."""
    p = record.input_params
    view = p["view"]
    time_range = p.get("time_range", DIAGNOSTIC_DEFAULT_TIME_RANGE)
    # anchor 는 emit 시점에 확정·저장된 값 — 재정규화하면 워커 지연만큼 발행 윈도우가 밀린다.
    anchor = datetime.fromisoformat(p["anchor_at"])

    if record.scope == "environment":
        summary = await query_service.get_environment_report(time_range, anchor, view=view)
        if summary.overview.total == 0:
            raise ReportGenerationError("no registered servers")
        return build_report_result(kind=REPORT_KIND_ENV, snapshot=env_report_to_dict(summary), view=view, aux=None)

    ids = p["server_public_ids"]
    sid_map = await query_service.resolve_server_ids(ids)
    valid = [pid for pid in ids if pid in sid_map]
    if not valid:
        raise ReportGenerationError("no valid server ids")
    attention = await query_service.get_attention_signals(end=anchor, limit_each=None)

    if len(valid) == 1:
        summary = await query_service.get_single_server_report(
            valid[0], view=view, time_range=time_range, anchor_at=anchor, attention=attention
        )
        if summary is None:
            raise ReportGenerationError("server not found")
        hostname = summary.base.rows[0].hostname if summary.base.rows else valid[0]
        aux = {"attention_for_host": attention_for_host(hostname, attention)}
        return build_report_result(kind=REPORT_KIND_ENV, snapshot=env_report_to_dict(summary), view=view, aux=aux)

    # child 생성 중 예외는 그대로 전파한다 — 워커가 parent 를 failed 로 전이해 부분 성공 parent 를 막는다.
    # 생성이 전부 끝난 뒤 emit 하므로 중간 실패 시 orphan child 가 남지 않는다.
    child_jobs: dict[str, str] = {}
    children = await query_service.build_child_prefetched_reports(valid, sid_map, view, time_range, anchor, attention)
    for pid, child in children:
        if child is None:
            continue
        hostname = child.base.rows[0].hostname if child.base.rows else pid
        cid = await diag_service.emit_report(
            view=view,
            scope="server",
            kind=REPORT_KIND_ENV,
            snapshot=env_report_to_dict(child),
            server_public_ids=[pid],
            time_range=time_range,
            anchor_at=anchor,
            aux={"attention_for_host": attention_for_host(hostname, attention)},
        )
        if cid:
            child_jobs[pid] = cid

    selection = await query_service.get_selection_report(valid, view=view, time_range=time_range, anchor_at=anchor)
    if selection is None:
        raise ReportGenerationError("no valid server ids")
    aux = {"attention_by_host": attention_by_host({r.hostname for r in selection.base.rows}, attention)}
    result = build_report_result(kind=REPORT_KIND_ENV, snapshot=env_report_to_dict(selection), view=view, aux=aux)
    result["child_jobs"] = child_jobs
    return result
