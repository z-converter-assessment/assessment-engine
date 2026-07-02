"""환경 개요·실시간·자원평가·토폴로지 조회 mixin."""

from datetime import UTC, datetime, timedelta

from assessment_engine import recommendation
from assessment_engine.db.repositories.base_diagnostic_repository import (
    DIAGNOSTIC_RANGE_DAYS,
    DiagnosticTimeRange,
)
from assessment_engine.db.repositories.query.types import TimeRange
from assessment_engine.web.services.mappers.attention import (
    build_environment_overview,
    build_environment_realtime,
    to_capacity_warning_item,
)
from assessment_engine.web.services.mappers.environment_report import build_efficiency_summary
from assessment_engine.web.services.mappers.report import build_resource_stats
from assessment_engine.web.services.mappers.shared import _DONUT_SEGMENT_FROM_REC
from assessment_engine.web.services.mappers.topology import build_network_topology
from assessment_engine.web.services.query._base import _BaseQueryServiceMixin, _empty_overview
from assessment_engine.web.settings import web_settings
from assessment_engine.web.view_models.attention import (
    CapacityWarningItem,
    EnvironmentAssessment,
    EnvironmentOverview,
    EnvironmentRealtime,
)
from assessment_engine.web.view_models.topology import NetworkTopology

# 대시보드 현황 윈도우 — 활용률 게이지·자원 적정성 분류 표시 전용 (최근 현황 모니터링).
# right-sizing 표준 평가 윈도우(recommendation.WINDOW_DAYS=7d — 보고서 기본·서버 목록 분류)와 의도 분리.
DASHBOARD_TIME_RANGE: TimeRange = "24h"
DASHBOARD_WINDOW_DAYS: float = DIAGNOSTIC_RANGE_DAYS[DASHBOARD_TIME_RANGE]


def _io_sum(snaps: list, *attrs: str) -> float | None:
    """I/O 스냅샷 list 의 attr(들) 환경/서버 합산 — None 제외, 유효값 0개면 None(페어 부재)."""
    total: float | None = None
    for s in snaps:
        for a in attrs:
            v = getattr(s, a)
            if v is not None:
                total = (total or 0.0) + v
    return None if total is None else round(total, 1)


class EnvironmentQueryMixin(_BaseQueryServiceMixin):
    async def get_environment_overview(self) -> EnvironmentOverview:
        """list 화면 상단 환경 요약 — 총 N대·온라인/오프라인·자원 합계·역할 분포·평균 활용률·위험도 분포 (P2).

        period_days는 보고서·right-sizing과 동일 윈도우 (AWS Compute Optimizer 표준).
        """
        now = datetime.now(UTC)
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return _empty_overview()
        details = await self.repo.get_servers(server_ids)
        util = await self.repo.environment_utilization(period_days=recommendation.WINDOW_DAYS, end=now)
        raws_period = await self.repo.report_aggregate(server_ids, period_days=recommendation.WINDOW_DAYS, end=now)
        await self._inject_net_baseline(raws_period, server_ids, recommendation.WINDOW_DAYS, now)
        online_by_id = await self._online_map(server_ids, details, now)
        return self._assemble_overview(details, util, raws_period, online_by_id)

    async def get_environment_realtime(self, server_ids: list[int] | None = None) -> EnvironmentRealtime:
        """list 화면 '환경 실시간 메트릭' 카드 — 각 서버 최신 스냅샷(get_latest_metric, Redis cache 우선) 집계.

        현황 모니터링 용도(right-sizing 7일 통계와 별개). server_ids=None 이면 전체 환경, 주어지면 선택 N대 한정.
        조립은 _assemble_realtime 단일 진실.
        """
        now = datetime.now(UTC)
        if server_ids is None:
            server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return build_environment_realtime(0, 0, [], None)
        details = await self.repo.get_servers(server_ids)
        return await self._assemble_realtime(server_ids, details, now)

    def _assemble_overview(
        self, details, util, raws_period, online_by_id: dict[int, bool], full_under: bool = False
    ) -> EnvironmentOverview:
        """report_aggregate raws -> USE Method 분류 도넛 + under_provisioned 상세, online_by_id -> online_count.

        분류는 `build_resource_stats`(net 반영) 단일 진실 — 호출자가 `_inject_net_baseline` 로 raw 에 net
        baseline 을 주입한 뒤 호출한다 (get_report 세부행과 동일 입력 -> idle/shutdown 정합).
        full_under=True 면 자원 부족 호스트 전체(상위 N 절단 해제) — 자원 평가 전용 페이지용.
        """
        risk_counts: dict[str, int] = {}
        under_hosts: list[CapacityWarningItem] = []
        for raw in raws_period:
            rec = recommendation.classify(build_resource_stats(raw))
            seg = _DONUT_SEGMENT_FROM_REC.get(rec, "insufficient_data")
            risk_counts[seg] = risk_counts.get(seg, 0) + 1
            if rec == "under_provisioned":
                under_hosts.append(to_capacity_warning_item(raw))
        online_count = sum(1 for d in details if online_by_id.get(d.id))
        if full_under:
            return build_environment_overview(details, online_count, util, risk_counts, under_hosts, under_limit=None)
        return build_environment_overview(details, online_count, util, risk_counts, under_hosts)

    async def get_environment_assessment(
        self, time_range: DiagnosticTimeRange, anchor_at: datetime | None = None
    ) -> EnvironmentAssessment:
        """자원 평가 전용 — 윈도우(time_range)·앵커(anchor) 기준 자원 적정성 분류 + 효율화/자원 부족 표.

        get_dashboard_overview 의 overview 조립부를 가변 윈도우·앵커로 재사용(attention/trend 제외, 경량).
        자원 부족은 상위 N 절단 없이 전체(full_under). 효율화 검토 대상은 보고서와 동일 산식
        (`build_efficiency_summary`) — base.rows(get_report) 단일 진실에서 도출해 보고서와 분류·정렬 일관.
        """
        end = anchor_at or datetime.now(UTC)
        period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return EnvironmentAssessment(overview=_empty_overview())
        details = await self.repo.get_servers(server_ids)
        raws_period = await self.repo.report_aggregate(server_ids, period_days=period_days, end=end)
        await self._inject_net_baseline(raws_period, server_ids, period_days, end)
        util = await self.repo.environment_utilization(period_days=period_days, end=end)
        online_by_id = await self._online_map(server_ids, details, end)
        overview = self._assemble_overview(details, util, raws_period, online_by_id, full_under=True)
        # 효율화 검토 대상 — 진단·권고 컬럼이 ReportRowItem 파생이라 get_report 행에서 산출 (보고서와 동일 경로).
        base = await self.get_report(server_ids, period_days, end=end, view="engineer")
        eff = build_efficiency_summary(base.rows)
        under = overview.under_provisioned_hosts
        return EnvironmentAssessment(
            overview=overview,
            efficiency_hosts=eff.hosts,
            efficiency_hosts_count=eff.hosts_count,
            efficiency_target_count=eff.target_count,
            efficiency_target_vcpus=eff.target_vcpus,
            efficiency_target_memory_gb=eff.target_memory_gb,
            under_provisioned_metric_labels=[m.label for m in under[0].metrics] if under else [],
        )

    async def _assemble_realtime(self, server_ids, details, now) -> EnvironmentRealtime:
        """각 서버 최신 스냅샷(get_latest_metric) 집계 — 신선한 데이터 있으면 포함(데이터 유무 = 온라인).

        표본은 최신 스냅샷 collected_at 이 신선(now-TTL 이내)한 서버만 — stale 메트릭이 현황 평균 왜곡 방지.
        online = 신선 데이터 서버 수(차트의 '그 시점 발행 서버' 기준과 동일 정렬). sample_size/total 표기.
        """
        detail_by_id = {d.id: d for d in details}
        fresh_threshold = now - timedelta(seconds=web_settings.redis_ttl_online)
        online = 0
        snapshots: list[dict] = []
        last_collected = None
        for sid in server_ids:
            d = detail_by_id.get(sid)
            if d is None:
                continue
            m = await self.get_latest_metric(sid)
            if not m or not m.collected_at or m.collected_at < fresh_threshold:
                continue  # 데이터 없음/stale = 오프라인 (통일: 데이터 신선도가 곧 온라인)
            online += 1
            mem = m.memory
            # 디스크 활용률 — 전 mount 통합 풀(sum(used)/sum(total)). 평균 도넛·탑3 동일 기준(worst mount 아님).
            fs_total = sum(mt.total_gb for mt in m.mounts if mt.total_gb and mt.used_gb is not None)
            fs_used = sum(mt.used_gb for mt in m.mounts if mt.total_gb and mt.used_gb is not None)
            disk_pool_pct = round(fs_used / fs_total * 100, 1) if fs_total else None
            snapshots.append(
                {
                    "hostname": d.hostname,
                    "public_id": d.public_id,
                    # 부하 상위(서버별 값) — 개별 호스트 랭킹. CPU/메모리/디스크 활용률 + I/O rate.
                    "cpu_pct": m.cpu.usage_pct if m.cpu else None,
                    "mem_pct": mem.usage_pct if mem else None,
                    "disk_pct": disk_pool_pct,  # 통합 풀 — 평균 도넛(fs_used/fs_total)과 동일 기준
                    # I/O rate — CPU 와 동일 2행 페어 delta (build_dashboard 산출분). 물리 디스크·실 iface 합산.
                    # 디스크=IOPS(작업), 네트워크=처리량(MB/s) 단일 지표만 (read/write·rx/tx 합산).
                    "disk_iops": _io_sum(m.disk_io_phys, "read_iops", "write_iops"),
                    "net_kbps": _io_sum(m.net_io, "rx_kbps", "tx_kbps"),
                    # capacity-weighted 평균용 가중치 (cpu=코어 가중, mem/disk=절대 총량 sum/sum).
                    "cpu_cores": d.cpu_cores,
                    "mem_used_kb": mem.used_kb if mem else None,
                    "mem_total_kb": mem.total_kb if mem else None,
                    "fs_used_gb": fs_used if fs_total else None,
                    "fs_total_gb": fs_total if fs_total else None,
                }
            )
            if last_collected is None or m.collected_at > last_collected:
                last_collected = m.collected_at
        return build_environment_realtime(len(server_ids), online, snapshots, last_collected)

    async def get_dashboard_overview(self) -> EnvironmentOverview:
        """환경 개요(`/`) 집계 — 24h(DASHBOARD_WINDOW_DAYS) capacity-weighted 평균 활용률·자원 합계·수집 상태.

        right-sizing 표준 평가(7일)와 의도 분리한 최근 24h 현황 (#F10 DASHBOARD_TIME_RANGE). 운영 신호
        (attention)는 실시간 현황 페이지(`get_attention_signals`)로 분리 — 본 메서드는 overview 단일 책임.
        """
        now = datetime.now(UTC)
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return _empty_overview()
        details = await self.repo.get_servers(server_ids)
        raws_period = await self.repo.report_aggregate(server_ids, period_days=DASHBOARD_WINDOW_DAYS, end=now)
        await self._inject_net_baseline(raws_period, server_ids, DASHBOARD_WINDOW_DAYS, now)
        util = await self.repo.environment_utilization(period_days=DASHBOARD_WINDOW_DAYS, end=now)
        online_by_id = await self._online_map(server_ids, details, now)
        return self._assemble_overview(details, util, raws_period, online_by_id)

    async def get_topology(self) -> NetworkTopology:
        """네트워크 토폴로지 — 전체 인벤토리의 L3 subnet 공동소속 그래프.

        개요(get_dashboard_overview)와 분리: 노드 규모가 커 별도 페이지(`/servers/topology`)에서 렌더.
        """
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return build_network_topology([])
        details = await self.repo.get_servers(server_ids)
        return build_network_topology(details)
