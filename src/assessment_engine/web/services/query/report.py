"""보고서·인벤토리 export 조회 mixin — 환경/선택/단일 보고서 + child prefetch + KPI 집계."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from assessment_engine.cache.redis import safe_get, safe_mget
from assessment_engine.db.dtos.outbound import (
    CpuBreakdownRaw,
    DiskIoBaselineRaw,
    MemoryBreakdownRaw,
    NetIoBaselineRaw,
    ReportRowRaw,
    ServerDetail,
)
from assessment_engine.db.repositories.query.types import (
    AUTO_BUCKET,
    DIAGNOSTIC_DEFAULT_TIME_RANGE,
    DIAGNOSTIC_RANGE_DAYS,
    TIME_RANGE_TD,
    BucketSize,
    TimeRange,
)
from assessment_engine.domain import right_sizing
from assessment_engine.web.services.device_filters import disk_total_bytes
from assessment_engine.web.services.mappers.attention import build_action_targets
from assessment_engine.web.services.mappers.environment_report import (
    build_metric_trend,
    build_saturation_trend,
    to_environment_report,
)
from assessment_engine.web.services.mappers.metric_dashboard import build_error_signals
from assessment_engine.web.services.mappers.period_assessment import build_period_assessment
from assessment_engine.web.services.mappers.report import (
    build_role_distribution,
    build_selection_context,
    compute_report_avg_p95,
    compute_report_totals_from_raw,
    sort_rows_for_report,
    to_report_row_item,
)
from assessment_engine.web.services.mappers.report_summary import build_report_summary_bullets
from assessment_engine.web.services.mappers.resource_stats import build_resource_stats
from assessment_engine.web.services.mappers.server import (
    build_cpu_breakdown,
    build_memory_breakdown,
    build_network_interfaces,
    build_server_inventory,
    build_storage_tree,
)
from assessment_engine.web.services.query._base import (
    _BaseQueryServiceMixin,
    _empty_overview,
    _filter_attention,
    _net_baseline_fields,
)
from assessment_engine.web.services.query.attention import attention_signals
from assessment_engine.web.services.query.environment import assemble_overview
from assessment_engine.web.services.unit_converter import bytes_to_gb, bytes_to_gib
from assessment_engine.web.settings import get_web_settings
from assessment_engine.web.view_models.attention import (
    AttentionSignals,
    EnvironmentOverview,
)
from assessment_engine.web.view_models.report import ReportRowItem, ReportSummary

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject
    from assessment_engine.web.services.mappers.constants import ReportView
    from assessment_engine.web.view_models.environment_report import EnvironmentReportSummary


@dataclass
class _ChildPrefetch:
    """N대 fan-out generator 가 배치 조회한 1대분 — get_single_server_report 주입.

    trend·online redis 는 여기 담지 않는다 — AsyncSession 동시 await 금지라 배치·gather 가 불가하다.
    """

    raw: ReportRowRaw
    detail: ServerDetail
    mem_raw: MemoryBreakdownRaw
    cpu_raw: CpuBreakdownRaw


def _with_report_baselines(
    raw: ReportRowRaw,
    *,
    reboot_count: int,
    agent_restart_count: int,
    disk: DiskIoBaselineRaw | None,
    net: NetIoBaselineRaw | None,
) -> ReportRowRaw:
    """보고서 경로 raw 에 4 baseline 을 얹은 새 행 — 채우는 필드 집합이 곧 이 경로의 분류 입력이다.

    `_with_net_baseline`(net 만) 과 합치지 않는다: 채우는 집합이 다르고 그 차이가 화면 분류를 가른다
    (`disk_iops_baseline` 은 여기서만 채워진다, tradeoffs T24).
    """
    disk_fields = (
        {}
        if disk is None
        else {
            "disk_iops_baseline": disk.iops_baseline,
            "disk_throughput_kbps": disk.throughput_kbps_baseline,
            "disk_iops_p95": disk.iops_p95,
            "disk_iops_peak": disk.iops_peak,
            "disk_throughput_kbps_p95": disk.kbps_p95,
            "disk_throughput_kbps_peak": disk.kbps_peak,
        }
    )
    return replace(
        raw,
        reboot_count=reboot_count,
        agent_restart_count=agent_restart_count,
        **disk_fields,
        **_net_baseline_fields(net),
    )


class ReportQueryMixin(_BaseQueryServiceMixin):
    async def get_environment_report(
        self,
        time_range: TimeRange = DIAGNOSTIC_DEFAULT_TIME_RANGE,
        anchor_at: datetime | None = None,
        view: ReportView = "customer",
    ) -> EnvironmentReportSummary:
        """환경 단위 보고서(전체 등록 서버) — server scope 양식과 별도 high-level. anchor_at=None 이면 현재 시각."""
        period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
        end_dt = anchor_at if anchor_at is not None else datetime.now(UTC)

        public_ids = await self.repo.list_server_public_ids()
        sid_map = await self.repo.resolve_server_ids(public_ids) if public_ids else {}
        server_ids = [sid_map[p] for p in public_ids if p in sid_map]

        raws_window: list[ReportRowRaw] = []
        if server_ids:
            details = await self.repo.get_servers(server_ids)
            raws_window = await self._assemble_report_raws(server_ids, period_days, end_dt)
            base = await self.get_report(server_ids, period_days, end=end_dt, view=view, raws=raws_window)
            online_by_id = await self._online_map(server_ids, details, end_dt)
            util = await self.repo.get_environment_utilization(
                period_days=period_days, end=end_dt, server_ids=server_ids
            )
            overview = assemble_overview(details, util, raws_window, online_by_id)
        else:
            overview = _empty_overview()
            base = ReportSummary(
                rows=[],
                period_days=period_days,
                total=0,
                online=0,
                risk_attention=0,
                risk_high=0,
            )
            details = []

        # os_eol·agent 신호는 창 독립이라 raws_window 재사용이 전체 재조회와 같은 결과다.
        attention = await attention_signals(self.repo, end=end_dt, limit_each=None, raws=raws_window)

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
        """선택 N대 보고서 — 환경 보고서 양식(`get_environment_report`)의 N대 scope 변형. 미존재·빈 선택이면 None."""
        # period_days 를 파라미터로 받지 않는다 — 호출자가 time_range 와 어긋나게 줄 여지를 없앤다.
        period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
        end_dt = anchor_at if anchor_at is not None else datetime.now(UTC)
        sid_map = await self.repo.resolve_server_ids(server_public_ids)
        server_ids = [sid_map[p] for p in server_public_ids if p in sid_map]
        if not server_ids:
            return None
        details = await self.repo.get_servers(server_ids)
        if not details:
            return None

        raws_window = await self._assemble_report_raws(server_ids, period_days, end_dt)
        # N 이 사용자 선택분이라 작아 per-server get_latest_errors 가 안전하다 (환경 전체 보고서는 N+1).
        base = await self.get_report(
            server_ids, period_days, end=end_dt, view=view, raws=raws_window, fetch_operational_events=True
        )
        online_by_id = await self._online_map(server_ids, details, end_dt)
        util = await self.repo.get_environment_utilization(period_days=period_days, end=end_dt, server_ids=server_ids)
        overview = assemble_overview(details, util, raws_window, online_by_id)

        # os_eol·agent 는 창 독립이라 선택 raws 로 산출 후 hostname 필터 = 전체 산출 후 필터와 동일 결과.
        hostnames = {d.hostname for d in details}
        attention = _filter_attention(
            await attention_signals(self.repo, end=end_dt, limit_each=None, raws=raws_window), hostnames
        )

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
        """단일 서버 보고서 — 환경 보고서 양식(`get_environment_report`)의 1대 scope 변형. 미존재 서버면 None.

        attention·prefetch 는 N대 fan-out 호출자가 1회 수집·배치한 것을 주입해 서버마다 재계산하지 않게 한다.
        None 이면 자체 조회(단독 라우트).
        """
        period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
        end_dt = anchor_at if anchor_at is not None else datetime.now(UTC)
        sid_map = await self.repo.resolve_server_ids([server_public_id])
        if server_public_id not in sid_map:
            return None
        server_id = sid_map[server_public_id]

        if prefetch is not None:
            detail = prefetch.detail
            raws_window = [prefetch.raw]
        else:
            fetched = await self.repo.get_servers([server_id])
            detail = fetched[0] if fetched else None
            if detail is None:
                return None
            raws_window = await self._assemble_report_raws([server_id], period_days, end_dt)
        details = [detail]
        base = await self.get_report(
            [server_id], period_days, end=end_dt, view=view, raws=raws_window, fetch_operational_events=True
        )
        if attention is None:
            attention = await attention_signals(self.repo, end=end_dt, limit_each=None)

        flag = await safe_get(self.redis, get_web_settings().redis_key_online.format(detail.id))
        if flag is not None:
            is_online = flag == "1"
        else:
            threshold = end_dt - timedelta(seconds=get_web_settings().redis_ttl_online)
            is_online = bool(detail.last_seen_at and detail.last_seen_at > threshold)

        mem_total_gb = bytes_to_gib(detail.mem_total_bytes) or 0.0
        disk_total_gb = int(bytes_to_gb(disk_total_bytes(detail.block_devices)) or 0)
        overview = EnvironmentOverview(
            total=1,
            online=1 if is_online else 0,
            offline=0 if is_online else 1,
            total_vcpus=detail.cpu_cores or 0,
            total_memory_gb=mem_total_gb,
            total_disk_gb=disk_total_gb,
        )

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
        summary.server_inventory = build_server_inventory(detail, is_online, raws_window[0] if raws_window else None)
        if view == "engineer":
            if prefetch is not None:
                mem_raw, cpu_raw = prefetch.mem_raw, prefetch.cpu_raw
            else:
                mem_raw = await self.repo.get_report_memory_breakdown(server_id, period_days, end_dt)
                cpu_raw = await self.repo.get_report_cpu_breakdown(server_id, period_days, end_dt)
            summary.memory_breakdown = build_memory_breakdown(mem_raw)
            summary.cpu_breakdown = build_cpu_breakdown(cpu_raw)

            # get_latest_errors 는 get_report 안에서도 부르지만 거기 결과는 폐기된다 — N=1 이라 한 번 더 조회.
            raw0 = raws_window[0]
            win_days = int(period_days)
            err = await self.repo.get_latest_errors(server_id, end_dt - timedelta(days=period_days))
            errors = build_error_signals(err, window_label=f"최근 {win_days}일", os_family=raw0.os_family)
            summary.period_assessment = build_period_assessment(
                build_resource_stats(raw0, disk_baseline=raw0.disk_iops_baseline),
                errors,
                disk_worst_mount=raw0.disk_capacity_worst_mount,
                window_days=win_days,
            )

            # 스토리지 트리만 창 집계가 아니라 현재 스냅샷이다.
            storage_dto = await self.repo.get_storage(server_id)
            if storage_dto is not None:
                summary.storage_tree = build_storage_tree(
                    storage_dto.block_devices, storage_dto.lvm_vgs, storage_dto.filesystems
                )

            # 활동(RX/TX)은 보고서 윈도우 표(net_rx_kbps 등)가 이미 갖고 있어 라이브 스냅샷을 겹쳐 넣지 않는다.
            summary.network_interfaces = build_network_interfaces(raw0.net_interfaces or [])

            summary.sat_trend = await self._build_report_saturation_trend(time_range, end_dt, [server_id])
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
        """N대 child 단일 보고서 — 공통 데이터(raws·details·breakdown)를 배치 1회 조회 후 서버별 조립.

        반환 [(public_id, summary|None)] — 미존재 서버는 None.
        """
        period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
        sids = [sid_map[p] for p in server_public_ids if p in sid_map]
        if not sids:
            return [(p, None) for p in server_public_ids]
        raws_by_id = {r.server_id: r for r in await self._assemble_report_raws(sids, period_days, anchor_at)}
        details_by_id = {d.id: d for d in await self.repo.get_servers(sids)}
        mem_by_id: dict[int, MemoryBreakdownRaw] = {}
        cpu_by_id: dict[int, CpuBreakdownRaw] = {}
        if view == "engineer":
            mem_by_id = await self.repo.get_report_memory_breakdown_batch(sids, period_days, anchor_at)
            cpu_by_id = await self.repo.get_report_cpu_breakdown_batch(sids, period_days, anchor_at)

        results: list[tuple[str, EnvironmentReportSummary | None]] = []
        for pid in server_public_ids:
            sid = sid_map.get(pid)
            raw = raws_by_id.get(sid) if sid is not None else None
            detail = details_by_id.get(sid) if sid is not None else None
            if sid is None or raw is None or detail is None:
                results.append((pid, None))
                continue
            prefetch = _ChildPrefetch(
                raw=raw,
                detail=detail,
                # 배치는 GROUP BY 라 데이터 없는 서버는 행 부재 — 단수 .one()(null avg row)과 동치로 빈 객체 채움.
                mem_raw=mem_by_id.get(sid) or MemoryBreakdownRaw(None, None, None, None),
                cpu_raw=cpu_by_id.get(sid) or CpuBreakdownRaw(None, None, None),
            )
            summary = await self.get_single_server_report(
                pid, view=view, time_range=time_range, anchor_at=anchor_at, attention=attention, prefetch=prefetch
            )
            results.append((pid, summary))
        return results

    async def _build_report_trend(
        self, time_range: str, end_dt: datetime, server_ids: list[int] | None = None
    ) -> list[JsonObject]:
        """CPU·메모리·디스크 평균 시계열 추이 — server_ids=None 이면 전체 환경, 주어지면 그 서버들 한정."""
        bucket = cast("BucketSize", AUTO_BUCKET.get(time_range, "1h"))
        trend_start = end_dt - TIME_RANGE_TD[time_range]
        cpu_series = await self.repo.get_metric_trend("cpu.usage_percent", trend_start, end_dt, bucket, server_ids)
        mem_series = await self.repo.get_metric_trend("mem.usage_percent", trend_start, end_dt, bucket, server_ids)
        disk_series = await self.repo.get_metric_trend("fs.usage_percent", trend_start, end_dt, bucket, server_ids)
        return build_metric_trend(cpu_series, mem_series, disk_series)

    async def _build_report_saturation_trend(
        self, time_range: str, end_dt: datetime, server_ids: list[int]
    ) -> list[JsonObject]:
        """CPU 실행 큐·메모리 페이징·디스크 I/O 포화 이진(0/1) 시계열 — 개별 서버 보고서(engineer) 전용.

        환경·선택 스코프는 노출 지점이 없어 1대 고정(server_ids 필수)으로 뒀다.
        """
        bucket = cast("BucketSize", AUTO_BUCKET.get(time_range, "1h"))
        trend_start = end_dt - TIME_RANGE_TD[time_range]
        cpu_series = await self.repo.get_metric_trend("cpu.saturation", trend_start, end_dt, bucket, server_ids)
        mem_series = await self.repo.get_metric_trend("mem.paging_pressure", trend_start, end_dt, bucket, server_ids)
        disk_series = await self.repo.get_metric_trend("disk.saturation", trend_start, end_dt, bucket, server_ids)
        return build_saturation_trend(cpu_series, mem_series, disk_series)

    async def _assemble_report_raws(
        self, server_ids: list[int], period_days: float, end_dt: datetime
    ) -> list[ReportRowRaw]:
        """get_report_aggregate + 4 baseline(uptime·agent_restart·disk_io·net_io) 주입 raws.

        보고서 3경로가 이 결과 하나를 세부 행·under 분류·overview 에 함께 쓴다 — 중복 조회 제거보다
        build_resource_stats 입력이 같아야 화면 간 분류가 어긋나지 않는 쪽이 이유다.
        """
        raws = await self.repo.get_report_aggregate(
            server_ids,
            period_days,
            end_dt,
        )
        uptime_stats = await self.repo.get_report_uptime_stats(
            server_ids,
            period_days,
            end_dt,
        )
        agent_restart_stats = await self.repo.get_report_agent_restart_stats(
            server_ids,
            period_days,
            end_dt,
        )
        disk_io = await self.repo.get_report_disk_io_baseline(
            server_ids,
            period_days,
            end_dt,
        )
        net_io = await self.repo.get_report_net_io_baseline(
            server_ids,
            period_days,
            end_dt,
        )
        return [
            _with_report_baselines(
                raw,
                reboot_count=uptime_stats.get(raw.server_id, 0),
                agent_restart_count=agent_restart_stats.get(raw.server_id, 0),
                disk=disk_io.get(raw.server_id),
                net=net_io.get(raw.server_id),
            )
            for raw in raws
        ]

    async def get_report(
        self,
        server_ids: list[int],
        period_days: float = right_sizing.WINDOW_DAYS,
        end: datetime | None = None,
        view: ReportView = "customer",
        raws: list[ReportRowRaw] | None = None,
        fetch_operational_events: bool = False,
    ) -> ReportSummary:
        """Assessment 보고서 — raw -> ViewModel + KPI 집계. view 는 summary_bullets 분기에만 쓰인다.

        fetch_operational_events=True 는 서버별 get_latest_errors 로 has_operational_event 를 채운다 —
        환경 전체(server_ids 최대 수백)까지 켜면 N+1 이라 작은 N(선택·단일 보고서)에서만 명시적으로 켠다.
        """
        end_dt = end or datetime.now(UTC)
        if raws is None:
            raws = await self._assemble_report_raws(server_ids, period_days, end_dt)

        online_keys = [get_web_settings().redis_key_online.format(r.server_id) for r in raws]
        flags = await safe_mget(self.redis, online_keys)
        threshold = end_dt - timedelta(seconds=get_web_settings().redis_ttl_online)
        window_start = end_dt - timedelta(days=period_days)

        items: list[ReportRowItem] = []
        for i, raw in enumerate(raws):
            online = bool(raw.last_seen_at and raw.last_seen_at > threshold) if flags is None else flags[i] is not None
            has_event = False
            if fetch_operational_events:
                err = await self.repo.get_latest_errors(raw.server_id, window_start)
                has_event = bool(
                    err.mce_count
                    or err.oom_count
                    or (err.corrupted_bytes or 0) > 0
                    or err.net_error_count
                    or err.disk_error_count
                )
            items.append(to_report_row_item(raw, online, end_dt, has_event))

        avg_cpu, avg_mem = compute_report_avg_p95(items)
        role_dist = build_role_distribution(raws)
        os_family_summary, workload_summary = build_selection_context(items, role_dist)
        # 환경 보고서는 top_risks 를 따로 뽑아 이 정렬에 영향받지 않는다.
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

    # inventory export 는 여기 없다 — /api/exports/inventory 가 assessment envelope 로 서비스(get_assessment).
