"""운영 신호(attention) 조회 mixin — gap·os_eol·agent_unstable 3 카탈로그 + 선택 N대 필터."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from assessment_engine import recommendation
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

# 운영신호(attention) 카탈로그 항목 한도 + gap 윈도우 — 단건 get_attention_signals 와 대시보드 묶음 공유.
_ATTENTION_LIMIT_EACH = 5
_GAP_MINUTES = 5
_GAP_RECENT_HOURS = 24


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
        """list 화면 운영 신호 카드 — USE Method 외 시스템 운영 이상 3 카탈로그.

        - gap_warnings: 5분+ 끊김 (24h 안 발생)
        - os_eol_warnings: OS EOL 임박/지남 (정적 매핑)
        - agent_unstable: 1h 윈도우 안 재시작 임계 초과

        디스크(capacity·IO)는 USE Method classify 통합 — 본 catalog 에서 제외 (중복 회피).
        조립은 _assemble_attention 단일 진실. 실시간 현황 페이지·보고서가 본 메서드 공유.

        raws 재사용(B2): 보고서 경로가 이미 산출한 report_aggregate raws 를 넘기면 내부 재조회를 생략한다.
        os_eol(os_id/version/kernel)·agent_unstable(public_id/hostname)만 읽어 창 독립이므로 어느 윈도우
        raws 든 정합 — 넘기면 환경 보고서의 aggregate 2회가 1회로. 미전달(None)이면 14일 창 자체 조회.
        """
        # end=보고서 anchor(없으면 현재). os_eol 임박/경과 판정이 이 시각 기준 — 보고서 다른 지표(end_dt)와 정합.
        # gap/agent_unstable 은 보고서 미표시(C1)라 anchor 영향 없음.
        ref = end if end is not None else datetime.now(UTC)
        # limit_each=None 이면 gap 도 전수(LIMIT NULL) — 운영신호 카드 3 카탈로그 모두 전수 출력.
        # 보고서는 gap 미표시(C1)라 전수여도 결과만 버려질 뿐(소량, 무해).
        gap_raws = await self.repo.metric_gap_warnings(gap_minutes, gap_recent_hours, limit_each)
        if raws is None:
            server_ids = await self.repo.list_server_ids()
            raws = (
                await self.repo.report_aggregate(server_ids, period_days=recommendation.WINDOW_DAYS, end=ref)
                if server_ids
                else []
            )
        else:
            server_ids = [r.server_id for r in raws]
        restart_counts: dict[int, int] = {}
        if server_ids:
            restart_counts = await self.repo.agent_restart_counts_recent(server_ids, ref - timedelta(hours=1))
        return self._assemble_attention(raws, gap_raws, restart_counts, ref, limit_each)

    def _assemble_attention(
        self,
        raws_period: list[ReportRowRaw],
        gap_raws: list[MetricGapWarningRaw],
        restart_counts: dict[int, int],
        now: datetime,
        limit_each: int | None,
    ) -> AttentionSignals:
        """gap/os_eol/agent_unstable 3 카탈로그 조립 — raws_period(report_aggregate) 재사용.

        agent_unstable: 1h 윈도우 재시작 임계 초과(server_inventory_history agent_started_at DISTINCT-1).
        """
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

    async def get_selection_attention(self, server_ids: list[int], end: datetime) -> AttentionSignals:
        """선택 N대 한정 운영 신호 — 실시간 현황(/environment/realtime?ids=) selection 범위 필터.

        전체 attention 을 선택 호스트 hostname 으로 필터 (get_selection_report 와 동일 _filter_attention 채널).
        """
        details = await self.repo.get_servers(server_ids)
        hostnames = {d.hostname for d in details}
        return _filter_attention(await self.get_attention_signals(end=end, limit_each=None), hostnames)
