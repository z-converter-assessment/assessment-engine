"""운영 신호(attention) 조회 mixin — gap·os_eol·agent_unstable 3 카탈로그 + 선택 N대 필터."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from assessment_engine.domain import right_sizing
from assessment_engine.web.services.mappers.attention import (
    to_agent_unstable_item,
    to_gap_warning_item,
    to_os_eol_warning_item,
)
from assessment_engine.web.services.query._base import _BaseQueryServiceMixin, _filter_attention
from assessment_engine.web.settings import get_web_settings
from assessment_engine.web.view_models.attention import AttentionRow, AttentionSignals

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import MetricGapWarningRaw, ReportRowRaw
    from assessment_engine.db.repositories.query import QueryRepository

_ATTENTION_LIMIT_EACH = 5
_GAP_MINUTES = 5
_GAP_RECENT_HOURS = 24


def _assemble_attention(
    raws_period: list[ReportRowRaw],
    gap_raws: list[MetricGapWarningRaw],
    restart_counts: dict[int, int],
    now: datetime,
    limit_each: int | None,
) -> AttentionSignals:
    os_eol_warnings: list[AttentionRow] = []
    for raw in raws_period:
        eol = to_os_eol_warning_item(raw, now)
        if eol and (limit_each is None or len(os_eol_warnings) < limit_each):
            os_eol_warnings.append(eol)
    agent_unstable: list[AttentionRow] = []
    raws_by_id = {r.server_id: r for r in raws_period}
    threshold_n = get_web_settings().agent_restart_alert_threshold
    for sid, count in restart_counts.items():
        if count >= threshold_n:
            raw = raws_by_id.get(sid)
            if raw and (limit_each is None or len(agent_unstable) < limit_each):
                agent_unstable.append(to_agent_unstable_item(raw.public_id, raw.hostname, count))
    return AttentionSignals(
        gap_warnings=[to_gap_warning_item(r, now) for r in gap_raws],
        os_eol_warnings=os_eol_warnings,
        agent_unstable=agent_unstable,
    )


async def attention_signals(
    repo: QueryRepository,
    disk_threshold_pct: float = 85,
    gap_minutes: int = _GAP_MINUTES,
    gap_recent_hours: int = _GAP_RECENT_HOURS,
    limit_each: int | None = _ATTENTION_LIMIT_EACH,
    days_until_full_threshold: int = 30,
    end: datetime | None = None,
    raws: list[ReportRowRaw] | None = None,
) -> AttentionSignals:

    ref = end if end is not None else datetime.now(UTC)

    gap_raws = await repo.get_metric_gap_warnings(gap_minutes, gap_recent_hours, limit_each)
    if raws is None:
        server_ids = await repo.list_server_ids()
        raws = (
            await repo.get_report_aggregate(server_ids, period_days=right_sizing.WINDOW_DAYS, end=ref)
            if server_ids
            else []
        )
    else:
        server_ids = [r.server_id for r in raws]
    restart_counts: dict[int, int] = {}
    if server_ids:
        restart_counts = await repo.get_agent_restart_counts_recent(server_ids, ref - timedelta(hours=1))
    return _assemble_attention(raws, gap_raws, restart_counts, ref, limit_each)


class AttentionQueryMixin(_BaseQueryServiceMixin):
    async def get_attention_signals(
        self,
        disk_threshold_pct: float = 85,
        gap_minutes: int = _GAP_MINUTES,
        gap_recent_hours: int = _GAP_RECENT_HOURS,
        limit_each: int | None = _ATTENTION_LIMIT_EACH,
        days_until_full_threshold: int = 30,
        end: datetime | None = None,
        raws: list[ReportRowRaw] | None = None,
    ) -> AttentionSignals:
        return await attention_signals(
            self.repo,
            disk_threshold_pct,
            gap_minutes,
            gap_recent_hours,
            limit_each,
            days_until_full_threshold,
            end,
            raws,
        )

    async def get_selection_attention(self, server_ids: list[int], end: datetime) -> AttentionSignals:
        details = await self.repo.get_servers(server_ids)
        hostnames = {d.hostname for d in details}
        return _filter_attention(await self.get_attention_signals(end=end, limit_each=None), hostnames)
