"""보고서·인벤토리 export 조회 mixin — 환경/선택/단일 보고서 + child prefetch + KPI 집계."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from assessment_engine import recommendation
from assessment_engine.cache.redis import safe_get, safe_mget
from assessment_engine.db.dtos.outbound import (
    CpuBreakdownRaw,
    InventoryExportEntry,
    MemoryBreakdownRaw,
    ReportMountUsageRaw,
    ReportRowRaw,
)
from assessment_engine.db.repositories.base_diagnostic_repository import (
    DIAGNOSTIC_DEFAULT_TIME_RANGE,
    DIAGNOSTIC_RANGE_DAYS,
    DiagnosticTimeRange,
)
from assessment_engine.db.repositories.query.types import _BUCKET_INFO, AUTO_BUCKET, TIME_RANGE_TD
from assessment_engine.web.services.mappers.attention import build_action_targets
from assessment_engine.web.services.mappers.environment_report import build_metric_trend, to_environment_report
from assessment_engine.web.services.mappers.export import to_inventory_export_entry
from assessment_engine.web.services.mappers.report import (
    build_report_summary_bullets,
    build_role_distribution,
    build_selection_context,
    compute_report_avg_p95,
    compute_report_totals_from_raw,
    sort_rows_for_report,
    to_report_row_item,
)
from assessment_engine.web.services.mappers.server import (
    build_cpu_breakdown,
    build_memory_breakdown,
    build_server_inventory,
    build_volumes,
)
from assessment_engine.web.services.mappers.shared import ReportView
from assessment_engine.web.services.query._base import _BaseQueryServiceMixin, _empty_overview, _filter_attention
from assessment_engine.web.services.unit_converter import bytes_to_gb, kb_to_gb
from assessment_engine.web.settings import web_settings
from assessment_engine.web.view_models.attention import (
    AttentionSignals,
    EnvironmentOverview,
)
from assessment_engine.web.view_models.environment_report import EnvironmentReportSummary
from assessment_engine.web.view_models.report import ReportRowItem, ReportSummary
from assessment_engine.web.view_models.server import ServerDetailResponse


@dataclass
class _ChildPrefetch:
    """N대 fan-out generator 가 배치 조회한 1대분 — get_single_server_report 주입 (A5).

    raws·detail·breakdown 만 배치(report_aggregate·get_servers·breakdown 1회). trend·online redis 는
    서버별이라 get_single_server_report 내부 유지(AsyncSession 동시 await 금지로 배치/gather 불가).
    """

    raw: ReportRowRaw
    detail: ServerDetailResponse
    mount_raws: list[ReportMountUsageRaw]
    mem_raw: MemoryBreakdownRaw
    cpu_raw: CpuBreakdownRaw


class ReportQueryMixin(_BaseQueryServiceMixin):
    async def get_environment_report(
        self,
        time_range: DiagnosticTimeRange = DIAGNOSTIC_DEFAULT_TIME_RANGE,
        anchor_at: datetime | None = None,
        view: ReportView = "customer",
    ) -> EnvironmentReportSummary:
        """환경 단위 보고서 (전체 등록 서버 대상) — server scope 양식과 별도 high-level.

        time_range: 7개 윈도우 (15m/1h/6h/24h/7d/14d/30d) — DIAGNOSTIC_RANGE_DAYS.
        anchor_at: 보고서 기준 시각 (None 이면 현재 시각).
        """
        period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
        end_dt = anchor_at if anchor_at is not None else datetime.now(UTC)

        attention = await self.get_attention_signals(end=end_dt, limit_each=None)

        public_ids = await self.repo.list_all_server_public_ids()
        sid_map = await self.repo.resolve_server_ids(public_ids) if public_ids else {}
        server_ids = [sid_map[p] for p in public_ids if p in sid_map]

        raws_window: list = []
        if server_ids:
            details = await self.repo.get_servers(server_ids)
            # raws 1회 조립 후 get_report·overview·조치 대상 공유 (A2: report_aggregate/net_io 중복 제거).
            # overview(평균 활용률·분류 도넛)도 선택 time_range 윈도우+anchor 기준 — 선택 N대 보고서와 동일 경로.
            raws_window = await self._assemble_report_raws(server_ids, period_days, end_dt)
            base = await self.get_report(server_ids, period_days, end=end_dt, view=view, raws=raws_window)
            online_by_id = await self._online_map(server_ids, details, end_dt)
            util = await self.repo.environment_utilization(period_days=period_days, end=end_dt, server_ids=server_ids)
            overview = self._assemble_overview(details, util, raws_window, online_by_id)
        else:
            overview = _empty_overview()
            base = ReportSummary(
                rows=[],
                period_days=int(period_days),
                total=0,
                online=0,
                risk_attention=0,
                risk_high=0,
            )
            details = []

        # 환경 시계열 추이 — 발행 모달 time_range 윈도우의 CPU·메모리 평균 버킷. 정적 스냅샷 저장.
        trend = []
        if server_ids:
            trend = await self._build_report_trend(time_range, end_dt)

        return to_environment_report(
            view=view,
            time_range=time_range,
            anchor_at=end_dt,
            overview=overview,
            attention=attention,
            base=base,
            details=details,
            generated_at=datetime.now(UTC),
            action=build_action_targets(raws_window),
            trend=trend,
        )

    async def get_selection_report(
        self,
        server_public_ids: list[str],
        view: ReportView = "customer",
        time_range: str = DIAGNOSTIC_DEFAULT_TIME_RANGE,
        anchor_at: datetime | None = None,
    ) -> EnvironmentReportSummary | None:
        """선택 N대 보고서 — 환경 보고서 양식(`get_environment_report`)의 N대 scope 변형 (대상만 선택 서버 한정).

        평균 활용률은 environment_utilization 을 server_ids 로 N대 한정 호출 (전체 환경과 동일
        capacity-weighted SQL). attention 은 N대 호스트로 필터. 미존재/빈 선택 시 None.
        """
        # period_days 는 time_range 에서 내부 도출 (환경 보고서와 동일 — 호출자 불일치 여지 0).
        period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
        end_dt = anchor_at if anchor_at is not None else datetime.now(UTC)
        sid_map = await self.repo.resolve_server_ids(server_public_ids)
        server_ids = [sid_map[p] for p in server_public_ids if p in sid_map]
        if not server_ids:
            return None
        details = await self.repo.get_servers(server_ids)
        if not details:
            return None

        # raws 1회 조립 후 get_report·overview·under_hosts 공유 (A2: report_aggregate/net_io 중복 제거).
        raws_window = await self._assemble_report_raws(server_ids, period_days, end_dt)
        base = await self.get_report(server_ids, period_days, end=end_dt, view=view, raws=raws_window)
        online_by_id = await self._online_map(server_ids, details, end_dt)
        # 평균 활용률 — capacity-weighted (자원 총량 가중). 전체 환경과 동일 SQL, server_ids 로 N대 한정.
        util = await self.repo.environment_utilization(period_days=period_days, end=end_dt, server_ids=server_ids)
        overview = self._assemble_overview(details, util, raws_window, online_by_id)


        # 운영 신호 — 전체 attention 을 선택 N대 호스트로 필터 (os_eol_count 등 N대 정합).
        hostnames = {d.hostname for d in details}
        attention = _filter_attention(await self.get_attention_signals(end=end_dt, limit_each=None), hostnames)

        # 환경 시계열 추이 — 선택 N대 한정 (metric_trend server_ids). 환경 보고서와 동일 버킷 정책.
        trend = await self._build_report_trend(time_range, end_dt, server_ids)

        return to_environment_report(
            view=view,
            time_range=time_range,
            anchor_at=end_dt,
            overview=overview,
            attention=attention,
            base=base,
            details=details,
            generated_at=datetime.now(UTC),
            action=build_action_targets(raws_window),
            trend=trend,
        )

    async def get_single_server_report(
        self,
        server_public_id: str,
        view: ReportView = "customer",
        time_range: str = DIAGNOSTIC_DEFAULT_TIME_RANGE,
        anchor_at: datetime | None = None,
        attention: AttentionSignals | None = None,
        prefetch: _ChildPrefetch | None = None,
    ) -> EnvironmentReportSummary | None:
        """단일 서버 보고서 — 환경 보고서 양식 (`get_environment_report`) 의 1대 scope 변형.

        anchor_at: 발행 시점 기준 시각 (None 이면 현재) — 발행 스냅샷 윈도우 재현.
        attention: 호출자가 이미 수집한 전역 운영 신호 주입 (N대 fan-out 시 재계산 회피).
            None 이면 내부 1회 수집 (단독 라우트 호환). os_eol 만 보고서에 표시(C1).
        prefetch: N대 fan-out generator 가 배치 조회한 1대분(raw·detail·breakdown) 주입 (A5).
            None 이면 자체 조회. trend·online 은 서버별이라 prefetch 무관하게 내부 조회.
        """
        # period_days 는 time_range 에서 내부 도출 (환경·선택 보고서와 동일 — 호출자 불일치 여지 0).
        period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
        end_dt = anchor_at if anchor_at is not None else datetime.now(UTC)
        sid_map = await self.repo.resolve_server_ids([server_public_id])
        if server_public_id not in sid_map:
            return None
        server_id = sid_map[server_public_id]

        # prefetch: N대 fan-out generator 가 배치 조회한 1대분 주입(A5) — raws·detail·breakdown 만 배치.
        if prefetch is not None:
            detail = prefetch.detail
            raws_window = [prefetch.raw]
        else:
            fetched = await self.repo.get_servers([server_id])
            detail = fetched[0] if fetched else None
            if detail is None:
                return None
            # raws 1회 조립 후 get_report·under_hosts 공유 (A2: report_aggregate 중복 제거 + net·worst_mount
            # 주입으로 단일 보고서 under 분류가 세부 행과 정합 — 기존 single 은 net 미주입이었음).
            raws_window = await self._assemble_report_raws([server_id], period_days, end_dt)
        details = [detail]
        base = await self.get_report([server_id], period_days, end=end_dt, view=view, raws=raws_window)
        # N대 fan-out 은 라우터가 1회 수집한 attention 주입 — 루프마다 전역 집계(report_aggregate 전체서버) 재계산 회피.
        if attention is None:
            attention = await self.get_attention_signals(end=end_dt, limit_each=None)


        # overview — 단일 서버 자원량. is_online 은 Redis online TTL (fail-open) 기반.
        flag = await safe_get(self.redis, web_settings.redis_key_online.format(detail.id))
        if flag is not None:
            is_online = flag == "1"
        else:
            threshold = end_dt - timedelta(seconds=web_settings.redis_ttl_online)
            is_online = bool(detail.last_seen_at and detail.last_seen_at > threshold)

        # P2 단일 진실 — units helper 경유 (mapper·service 공통 단위 산식).
        mem_total_gb = kb_to_gb(detail.mem_total_kb) or 0.0
        disk_total_bytes = sum((d.get("size_bytes") or 0) for d in detail.disks) if detail.disks else 0
        disk_total_gb = int(bytes_to_gb(disk_total_bytes) or 0)
        overview = EnvironmentOverview(
            total=1,
            online=1 if is_online else 0,
            offline=0 if is_online else 1,
            total_vcpus=detail.cpu_cores or 0,
            total_memory_gb=mem_total_gb,
            total_disk_gb=disk_total_gb,
        )

        # 시계열 추이 — 1대 한정 (환경·선택 동일 버킷 정책). 개별 서버 부하 패턴.
        trend = await self._build_report_trend(time_range, end_dt, [server_id])

        summary = to_environment_report(
            view=view,
            time_range=time_range,
            anchor_at=end_dt,
            overview=overview,
            attention=attention,
            base=base,
            details=details,
            generated_at=datetime.now(UTC),
            action=build_action_targets(raws_window),
            trend=trend,
        )
        # 개별 보고서 충실 인벤토리 — ServerDetail 전체(전체 IP·하드웨어·식별자) 보존 (왜곡·생략 0).
        summary.server_inventory = build_server_inventory(detail, is_online)
        # 심화 메트릭 (engineer 전용) — 마운트별 스토리지·메모리 구성·CPU 분류 (윈도우 집계).
        if view == "engineer":
            if prefetch is not None:
                mount_raws, mem_raw, cpu_raw = prefetch.mount_raws, prefetch.mem_raw, prefetch.cpu_raw
            else:
                mount_raws = await self.repo.report_mount_usage(server_id, period_days, end_dt)
                mem_raw = await self.repo.report_memory_breakdown(server_id, period_days, end_dt)
                cpu_raw = await self.repo.report_cpu_breakdown(server_id, period_days, end_dt)
            summary.volumes = build_volumes(mount_raws)
            summary.memory_breakdown = build_memory_breakdown(mem_raw)
            summary.cpu_breakdown = build_cpu_breakdown(cpu_raw)
        return summary

    async def build_child_prefetched_reports(
        self,
        server_public_ids: list[str],
        sid_map: dict[str, int],
        view: ReportView,
        time_range: str,
        anchor_at: datetime,
        attention: AttentionSignals | None = None,
    ) -> list[tuple[str, EnvironmentReportSummary | None]]:
        """N대 child 단일 보고서 — 공통 데이터(raws·details·breakdown) 배치 1회 조회 후 서버별 조립 (A5).

        get_single_server_report 를 prefetch 주입으로 N회 호출 — report_aggregate(6N->6)·get_servers(N->1)·
        breakdown(3N->3) 배치 절감. trend·online redis 는 서버별이라 내부 유지(AsyncSession 동시 await 금지).
        반환 [(public_id, summary|None)] — 미존재 서버는 None.
        """
        period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
        sids = [sid_map[p] for p in server_public_ids if p in sid_map]
        if not sids:
            return [(p, None) for p in server_public_ids]
        raws_by_id = {r.server_id: r for r in await self._assemble_report_raws(sids, period_days, anchor_at)}
        details_by_id = {d.id: d for d in await self.repo.get_servers(sids)}
        mount_by_id: dict[int, list[ReportMountUsageRaw]] = {}
        mem_by_id: dict[int, MemoryBreakdownRaw] = {}
        cpu_by_id: dict[int, CpuBreakdownRaw] = {}
        if view == "engineer":
            mount_by_id = await self.repo.report_mount_usage_batch(sids, period_days, anchor_at)
            mem_by_id = await self.repo.report_memory_breakdown_batch(sids, period_days, anchor_at)
            cpu_by_id = await self.repo.report_cpu_breakdown_batch(sids, period_days, anchor_at)

        results: list[tuple[str, EnvironmentReportSummary | None]] = []
        for pid in server_public_ids:
            sid = sid_map.get(pid)
            raw = raws_by_id.get(sid) if sid is not None else None
            detail = details_by_id.get(sid) if sid is not None else None
            if raw is None or detail is None:
                results.append((pid, None))
                continue
            prefetch = _ChildPrefetch(
                raw=raw,
                detail=detail,
                # 배치는 GROUP BY 라 데이터 없는 서버는 행 부재 — 단수 .one()(null avg row)과 동치로 빈 객체 채움.
                mount_raws=mount_by_id.get(sid, []),
                mem_raw=mem_by_id.get(sid) or MemoryBreakdownRaw(None, None, None, None),
                cpu_raw=cpu_by_id.get(sid) or CpuBreakdownRaw(None, None, None),
            )
            summary = await self.get_single_server_report(
                pid, view=view, time_range=time_range, anchor_at=anchor_at, attention=attention, prefetch=prefetch
            )
            results.append((pid, summary))
        return results

    async def _build_report_trend(self, time_range: str, end_dt: datetime, server_ids: list[int] | None = None) -> list:
        """CPU·메모리·디스크 평균 시계열 추이 — 보고서 3경로 공유.

        server_ids=None 이면 전체 환경(env 보고서), 주어지면 선택 N대/1대 한정(selection·single). 버킷 정책 동일.
        """
        bi, bucket_td = _BUCKET_INFO[AUTO_BUCKET.get(time_range, "1h")]
        trend_start = end_dt - TIME_RANGE_TD[time_range]
        cpu_series = await self.repo.metric_trend("cpu.usage_percent", trend_start, end_dt, bi, bucket_td, server_ids)
        mem_series = await self.repo.metric_trend("mem.usage_percent", trend_start, end_dt, bi, bucket_td, server_ids)
        disk_series = await self.repo.metric_trend("disk.usage_percent", trend_start, end_dt, bi, bucket_td, server_ids)
        return build_metric_trend(cpu_series, mem_series, disk_series)

    async def _assemble_report_raws(self, server_ids: list[int], period_days: float, end_dt: datetime) -> list:
        """report_aggregate + 5 baseline(mount_worst·uptime·agent_restart·disk_io·net_io) 주입 raws.

        보고서 3경로(env·selection·single)가 get_report 세부 행·under_hosts 분류·overview 에 동일
        raws 를 공유 — report_aggregate/net_io 중복 호출 제거 + build_resource_stats(net·worst_mount
        포함, #E3) 입력 일치로 유휴 분류 정합(세부 행·under·도넛 동일 입력).
        """
        raws = await self.repo.report_aggregate(server_ids, period_days, end_dt)
        mount_worst = await self.repo.report_mount_worst(server_ids, period_days, end_dt)
        uptime_stats = await self.repo.report_uptime_stats(server_ids, period_days, end_dt)
        agent_restart_stats = await self.repo.report_agent_restart_stats(server_ids, period_days, end_dt)
        disk_io = await self.repo.report_disk_io_baseline(server_ids, period_days, end_dt)
        net_io = await self.repo.report_net_io_baseline(server_ids, period_days, end_dt)
        for raw in raws:
            mount_tuple = mount_worst.get(raw.server_id)
            if mount_tuple is not None:
                raw.worst_mount, raw.worst_mount_used_pct, raw.worst_mount_days_until_full = mount_tuple
            raw.reboot_count = uptime_stats.get(raw.server_id, 0)
            raw.agent_restart_count = agent_restart_stats.get(raw.server_id, 0)
            disk_tuple = disk_io.get(raw.server_id)
            if disk_tuple is not None:
                (
                    raw.disk_iops_baseline,
                    raw.disk_throughput_kbps,
                    raw.disk_iops_p95,
                    raw.disk_iops_peak,
                    raw.disk_throughput_kbps_p95,
                    raw.disk_throughput_kbps_peak,
                ) = disk_tuple
            net_tuple = net_io.get(raw.server_id)
            if net_tuple is not None:
                (
                    raw.net_rx_kbps,
                    raw.net_tx_kbps,
                    raw.net_rx_kbps_p95,
                    raw.net_rx_kbps_peak,
                    raw.net_tx_kbps_p95,
                    raw.net_tx_kbps_peak,
                ) = net_tuple
        return raws

    async def get_report(
        self,
        server_ids: list[int],
        period_days: float = recommendation.WINDOW_DAYS,
        end: datetime | None = None,
        view: ReportView = "customer",
        raws: list | None = None,
    ) -> ReportSummary:
        """Assessment 보고서 — raw → ViewModel + KPI 집계 (P2 단일 변환).

        repo는 raw stats(`ReportRowRaw`)만 산출. mapper(`to_report_row_item`)가 표시 파생
        (role/recommendation/badge_class/os_display) 채움. KPI도 service 책임.
        is_online은 Redis mget 일괄 (N+1 회피, fail-open 시 last_seen_at fallback).
        view는 summary_bullets 분기에만 사용 (양식 A/B로 행동 시그널 vs 엔지니어 시그널 분리).
        """
        end_dt = end or datetime.now(UTC)
        # raws 주입 시 재사용(보고서 3경로가 under_hosts 분류·overview 와 공유 — report_aggregate/net_io
        # 중복 호출 제거), 없으면 자체 조립(단독 라우트·dashboard 호환).
        if raws is None:
            raws = await self._assemble_report_raws(server_ids, period_days, end_dt)

        online_keys = [web_settings.redis_key_online.format(r.server_id) for r in raws]
        flags = await safe_mget(self.redis, online_keys)
        threshold = end_dt - timedelta(seconds=web_settings.redis_ttl_online)

        items: list[ReportRowItem] = []
        for i, raw in enumerate(raws):
            online = bool(raw.last_seen_at and raw.last_seen_at > threshold) if flags is None else flags[i] is not None
            items.append(to_report_row_item(raw, online, end_dt))

        avg_cpu, avg_mem = compute_report_avg_p95(items)
        role_dist = build_role_distribution(raws)
        os_family_summary, workload_summary = build_selection_context(items, role_dist)
        # N대 비교 표 — 위험 우선 정렬 (표시 파생, P2). 환경 base.rows 는 top_risks 별도라 무영향.
        sorted_items = sort_rows_for_report(items)

        return ReportSummary(
            rows=sorted_items,
            period_days=period_days,
            total=len(items),
            online=sum(1 for it in items if it.is_online),
            risk_attention=sum(1 for it in items if it.risk_level == "attention"),
            risk_high=sum(1 for it in items if it.risk_level == "high"),
            avg_cpu_p95_pct=avg_cpu,
            avg_mem_p95_pct=avg_mem,
            totals=compute_report_totals_from_raw(raws),
            summary_bullets=build_report_summary_bullets(items, raws, view=view),
            role_distribution=role_dist,
            os_family_summary=os_family_summary,
            workload_summary=workload_summary,
            anchor_at=end_dt,
            generated_at=datetime.now(UTC),
        )

    async def get_inventory_export(
        self,
        server_ids: list[int],
        period_days: float = recommendation.WINDOW_DAYS,
    ) -> list[InventoryExportEntry]:
        """선택 서버 N대의 정제 inventory JSON 항목 list.

        Right-sizing stats(cpu_p95/peak·mem_p95/peak·load·swap)도 같이 fetch하여 export에 포함.
        USE Method 보고서 SQL(`report_aggregate`) 재사용 — period_days 윈도우 통계.

        각 서버는 ServerDetail + ReportRowRaw -> mapper로 변환. 누락된 server_id는 silent skip.

        C5: `get_servers` + `report_aggregate` 단일 SQL 각 1회 — 입력 server_ids 순서 보존.
        스키마·정제 원칙·사용처: docs/architecture/web/export-schema.md.
        """
        end_dt = datetime.now(UTC)
        details = await self.repo.get_servers(server_ids)
        stats_rows = await self.repo.report_aggregate(server_ids, period_days, end_dt)
        disk_io = await self.repo.report_disk_io_baseline(server_ids, period_days, end_dt)
        net_io = await self.repo.report_net_io_baseline(server_ids, period_days, end_dt)

        # stats에 disk_io·net_io baseline + p95/peak 주입 (inventory-export 확장)
        for row in stats_rows:
            disk_tuple = disk_io.get(row.server_id)
            if disk_tuple is not None:
                (
                    row.disk_iops_baseline,
                    row.disk_throughput_kbps,
                    row.disk_iops_p95,
                    row.disk_iops_peak,
                    row.disk_throughput_kbps_p95,
                    row.disk_throughput_kbps_peak,
                ) = disk_tuple
            net_tuple = net_io.get(row.server_id)
            if net_tuple is not None:
                (
                    row.net_rx_kbps,
                    row.net_tx_kbps,
                    row.net_rx_kbps_p95,
                    row.net_rx_kbps_peak,
                    row.net_tx_kbps_p95,
                    row.net_tx_kbps_peak,
                ) = net_tuple

        stats_by_id = {row.server_id: row for row in stats_rows}
        order = {sid: i for i, sid in enumerate(server_ids)}
        details.sort(key=lambda d: order.get(d.id, len(server_ids)))
        return [to_inventory_export_entry(d, stats_by_id.get(d.id)) for d in details]
