"""보고서·인벤토리 export 조회 mixin — 환경/선택/단일 보고서 + child prefetch + KPI 집계."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast

from assessment_engine import recommendation
from assessment_engine.cache.redis import safe_get, safe_mget
from assessment_engine.db.dtos.outbound import (
    CpuBreakdownRaw,
    FleetErrorRaw,
    MemoryBreakdownRaw,
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
from assessment_engine.web.services.device_filters import disk_total_bytes
from assessment_engine.web.services.mappers.attention import build_action_targets
from assessment_engine.web.services.mappers.environment_report import (
    build_metric_trend,
    build_saturation_trend,
    to_environment_report,
)
from assessment_engine.web.services.mappers.report import (
    build_period_assessment,
    build_report_summary_bullets,
    build_resource_stats,
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
    build_network_interfaces,
    build_server_inventory,
    build_storage_tree,
)
from assessment_engine.web.services.mappers.shared import ReportView
from assessment_engine.web.services.metrics_calculator import build_error_signals
from assessment_engine.web.services.query._base import _BaseQueryServiceMixin, _empty_overview, _filter_attention
from assessment_engine.web.services.unit_converter import bytes_to_gb, bytes_to_gib
from assessment_engine.web.settings import get_web_settings
from assessment_engine.web.view_models.attention import (
    AttentionSignals,
    EnvironmentOverview,
)
from assessment_engine.web.view_models.environment_report import EnvironmentReportSummary
from assessment_engine.web.view_models.report import ReportRowItem, ReportSummary

if TYPE_CHECKING:

    class _CrossDomainCalls(Protocol):
        """본 mixin 이 self 로 호출하는 타 도메인 mixin(environment·attention) 메서드 계약.

        QueryService 가 6 mixin 을 multiple inheritance 로 결합할 때 MRO 로 해결되는 호출이라
        본 mixin 자신에는 정의가 없다 — 계약만 선언하고 구현은 각 도메인 mixin 이 갖는다.
        """

        def _assemble_overview(
            self,
            details,
            util,
            raws_period,
            online_by_id: dict[int, bool],
            full_under: bool = ...,
            error_summary: FleetErrorRaw | None = ...,
        ) -> EnvironmentOverview: ...

        async def get_attention_signals(
            self,
            disk_threshold_pct: float = ...,
            gap_minutes: int = ...,
            gap_recent_hours: int = ...,
            limit_each: int | None = ...,
            days_until_full_threshold: int = ...,
            end: datetime | None = ...,
            raws: list | None = ...,
        ) -> AttentionSignals: ...

    class _ReportMixinBase(_BaseQueryServiceMixin, _CrossDomainCalls): ...

else:
    _ReportMixinBase = _BaseQueryServiceMixin


@dataclass
class _ChildPrefetch:
    """N대 fan-out generator 가 배치 조회한 1대분 — get_single_server_report 주입 (A5).

    raws·detail·breakdown 만 배치(report_aggregate·get_servers·breakdown 1회). trend·online redis 는
    서버별이라 get_single_server_report 내부 유지(AsyncSession 동시 await 금지로 배치/gather 불가).
    """

    raw: ReportRowRaw
    detail: ServerDetail
    mem_raw: MemoryBreakdownRaw
    cpu_raw: CpuBreakdownRaw


class ReportQueryMixin(_ReportMixinBase):
    async def get_environment_report(
        self,
        time_range: TimeRange = DIAGNOSTIC_DEFAULT_TIME_RANGE,
        anchor_at: datetime | None = None,
        view: ReportView = "customer",
    ) -> EnvironmentReportSummary:
        """환경 단위 보고서 (전체 등록 서버 대상) — server scope 양식과 별도 high-level.

        time_range: 7개 윈도우 (15m/1h/6h/24h/7d/14d/30d) — DIAGNOSTIC_RANGE_DAYS.
        anchor_at: 보고서 기준 시각 (None 이면 현재 시각).
        """
        period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
        end_dt = anchor_at if anchor_at is not None else datetime.now(UTC)

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
                period_days=period_days,
                total=0,
                online=0,
                risk_attention=0,
                risk_high=0,
            )
            details = []

        # 운영 신호 — 이미 산출한 raws_window 재사용(B2: os_eol/agent 는 창 독립이라 aggregate 재조회 생략).
        attention = await self.get_attention_signals(end=end_dt, limit_each=None, raws=raws_window)

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
        # fetch_operational_events=True — 세부 서버 목록(N대 선택 전용, "운영 이벤트" 열)에 필요. N 이
        # 사용자 선택분이라 작아 latest_errors per-server 조회가 안전(환경 전체는 기본 False 로 미조회).
        base = await self.get_report(
            server_ids, period_days, end=end_dt, view=view, raws=raws_window, fetch_operational_events=True
        )
        online_by_id = await self._online_map(server_ids, details, end_dt)
        # 평균 활용률 — capacity-weighted (자원 총량 가중). 전체 환경과 동일 SQL, server_ids 로 N대 한정.
        util = await self.repo.environment_utilization(period_days=period_days, end=end_dt, server_ids=server_ids)
        overview = self._assemble_overview(details, util, raws_window, online_by_id)


        # 운영 신호 — 선택 N대 raws_window 재사용(B2). os_eol/agent 는 창 독립이라 선택 raws 로 산출 후
        # hostname 필터 = 전체 산출 후 필터와 동일 결과(선택분만 계산, 전체 aggregate 재조회 생략).
        hostnames = {d.hostname for d in details}
        attention = _filter_attention(
            await self.get_attention_signals(end=end_dt, limit_each=None, raws=raws_window), hostnames
        )

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
        # fetch_operational_events=True — 단일 서버라 N=1, latest_errors 1건 추가 조회는 안전(N+1 무관).
        base = await self.get_report(
            [server_id], period_days, end=end_dt, view=view, raws=raws_window, fetch_operational_events=True
        )
        # N대 fan-out 은 라우터가 1회 수집한 attention 주입 — 루프마다 전역 집계(report_aggregate 전체서버) 재계산 회피.
        if attention is None:
            attention = await self.get_attention_signals(end=end_dt, limit_each=None)


        # overview — 단일 서버 자원량. is_online 은 Redis online TTL (fail-open) 기반.
        flag = await safe_get(self.redis, get_web_settings().redis_key_online.format(detail.id))
        if flag is not None:
            is_online = flag == "1"
        else:
            threshold = end_dt - timedelta(seconds=get_web_settings().redis_ttl_online)
            is_online = bool(detail.last_seen_at and detail.last_seen_at > threshold)

        # P2 단일 진실 — units helper 경유 (mapper·service 공통 단위 산식).
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
        summary.server_inventory = build_server_inventory(detail, is_online, raws_window[0] if raws_window else None)
        # 심화 메트릭 (engineer 전용) — 메모리 구성·CPU 분류 (윈도우 집계).
        if view == "engineer":
            if prefetch is not None:
                mem_raw, cpu_raw = prefetch.mem_raw, prefetch.cpu_raw
            else:
                mem_raw = await self.repo.report_memory_breakdown(server_id, period_days, end_dt)
                cpu_raw = await self.repo.report_cpu_breakdown(server_id, period_days, end_dt)
            summary.memory_breakdown = build_memory_breakdown(mem_raw)
            summary.cpu_breakdown = build_cpu_breakdown(cpu_raw)

            # 자원 적정성·포화·에러(U+S+E) — 서버 상세·자원 상세 탭과 동일 단일 진실(build_period_assessment,
            # query/server.py::get_period_assessment 와 동일 호출 패턴). latest_errors 는 get_report 내부에서도
            # 호출되지만(has_operational_event 용) 그 결과는 폐기되므로 N=1 이라 안전하게 한 번 더 조회한다.
            raw0 = raws_window[0]
            win_days = int(period_days)
            err = await self.repo.latest_errors(server_id, end_dt - timedelta(days=period_days))
            errors = build_error_signals(err, window_label=f"최근 {win_days}일", os_family=raw0.os_family)
            summary.period_assessment = build_period_assessment(
                build_resource_stats(raw0), errors, disk_worst_mount=raw0.disk_capacity_worst_mount,
                window_days=win_days,
            )

            # 스토리지 레이아웃 트리 — storage.html 과 동일 단일 진실(build_storage_tree). 현재 스냅샷 기준
            # (마운트별 세부 사용량·usage_pct 는 트리 리프 노드가 겸한다).
            storage_dto = await self.repo.get_storage(server_id)
            if storage_dto is not None:
                summary.storage_tree = build_storage_tree(
                    storage_dto.block_devices, storage_dto.lvm_vgs, storage_dto.filesystems
                )

            # 네트워크 인터페이스 정적 정보 — network.html 과 동일 단일 진실(build_network_interfaces).
            # 활동(RX/TX)은 이미 report 자체 윈도우 표(net_rx_kbps 등)가 있어 라이브 스냅샷 재주입 안 함.
            summary.network_interfaces = build_network_interfaces(raw0.net_interfaces or [])

            # 자원 포화 여부 3축 추이 — trend(이용률)와 동일 윈도우·bucket, 서버 1대 이진 0/1(#F10 창 일관).
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
        mem_by_id: dict[int, MemoryBreakdownRaw] = {}
        cpu_by_id: dict[int, CpuBreakdownRaw] = {}
        if view == "engineer":
            mem_by_id = await self.repo.report_memory_breakdown_batch(sids, period_days, anchor_at)
            cpu_by_id = await self.repo.report_cpu_breakdown_batch(sids, period_days, anchor_at)

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

    async def _build_report_trend(self, time_range: str, end_dt: datetime, server_ids: list[int] | None = None) -> list:
        """CPU·메모리·디스크 평균 시계열 추이 — 보고서 3경로 공유.

        server_ids=None 이면 전체 환경(env 보고서), 주어지면 선택 N대/1대 한정(selection·single). 버킷 정책 동일.
        """
        bucket = cast(BucketSize, AUTO_BUCKET.get(time_range, "1h"))
        trend_start = end_dt - TIME_RANGE_TD[time_range]
        cpu_series = await self.repo.metric_trend("cpu.usage_percent", trend_start, end_dt, bucket, server_ids)
        mem_series = await self.repo.metric_trend("mem.usage_percent", trend_start, end_dt, bucket, server_ids)
        disk_series = await self.repo.metric_trend("fs.usage_percent", trend_start, end_dt, bucket, server_ids)
        return build_metric_trend(cpu_series, mem_series, disk_series)

    async def _build_report_saturation_trend(self, time_range: str, end_dt: datetime, server_ids: list[int]) -> list:
        """CPU 실행 큐·메모리 페이징·디스크 I/O 포화 이진(0/1) 시계열 — 개별 서버 보고서(engineer) 전용.

        trend(이용률)와 동일 윈도우·bucket 정책. 서버 1대 스코프 고정(server_ids 필수) — 환경/선택 스코프는
        해당 화면에 노출 지점이 없어 미도입(실사용 시점 확장).
        """
        bucket = cast(BucketSize, AUTO_BUCKET.get(time_range, "1h"))
        trend_start = end_dt - TIME_RANGE_TD[time_range]
        cpu_series = await self.repo.metric_trend("cpu.saturation", trend_start, end_dt, bucket, server_ids)
        mem_series = await self.repo.metric_trend("mem.paging_pressure", trend_start, end_dt, bucket, server_ids)
        disk_series = await self.repo.metric_trend("disk.saturation", trend_start, end_dt, bucket, server_ids)
        return build_saturation_trend(cpu_series, mem_series, disk_series)

    async def _assemble_report_raws(self, server_ids: list[int], period_days: float, end_dt: datetime) -> list:
        """report_aggregate + 4 baseline(uptime·agent_restart·disk_io·net_io) 주입 raws.

        보고서 3경로(env·selection·single)가 get_report 세부 행·under_hosts 분류·overview 에 동일
        raws 를 공유 — report_aggregate/net_io 중복 호출 제거 + build_resource_stats(#E3) 입력 일치로
        유휴 분류 정합(세부 행·under·도넛 동일 입력). worst_mount used%·용량 임박(구동 마운트·runway)은
        report_aggregate 단일 산출 (별도 mount_worst 쿼리 폐기).
        """
        raws = await self.repo.report_aggregate(
            server_ids,
            period_days,
            end_dt,
        )
        uptime_stats = await self.repo.report_uptime_stats(
            server_ids,
            period_days,
            end_dt,
        )
        agent_restart_stats = await self.repo.report_agent_restart_stats(
            server_ids,
            period_days,
            end_dt,
        )
        disk_io = await self.repo.report_disk_io_baseline(
            server_ids,
            period_days,
            end_dt,
        )
        net_io = await self.repo.report_net_io_baseline(
            server_ids,
            period_days,
            end_dt,
        )
        for raw in raws:
            raw.reboot_count = uptime_stats.get(raw.server_id, 0)
            raw.agent_restart_count = agent_restart_stats.get(raw.server_id, 0)
            disk_bl = disk_io.get(raw.server_id)
            if disk_bl is not None:
                raw.disk_iops_baseline = disk_bl.iops_baseline
                raw.disk_throughput_kbps = disk_bl.throughput_kbps_baseline
                raw.disk_iops_p95 = disk_bl.iops_p95
                raw.disk_iops_peak = disk_bl.iops_peak
                raw.disk_throughput_kbps_p95 = disk_bl.kbps_p95
                raw.disk_throughput_kbps_peak = disk_bl.kbps_peak
            net_bl = net_io.get(raw.server_id)
            if net_bl is not None:
                raw.net_rx_kbps = net_bl.rx_kbps_baseline
                raw.net_tx_kbps = net_bl.tx_kbps_baseline
                raw.net_rx_kbps_p95 = net_bl.rx_p95
                raw.net_rx_kbps_peak = net_bl.rx_peak
                raw.net_tx_kbps_p95 = net_bl.tx_p95
                raw.net_tx_kbps_peak = net_bl.tx_peak
        return raws

    async def get_report(
        self,
        server_ids: list[int],
        period_days: float = recommendation.WINDOW_DAYS,
        end: datetime | None = None,
        view: ReportView = "customer",
        raws: list | None = None,
        fetch_operational_events: bool = False,
    ) -> ReportSummary:
        """Assessment 보고서 — raw → ViewModel + KPI 집계 (P2 단일 변환).

        repo는 raw stats(`ReportRowRaw`)만 산출. mapper(`to_report_row_item`)가 표시 파생
        (role/recommendation/badge_class/os_display) 채움. KPI도 service 책임.
        is_online은 Redis mget 일괄 (N+1 회피, fail-open 시 last_seen_at fallback).
        view는 summary_bullets 분기에만 사용 (양식 A/B로 행동 시그널 vs 엔지니어 시그널 분리).
        fetch_operational_events — True 면 서버별 latest_errors(보고서 window 기준)로 has_operational_event
        판정(세부 서버 목록 전용, get_selection_report 만 True). 기본 False — 환경 전체 보고서(server_ids
        최대 수백)까지 켜면 서버별 쿼리 N+1 이라 반드시 작은 N(선택 보고서)에만 명시적으로 켠다.
        """
        end_dt = end or datetime.now(UTC)
        # raws 주입 시 재사용(보고서 3경로가 under_hosts 분류·overview 와 공유 — report_aggregate/net_io
        # 중복 호출 제거), 없으면 자체 조립(단독 라우트·dashboard 호환).
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
                err = await self.repo.latest_errors(raw.server_id, window_start)
                has_event = bool(err.mce_count or err.oom_count or (err.corrupted_bytes or 0) > 0
                                 or err.net_error_count or err.disk_error_count)
            items.append(to_report_row_item(raw, online, end_dt, has_event))

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

    # inventory export 는 assessment 계약(/api/exports/inventory = assessment envelope 파일)으로 서비스.
    # 사이징/재현 데이터는 get_assessment 단일 진실.
