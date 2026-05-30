import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Literal

from redis.asyncio import Redis
from redis.exceptions import RedisError

from assessment_engine import recommendation
from assessment_engine.cache.redis import safe_get, safe_mget, safe_set
from assessment_engine.db.dtos.outbound import InventoryExportEntry, MetricSeries, RebootEvent
from assessment_engine.db.repositories.base_diagnostic_repository import (
    DIAGNOSTIC_RANGE_DAYS,
    DiagnosticTimeRange,
)
from assessment_engine.db.repositories.query.base_query_repository import BaseQueryRepository
from assessment_engine.db.repositories.query.types import (
    TIME_RANGE_TD,
    AggFunc,
    BucketSize,
    MetricType,
    TimeRange,
)
from assessment_engine.web.services.cache_serializer import (
    dashboard_from_json,
    dashboard_to_json,
    server_detail_from_json,
    server_detail_to_json,
)
from assessment_engine.web.services.device_filters import (
    is_lvm_disk,
    is_partition,
    is_physical_disk,
    is_virtual_interface,
    is_virtual_mount,
)
from assessment_engine.web.services.mappers.attention import (
    build_environment_overview,
    build_environment_realtime,
    to_agent_unstable_item,
    to_capacity_warning_item,
    to_gap_warning_item,
    to_os_eol_warning_item,
)
from assessment_engine.web.services.mappers.environment_report import to_environment_report
from assessment_engine.web.services.mappers.export import to_inventory_export_entry
from assessment_engine.web.services.mappers.metric import (
    to_collection_status_item,
    to_metric_series_item,
)
from assessment_engine.web.services.mappers.report import (
    build_report_summary_bullets,
    build_role_distribution,
    compute_report_avg_p95,
    compute_report_totals_from_raw,
    to_report_row_item,
)
from assessment_engine.web.services.mappers.server import (
    to_network_detail,
    to_server_detail,
    to_server_list_item,
    to_storage_detail,
)
from assessment_engine.web.services.mappers.shared import (
    _DONUT_SEGMENT_FROM_REC,
    ReportView,
)
from assessment_engine.web.services.mappers.task import (
    to_task_detail,
    to_task_summary,
)
from assessment_engine.web.services.metrics_calculator import build_dashboard
from assessment_engine.web.services.unit_converter import bytes_to_gb, kb_to_gb
from assessment_engine.web.settings import web_settings
from assessment_engine.web.view_models.attention import (
    AttentionRow,
    AttentionSignals,
    CapacityWarningItem,
    DashboardLive,
    EnvironmentOverview,
    EnvironmentRealtime,
)
from assessment_engine.web.view_models.environment_report import EnvironmentReportSummary
from assessment_engine.web.view_models.metric import (
    CollectionStatusItem,
    MetricDashboard,
    MetricSeriesItem,
)
from assessment_engine.web.view_models.report import ReportRowItem, ReportSummary
from assessment_engine.web.view_models.server import (
    NetworkDetailResponse,
    ServerDetailResponse,
    ServerListItem,
    StorageDetailResponse,
)
from assessment_engine.web.view_models.task import TaskDetailItem, TaskSummaryItem

_DISK_METRIC_TYPES = frozenset({"disk.read_iops", "disk.write_iops"})
_NET_METRIC_TYPES = frozenset(
    {"net.rx_bytes_per_sec", "net.tx_bytes_per_sec", "net.rx_packets_per_sec", "net.tx_packets_per_sec"}
)

# 운영신호(attention) 카탈로그 항목 한도 + gap 윈도우 — 단건 get_attention_signals 와 대시보드 묶음 공유.
_ATTENTION_LIMIT_EACH = 5
_GAP_MINUTES = 5
_GAP_RECENT_HOURS = 24
_SSE_PING_INTERVAL_SEC = 15  # SSE idle keep-alive ping 주기 — 메시지 없을 때 프록시·브라우저 idle 끊김 방지


def _empty_overview() -> EnvironmentOverview:
    """등록 서버 0대 — 빈 환경 요약 (단건 get_environment_overview · 대시보드 묶음 공유)."""
    return EnvironmentOverview(
        total=0,
        online=0,
        offline=0,
        total_vcpus=0,
        total_memory_gb=0.0,
        total_disk_gb=0,
        role_distribution={},
    )

# disk metric_type에서만 의미를 갖는 service 레벨 분기. 라우터에서 Literal로 검증 (F3 단일 경로).
DeviceCategory = Literal["phys", "logical"]


def _filter_disk_category(dtos: list[MetricSeries], category: DeviceCategory) -> list[MetricSeries]:
    if category == "phys":
        return [d for d in dtos if is_physical_disk(d.dimension)]
    # category == "logical"
    lvm = [d for d in dtos if is_lvm_disk(d.dimension)]
    return lvm if lvm else [d for d in dtos if is_partition(d.dimension)]


class QueryService:
    def __init__(self, repo: BaseQueryRepository, redis: Redis):
        self.repo = repo
        self.redis = redis

    async def resolve_server_id(self, public_id: str) -> int | None:
        cache_key = web_settings.redis_key_cache_resolve.format(public_id)
        cached = await safe_get(self.redis, cache_key)
        if cached:
            return int(cached)
        server_id = await self.repo.resolve_server_id(public_id)
        if server_id is not None:
            await safe_set(self.redis, cache_key, str(server_id))
        return server_id

    async def resolve_server_ids(self, public_ids: list[str]) -> dict[str, int]:
        """N개 public_id → {public_id: server_id} 단일 SQL (C5 N+1 회피).

        cache 활용 안 함 — batch 미스 시 DB는 어차피 단일 SQL이라 cache mget 복잡도 감수 대비 이득 미미.
        단건 path(`resolve_server_id`)는 cache 그대로.
        """
        if not public_ids:
            return {}
        return await self.repo.resolve_server_ids(public_ids)

    async def list_all_server_public_ids(self) -> list[str]:
        """전체 등록 서버 public_id — 환경 단위 보고서 URL 합성용."""
        return await self.repo.list_all_server_public_ids()

    async def _is_online(self, server_id: int) -> bool:
        flag = await safe_get(self.redis, web_settings.redis_key_online.format(server_id))
        return flag is not None

    async def list_servers(
        self,
        page: int,
        limit: int,
        search: str | None,
        is_online: bool | None,
        service: str | None = None,
        os_distro: str | None = None,
        classification: str | None = None,
    ) -> list[ServerListItem]:
        dtos = await self.repo.list_servers(page, limit, search)
        if not dtos:
            return []
        keys = [web_settings.redis_key_online.format(dto.id) for dto in dtos]
        online_flags = await safe_mget(self.redis, keys)

        # 14일 USE Method 분류 — 페이지 서버만 별도 SQL 1회. 보고서·right-sizing과 동일 윈도우.
        page_server_ids = [dto.id for dto in dtos]
        raws_period = await self.repo.report_aggregate(
            page_server_ids,
            period_days=recommendation.WINDOW_DAYS,
            end=datetime.now(UTC),
        )
        raws_by_id: dict[int, object] = {r.server_id: r for r in raws_period}

        # 페이지 서버별 마지막 task — 별도 SQL 1회 (DISTINCT ON). 빈 dict면 row 없는 서버.
        last_tasks = await self.latest_tasks_by_servers(page_server_ids)

        items: list[ServerListItem] = []
        if online_flags is None:
            # Redis 장애 fallback: last_seen_at 기반 판정 (TTL 임계와 동일)
            threshold = datetime.now(UTC) - timedelta(seconds=web_settings.redis_ttl_online)
            for dto in dtos:
                item = to_server_list_item(dto, raws_by_id.get(dto.id))
                item.is_online = dto.last_seen_at is not None and dto.last_seen_at > threshold
                item.last_task = last_tasks.get(dto.id)
                items.append(item)
        else:
            # dtos와 online_flags는 동일 길이 보장 — mget이 키 개수만큼 반환.
            for dto, flag in zip(dtos, online_flags, strict=True):
                item = to_server_list_item(dto, raws_by_id.get(dto.id))
                item.is_online = flag is not None
                item.last_task = last_tasks.get(dto.id)
                items.append(item)

        if is_online is not None:
            items = [i for i in items if i.is_online == is_online]
        if service:
            # service category 매칭 — "이 카테고리를 운영하는 호스트". known_services 에 카테고리 contains.
            items = [i for i in items if any(s.category == service for s in i.known_services)]
        if os_distro:
            items = [i for i in items if i.os_distro == os_distro]
        if classification:
            items = [i for i in items if i.provisioning_class == classification]
        # 1차 online(온라인 우선) · 2차 hostname ASC. online 판정은 Redis 기반이라 DB ORDER BY 불가 →
        # service 레이어 정렬(P2). repo 는 hostname ASC raw 반환(2차 기준 보존).
        items.sort(key=lambda i: (not i.is_online, i.hostname.lower()))
        return items

    async def get_server(self, server_id: int) -> ServerDetailResponse | None:
        cache_key = web_settings.redis_key_cache_inventory.format(server_id)
        cached = await safe_get(self.redis, cache_key)
        if cached:
            return server_detail_from_json(cached)

        dto = await self.repo.get_server(server_id)
        if not dto:
            return None
        result = to_server_detail(dto)
        await safe_set(self.redis, cache_key, server_detail_to_json(result), ex=300)
        return result

    async def get_storage(self, server_id: int) -> StorageDetailResponse | None:
        dto = await self.repo.get_storage(server_id)
        return to_storage_detail(dto) if dto else None

    async def get_network(self, server_id: int) -> NetworkDetailResponse | None:
        dto = await self.repo.get_network(server_id)
        return to_network_detail(dto) if dto else None

    async def get_collection_status(self, server_id: int) -> CollectionStatusItem | None:
        dto = await self.repo.get_collection_status(server_id)
        if dto is None:
            return None
        online = await self._is_online(server_id)
        return to_collection_status_item(dto, online)

    async def get_latest_metric(self, server_id: int) -> MetricDashboard | None:
        cache_key = web_settings.redis_key_cache_metrics.format(server_id)
        cached = await safe_get(self.redis, cache_key)
        if cached:
            return dashboard_from_json(cached)

        raw = await self.repo.latest_dashboard(server_id)
        if not raw or not raw.metrics:
            return None
        result = build_dashboard(raw)
        await safe_set(self.redis, cache_key, dashboard_to_json(result), ex=60)
        return result

    async def get_metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeriesItem]:
        dtos = await self.repo.metric_snapshots(server_id, cursor, limit)
        return [to_metric_series_item(dto) for dto in dtos]

    async def get_metric_chart(
        self,
        server_id: int,
        metric_type: MetricType,
        dimension: str | None,
        time_range: TimeRange,
        bucket: BucketSize,
        agg: AggFunc,
        end: datetime | None = None,
        device_category: DeviceCategory | None = None,
    ) -> list[MetricSeriesItem]:
        # 검증은 라우터의 Query(MetricType) Literal Pydantic 단계에서 이미 처리됨.
        dtos = await self.repo.metric_chart(server_id, metric_type, dimension, time_range, bucket, agg, end)
        if metric_type == "fs.usage_percent":
            dtos = [d for d in dtos if not is_virtual_mount(None, d.dimension)]
        if metric_type in _NET_METRIC_TYPES:
            dtos = [d for d in dtos if not is_virtual_interface(d.dimension)]
        if device_category is not None and metric_type in _DISK_METRIC_TYPES:
            dtos = _filter_disk_category(dtos, device_category)
        return [to_metric_series_item(dto) for dto in dtos]

    async def get_attention_signals(
        self,
        disk_threshold_pct: float = 85,
        gap_minutes: int = _GAP_MINUTES,
        gap_recent_hours: int = _GAP_RECENT_HOURS,
        limit_each: int = _ATTENTION_LIMIT_EACH,
        days_until_full_threshold: int = 30,
    ) -> AttentionSignals:
        """list 화면 운영 신호 카드 — USE Method 외 시스템 운영 이상 3 카탈로그.

        - gap_warnings: 5분+ 끊김 (24h 안 발생)
        - os_eol_warnings: OS EOL 임박/지남 (정적 매핑)
        - agent_unstable: 1h 윈도우 안 재시작 임계 초과

        디스크(capacity·IO)는 USE Method classify 통합 — 본 catalog 에서 제외 (중복 회피).
        조립은 _assemble_attention 단일 진실 (대시보드 묶음 get_dashboard_live 와 공유).
        """
        now = datetime.now(UTC)
        gap_raws = await self.repo.metric_gap_warnings(gap_minutes, gap_recent_hours, limit_each)
        server_ids = await self.repo.list_server_ids()
        raws_period = []
        restart_counts: dict[int, int] = {}
        if server_ids:
            raws_period = await self.repo.report_aggregate(server_ids, period_days=recommendation.WINDOW_DAYS, end=now)
            restart_counts = await self.repo.agent_restart_counts_recent(server_ids, now - timedelta(hours=1))
        return self._assemble_attention(raws_period, gap_raws, restart_counts, now, limit_each)

    async def get_environment_overview(self) -> "EnvironmentOverview":
        """list 화면 상단 환경 요약 — 총 N대·온라인/오프라인·자원 합계·역할 분포·평균 활용률·위험도 분포 (P2).

        흐름:
          list_server_ids -> get_servers (inventory)
                          + environment_utilization (14일 평균)
                          + report_aggregate(period_days=WINDOW_DAYS) (USE Method 분류)
                          -> mapper 집계.
        period_days는 보고서·right-sizing과 동일 윈도우 (AWS Compute Optimizer 표준).
        모든 등록 서버 대상 단일 SQL. 첫 페이지 호출에서만 사용.
        """
        now = datetime.now(UTC)
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return _empty_overview()
        details = await self.repo.get_servers(server_ids)
        util = await self.repo.environment_utilization(period_days=recommendation.WINDOW_DAYS)
        raws_period = await self.repo.report_aggregate(server_ids, period_days=recommendation.WINDOW_DAYS, end=now)
        online_by_id = await self._online_map(server_ids, details, now)
        return self._assemble_overview(details, util, raws_period, online_by_id)

    async def get_environment_realtime(self) -> "EnvironmentRealtime":
        """list 화면 '환경 실시간 메트릭' 카드 — 각 서버 최신 스냅샷(get_latest_metric, Redis cache 우선) 집계.

        현황 모니터링 용도(right-sizing 14일 통계와 별개).
        평균 도넛·자원별 부하상위: 현재 CPU/메모리/디스크(worst mount), online: Redis online:{id} TTL,
        last_collected_at: 환경 전체 최신 수집시각(신선도). 조립은 _assemble_realtime 단일 진실 (대시보드 묶음과 공유).
        """
        now = datetime.now(UTC)
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return build_environment_realtime(0, 0, [], None)
        details = await self.repo.get_servers(server_ids)
        online_by_id = await self._online_map(server_ids, details, now)
        return await self._assemble_realtime(server_ids, details, online_by_id, now)

    # ─── 대시보드 live 조립 (단건 + get_dashboard_live 묶음 공유) ────────────

    async def _online_map(self, server_ids: list[int], details: list, now: datetime) -> dict[int, bool]:
        """server_id -> online bool. Redis online flags(safe_mget) 우선, 장애(None) 시 last_seen_at fallback.

        flags 는 online_keys(server_ids 순서) 대응. get_servers 는 순서 비보존이라 dict 매칭으로 순서 의존 제거.
        """
        online_keys = [web_settings.redis_key_online.format(sid) for sid in server_ids]
        flags = await safe_mget(self.redis, online_keys)
        threshold = now - timedelta(seconds=web_settings.redis_ttl_online)
        if flags is None:
            return {d.id: bool(d.last_seen_at and d.last_seen_at > threshold) for d in details}
        return {sid: (flags[i] is not None) for i, sid in enumerate(server_ids)}

    def _assemble_overview(self, details, util, raws_period, online_by_id: dict[int, bool]) -> EnvironmentOverview:
        """report_aggregate raws -> USE Method 분류 도넛 + under_provisioned 상세, online_by_id -> online_count."""
        risk_counts: dict[str, int] = {}
        under_hosts: list[CapacityWarningItem] = []
        for raw in raws_period:
            rec = recommendation.classify(
                recommendation.ResourceStats(
                    cpu_p95_pct=raw.cpu_p95_pct,
                    cpu_peak_pct=raw.cpu_peak_pct,
                    cpu_load_15m_max=raw.load_15m_max,
                    cpu_cores=raw.cpu_cores,
                    mem_p95_pct=raw.mem_p95_pct,
                    swap_used=raw.swap_used,
                    disk_used_pct=raw.worst_mount_used_pct,
                    iowait_p95_pct=raw.iowait_p95_pct,
                    net_avg_kbps=None,
                    os_family=raw.os_family,  # P2 — Windows swap 축 제외
                )
            )
            seg = _DONUT_SEGMENT_FROM_REC.get(rec, "insufficient_data")
            risk_counts[seg] = risk_counts.get(seg, 0) + 1
            if rec == "under_provisioned":
                under_hosts.append(to_capacity_warning_item(raw))
        online_count = sum(1 for d in details if online_by_id.get(d.id))
        return build_environment_overview(details, online_count, util, risk_counts, under_hosts)

    def _assemble_attention(self, raws_period, gap_raws, restart_counts, now, limit_each) -> AttentionSignals:
        """gap/os_eol/agent_unstable 3 카탈로그 조립 — raws_period(report_aggregate) 재사용.

        agent_unstable: 1h 윈도우 재시작 임계 초과(server_inventory_history agent_started_at DISTINCT-1).
        """
        os_eol_warnings: list[AttentionRow] = []
        for raw in raws_period:
            eol = to_os_eol_warning_item(raw, now)
            if eol and len(os_eol_warnings) < limit_each:
                os_eol_warnings.append(eol)
        agent_unstable: list[AttentionRow] = []
        raws_by_id = {r.server_id: r for r in raws_period}
        threshold_n = web_settings.agent_restart_alert_threshold
        for sid, count in restart_counts.items():
            if count >= threshold_n:
                raw = raws_by_id.get(sid)
                if raw and len(agent_unstable) < limit_each:
                    agent_unstable.append(to_agent_unstable_item(raw.public_id, raw.hostname, count))
        return AttentionSignals(
            gap_warnings=[to_gap_warning_item(r, now) for r in gap_raws],
            os_eol_warnings=os_eol_warnings,
            agent_unstable=agent_unstable,
        )

    async def _assemble_realtime(self, server_ids, details, online_by_id: dict[int, bool], now) -> EnvironmentRealtime:
        """각 서버 최신 스냅샷(get_latest_metric) 집계 — online_by_id 로 온라인 판정 공유.

        평균·hotspot 표본은 온라인 서버만 (오프라인 stale 메트릭이 현황 평균 왜곡 방지) — sample_size/total 표기.
        """
        detail_by_id = {d.id: d for d in details}
        online = 0
        snapshots: list[dict] = []
        last_collected = None
        for sid in server_ids:
            d = detail_by_id.get(sid)
            if d is None:
                continue
            is_on = online_by_id.get(sid, False)
            if is_on:
                online += 1
            m = await self.get_latest_metric(sid)
            if not m or not m.collected_at:
                continue
            if not is_on:
                continue
            disk = max((mt.usage_pct for mt in m.mounts if mt.usage_pct is not None), default=None)
            snapshots.append(
                {
                    "hostname": d.hostname,
                    "public_id": d.public_id,
                    "cpu_pct": m.cpu.usage_pct if m.cpu else None,
                    "mem_pct": m.memory.usage_pct if m.memory else None,
                    "disk_pct": disk,
                }
            )
            if last_collected is None or m.collected_at > last_collected:
                last_collected = m.collected_at
        return build_environment_realtime(len(server_ids), online, snapshots, last_collected)

    async def get_dashboard_live(self) -> DashboardLive:
        """fragment=live·page1 공용 — 공유 기초 데이터 1회 조회 후 3 ViewModel 조립.

        단건 3 메서드를 각각 호출하면 list_server_ids 3회·report_aggregate 2회·get_servers 2회·online flags 2회
        중복. 본 메서드는 공유분(server_ids·details·raws_period·online_by_id)을 1회만 조회해 _assemble_* 에 주입.
        세 카드가 동일 now·스냅샷 기준이라 카드 간 값 일관(비결정성 해소).
        """
        now = datetime.now(UTC)
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return DashboardLive(
                overview=_empty_overview(),
                attention=AttentionSignals(gap_warnings=[], os_eol_warnings=[], agent_unstable=[]),
                realtime=build_environment_realtime(0, 0, [], None),
            )
        details = await self.repo.get_servers(server_ids)
        raws_period = await self.repo.report_aggregate(server_ids, period_days=recommendation.WINDOW_DAYS, end=now)
        util = await self.repo.environment_utilization(period_days=recommendation.WINDOW_DAYS)
        online_by_id = await self._online_map(server_ids, details, now)
        gap_raws = await self.repo.metric_gap_warnings(_GAP_MINUTES, _GAP_RECENT_HOURS, _ATTENTION_LIMIT_EACH)
        restart_counts = await self.repo.agent_restart_counts_recent(server_ids, now - timedelta(hours=1))
        return DashboardLive(
            overview=self._assemble_overview(details, util, raws_period, online_by_id),
            attention=self._assemble_attention(raws_period, gap_raws, restart_counts, now, _ATTENTION_LIMIT_EACH),
            realtime=await self._assemble_realtime(server_ids, details, online_by_id, now),
        )

    async def get_environment_report(
        self,
        time_range: DiagnosticTimeRange = "14d",
        anchor_at: datetime | None = None,
        view: ReportView = "customer",
    ) -> EnvironmentReportSummary:
        """환경 단위 보고서 (전체 등록 서버 대상) — server scope 양식과 별도 high-level.

        time_range: AI 진단과 동일 7개 윈도우 (15m/1h/6h/24h/7d/14d/30d) — DIAGNOSTIC_RANGE_DAYS.
        anchor_at: 보고서 기준 시각 (None 이면 현재 시각). period_days = window days.
        구성: overview (KPI/utilization) + attention (6 카탈로그) + base ReportSummary (분류·KPI)
            + classification_dist 도넛 + os_distribution + top_risks + view 별 summary_bullets_env.
        """
        period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
        end_dt = anchor_at if anchor_at is not None else datetime.now(UTC)

        overview = await self.get_environment_overview()
        attention = await self.get_attention_signals()

        public_ids = await self.repo.list_all_server_public_ids()
        sid_map = await self.repo.resolve_server_ids(public_ids) if public_ids else {}
        server_ids = [sid_map[p] for p in public_ids if p in sid_map]

        under_hosts: list[CapacityWarningItem] = []
        if server_ids:
            base = await self.get_report(server_ids, period_days, end=end_dt, view=view)
            details = await self.repo.get_servers(server_ids)
            # under_provisioned 호스트 추출 — time_range 윈도우 정확 정합 (overview 14일 고정 의존 회피).
            raws_window = await self.repo.report_aggregate(server_ids, period_days, end_dt)
            for raw in raws_window:
                rec = recommendation.classify(
                    recommendation.ResourceStats(
                        cpu_p95_pct=raw.cpu_p95_pct,
                        cpu_peak_pct=raw.cpu_peak_pct,
                        cpu_load_15m_max=raw.load_15m_max,
                        cpu_cores=raw.cpu_cores,
                        mem_p95_pct=raw.mem_p95_pct,
                        swap_used=raw.swap_used,
                        disk_used_pct=raw.worst_mount_used_pct,
                        iowait_p95_pct=raw.iowait_p95_pct,
                        net_avg_kbps=None,
                        os_family=raw.os_family,  # P2 — Windows swap 축 제외
                    )
                )
                if rec == "under_provisioned":
                    under_hosts.append(to_capacity_warning_item(raw))
        else:
            base = ReportSummary(
                rows=[],
                period_days=int(period_days),
                total=0,
                online=0,
                risk_attention=0,
                risk_high=0,
            )
            details = []

        return to_environment_report(
            view=view,
            time_range=time_range,
            anchor_at=end_dt,
            overview=overview,
            attention=attention,
            base=base,
            details=details,
            generated_at=datetime.now(UTC),
            under_provisioned_hosts=under_hosts,
        )

    async def get_single_server_report(
        self,
        server_public_id: str,
        period_days: float = 14,
        view: ReportView = "customer",
        time_range: str = "14d",
        anchor_at: datetime | None = None,
    ) -> "EnvironmentReportSummary":
        """단일 서버 보고서 — 환경 보고서 양식 (`get_environment_report`) 의 1대 scope 변형.

        발행(POST /servers/report/emit, ids 1개) 시 스냅샷 합성 + 이력 1대 row link 진입.
        환경 보고서와 동일 양식 (overview·attention·rows·top_risks).
        anchor_at: 발행 시점 기준 시각 (None 이면 현재) — worker narrative 와 같은 윈도우 재현.
        """
        end_dt = anchor_at if anchor_at is not None else datetime.now(UTC)
        sid_map = await self.repo.resolve_server_ids([server_public_id])
        if server_public_id not in sid_map:
            return None  # type: ignore[return-value]
        server_id = sid_map[server_public_id]

        details = await self.repo.get_servers([server_id])
        detail = details[0] if details else None
        if detail is None:
            return None  # type: ignore[return-value]

        # 1대 한정 합성 — 환경 양식과 동일 흐름.
        base = await self.get_report([server_id], period_days, end=end_dt, view=view)
        attention = await self.get_attention_signals()

        raws_window = await self.repo.report_aggregate([server_id], period_days, end_dt)
        under_hosts: list[CapacityWarningItem] = []
        for raw in raws_window:
            rec = recommendation.classify(
                recommendation.ResourceStats(
                    cpu_p95_pct=raw.cpu_p95_pct,
                    cpu_peak_pct=raw.cpu_peak_pct,
                    cpu_load_15m_max=raw.load_15m_max,
                    cpu_cores=raw.cpu_cores,
                    mem_p95_pct=raw.mem_p95_pct,
                    swap_used=raw.swap_used,
                    disk_used_pct=raw.worst_mount_used_pct,
                    iowait_p95_pct=raw.iowait_p95_pct,
                    net_avg_kbps=None,
                    os_family=raw.os_family,  # P2 — Windows swap 축 제외
                )
            )
            if rec == "under_provisioned":
                under_hosts.append(to_capacity_warning_item(raw))

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

        return to_environment_report(
            view=view,
            time_range=time_range,
            anchor_at=end_dt,
            overview=overview,
            attention=attention,
            base=base,
            details=details,
            generated_at=datetime.now(UTC),
            under_provisioned_hosts=under_hosts,
        )

    async def get_report(
        self,
        server_ids: list[int],
        period_days: float = 14,
        end: datetime | None = None,
        view: ReportView = "customer",
    ) -> ReportSummary:
        """Assessment 보고서 — raw → ViewModel + KPI 집계 (P2 단일 변환).

        repo는 raw stats(`ReportRowRaw`)만 산출. mapper(`to_report_row_item`)가 표시 파생
        (role/recommendation/badge_class/os_display) 채움. KPI도 service 책임.
        is_online은 Redis mget 일괄 (N+1 회피, fail-open 시 last_seen_at fallback).
        view는 summary_bullets 분기에만 사용 (양식 A/B로 행동 시그널 vs 엔지니어 시그널 분리).
        """
        end_dt = end or datetime.now(UTC)
        # 5개 SQL 단일 round-trip씩. 결과 dict는 server_id 키로 zip.
        raws = await self.repo.report_aggregate(server_ids, period_days, end_dt)
        mount_worst = await self.repo.report_mount_worst(server_ids, period_days, end_dt)
        uptime_stats = await self.repo.report_uptime_stats(server_ids, period_days, end_dt)
        agent_restart_stats = await self.repo.report_agent_restart_stats(server_ids, period_days, end_dt)
        disk_io = await self.repo.report_disk_io_baseline(server_ids, period_days, end_dt)
        net_io = await self.repo.report_net_io_baseline(server_ids, period_days, end_dt)

        # raws에 결과 주입 (P1 raw 단계 합성)
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

        online_keys = [web_settings.redis_key_online.format(r.server_id) for r in raws]
        flags = await safe_mget(self.redis, online_keys)
        threshold = end_dt - timedelta(seconds=web_settings.redis_ttl_online)

        items: list[ReportRowItem] = []
        for i, raw in enumerate(raws):
            online = bool(raw.last_seen_at and raw.last_seen_at > threshold) if flags is None else flags[i] is not None
            items.append(to_report_row_item(raw, online, end_dt))

        avg_cpu, avg_mem = compute_report_avg_p95(items)

        return ReportSummary(
            rows=items,
            period_days=period_days,
            total=len(items),
            online=sum(1 for it in items if it.is_online),
            risk_attention=sum(1 for it in items if it.risk_level == "attention"),
            risk_high=sum(1 for it in items if it.risk_level == "high"),
            avg_cpu_p95_pct=avg_cpu,
            avg_mem_p95_pct=avg_mem,
            totals=compute_report_totals_from_raw(raws),
            summary_bullets=build_report_summary_bullets(items, raws, view=view),
            role_distribution=build_role_distribution(raws),
            anchor_at=end_dt,
            generated_at=datetime.now(UTC),
        )

    async def get_inventory_export(
        self,
        server_ids: list[int],
        period_days: int = 7,
    ) -> list[InventoryExportEntry]:
        """선택 서버 N대의 정제 inventory JSON 항목 list.

        Right-sizing stats(cpu_p95/peak·mem_p95/peak·load·swap)도 같이 fetch하여 export에 포함.
        USE Method 보고서 SQL(`report_aggregate`) 재사용 — period_days 윈도우 통계.

        각 서버는 ServerDetail + ReportRowRaw -> mapper로 변환. 누락된 server_id는 silent skip.

        C5: `get_servers` + `report_aggregate` 단일 SQL 각 1회 — 입력 server_ids 순서 보존.
        스키마·정제 원칙·사용처: docs/architecture/inventory-export.md.
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

    async def get_reboot_events(
        self,
        server_id: int,
        time_range: TimeRange,
        end: datetime | None = None,
    ) -> list[RebootEvent]:
        """차트 vertical marker용 — 지정 time_range 내 시스템 재부팅·에이전트 재시작 시점.

        outbound DTO 그대로 반환 (raw 그대로 — P1). 별도 ViewModel 변환 없음 — 파생 필드
        없고 datetime / Literal kind 그대로 JSON 직렬화 가능 (cache_serializer._json_default).
        """
        end_dt = end or datetime.now(UTC)
        start = end_dt - TIME_RANGE_TD[time_range]
        return await self.repo.reboot_events(server_id, start, end_dt)

    async def stream_metrics_events(self, server_id: int) -> AsyncIterator[str]:
        """SSE 라인 스트림 — 본 server_id 메트릭 이벤트 + idle keep-alive ping (라우터는 그대로 통과).

        get_message timeout 으로 _SSE_PING_INTERVAL_SEC 마다 메시지 없으면 comment ping(`: keep-alive`) 전송 —
        메시지 없는 idle 구간에 프록시·브라우저가 연결을 끊지 않게 함.
        """
        async with self.redis.pubsub() as pubsub:
            await pubsub.subscribe(web_settings.redis_channel_metrics)
            try:
                while True:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=_SSE_PING_INTERVAL_SEC
                    )
                    if message is None:
                        yield ": keep-alive\n\n"
                        continue
                    if message["type"] != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                    except (ValueError, TypeError):
                        continue
                    if payload.get("server_id") == server_id:
                        yield f"data: {message['data']}\n\n"
            except RedisError:
                pass

    # ---------- Task 조회 ----------

    async def get_task(self, task_id: str) -> TaskDetailItem | None:
        row = await self.repo.get_task_by_public_id(task_id)
        return to_task_detail(row) if row else None

    async def list_recent_tasks(
        self,
        server_public_id: str,
        limit: int,
        cursor: datetime | None,
    ) -> list[TaskSummaryItem]:
        sid = await self.repo.resolve_server_id(server_public_id)
        if sid is None:
            return []
        rows = await self.repo.list_recent_tasks(sid, limit, cursor)
        return [to_task_summary(r) for r in rows]

    async def latest_tasks_by_servers(
        self,
        server_ids: list[int],
    ) -> dict[int, TaskSummaryItem]:
        rows = await self.repo.latest_tasks_by_servers(server_ids)
        return {sid: to_task_summary(r) for sid, r in rows.items()}
