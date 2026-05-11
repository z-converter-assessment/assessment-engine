import json
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Literal

from redis.asyncio import Redis
from redis.exceptions import RedisError

from assessment_engine.config import web_settings
from assessment_engine.db.redis import safe_get, safe_mget, safe_set
from assessment_engine.db.repositories.base_query_repository import (
    TIME_RANGE_TD,
    AggFunc,
    BaseQueryRepository,
    BucketSize,
    MetricType,
    TimeRange,
)
from assessment_engine.db.repositories.outbound import InventoryExportEntry, RebootEvent
from assessment_engine.web.services.device_filters import is_lvm_disk, is_partition, is_physical_disk, is_virtual_mount

# disk metric_type에서만 의미를 갖는 service 레벨 분기. 라우터에서 Literal로 검증 (F3 단일 경로).
DeviceCategory = Literal["phys", "logical"]

from assessment_engine.web.services.cache_serializer import (
    dashboard_from_json,
    dashboard_to_json,
    server_detail_from_json,
    server_detail_to_json,
)
from assessment_engine.web.services import recommendation
from assessment_engine.web.services.mappers import (
    _DONUT_SEGMENT_FROM_REC,
    build_capacity_breakdown,
    build_environment_overview,
    build_report_summary_bullets,
    build_role_distribution,
    compute_report_totals_from_raw,
    to_agent_unstable_item,
    to_capacity_warning_item,
    to_collection_status_item,
    to_disk_days_warning_item,
    to_disk_warning_item,
    to_gap_warning_item,
    to_inventory_export_entry,
    to_metric_series_item,
    to_network_detail,
    to_os_eol_warning_item,
    to_report_row_item,
    to_server_detail,
    to_server_list_item,
    to_storage_detail,
)
from assessment_engine.web.services.metrics_calculator import build_dashboard
from assessment_engine.web.view_models import (
    AgentUnstableItem,
    AttentionSignals,
    CapacityWarningItem,
    CollectionStatusItem,
    DiskDaysWarningItem,
    EnvironmentOverview,
    MetricDashboard,
    MetricSeriesItem,
    NetworkDetailResponse,
    OSEolWarningItem,
    ReportRowItem,
    ReportSummary,
    ServerDetailResponse,
    ServerListItem,
    StorageDetailResponse,
)

_DISK_METRIC_TYPES = frozenset({"disk.read_iops", "disk.write_iops"})


def _filter_disk_category(dtos: list, category: DeviceCategory) -> list:
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

    async def _is_online(self, server_id: int) -> bool:
        flag = await safe_get(self.redis, web_settings.redis_key_online.format(server_id))
        return flag is not None

    async def list_servers(
        self,
        page: int,
        limit: int,
        search: str | None,
        is_online: bool | None,
    ) -> list[ServerListItem]:
        dtos = await self.repo.list_servers(page, limit, search)
        if not dtos:
            return []
        keys = [web_settings.redis_key_online.format(dto.id) for dto in dtos]
        online_flags = await safe_mget(self.redis, keys)

        # 14일 USE Method 분류 — 페이지 서버만 별도 SQL 1회. 보고서·right-sizing과 동일 윈도우.
        page_server_ids = [dto.id for dto in dtos]
        raws_period = await self.repo.report_aggregate(
            page_server_ids, period_days=recommendation.WINDOW_DAYS, end=datetime.now(timezone.utc),
        )
        raws_by_id: dict[int, object] = {r.server_id: r for r in raws_period}

        items: list[ServerListItem] = []
        if online_flags is None:
            # Redis 장애 fallback: last_seen_at 기반 판정 (TTL 임계와 동일)
            threshold = datetime.now(timezone.utc) - timedelta(seconds=web_settings.redis_ttl_online)
            for dto in dtos:
                item = to_server_list_item(dto, raws_by_id.get(dto.id))
                item.is_online = dto.last_seen_at is not None and dto.last_seen_at > threshold
                items.append(item)
        else:
            for dto, flag in zip(dtos, online_flags):
                item = to_server_list_item(dto, raws_by_id.get(dto.id))
                item.is_online = flag is not None
                items.append(item)

        if is_online is not None:
            items = [i for i in items if i.is_online == is_online]
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
        if device_category is not None and metric_type in _DISK_METRIC_TYPES:
            dtos = _filter_disk_category(dtos, device_category)
        return [to_metric_series_item(dto) for dto in dtos]

    async def get_attention_signals(
        self,
        disk_threshold_pct: float = 85,
        gap_minutes: int = 5,
        gap_recent_hours: int = 24,
        limit_each: int = 5,
        days_until_full_threshold: int = 30,
    ) -> AttentionSignals:
        """list 화면 통합 신호 카드 — 현재 시점 + 평가 기간 신호 6 카탈로그.

        - disk_warnings: 현재 사용률 임박 mount (>=85%)
        - gap_warnings: 5분+ 끊김 (24h 안 발생)
        - capacity_warnings: 24h 평균 자원 부족 의심 (under_provisioned 분류)
        - days_until_full_warnings: 디스크 잔여 30일 안 (fill_rate 추정)
        - os_eol_warnings: OS EOL 임박/지남 (정적 매핑)
        - agent_unstable: 1h 윈도우 안 재시작 임계 초과
        한 화면 진입에 6개 신호 SQL — limit_each로 표시 부담 제어. has_any로 빈 카드 조건 분기.
        """
        disk_raws = await self.repo.disk_usage_warnings(disk_threshold_pct, limit_each)
        gap_raws = await self.repo.metric_gap_warnings(gap_minutes, gap_recent_hours, limit_each)
        now = datetime.now(timezone.utc)

        # 평가 기간 신호 — 14일 period 전체 서버 대상 (보고서·right-sizing 윈도우와 동일)
        server_ids = await self.repo.list_server_ids()
        capacity_warnings: list[CapacityWarningItem] = []
        days_warnings: list[DiskDaysWarningItem] = []
        os_eol_warnings: list[OSEolWarningItem] = []
        raws_period = []
        if server_ids:
            raws_period = await self.repo.report_aggregate(server_ids, period_days=recommendation.WINDOW_DAYS, end=now)
            mount_worst = await self.repo.report_mount_worst(server_ids, period_days=recommendation.WINDOW_DAYS, end=now)
            for raw in raws_period:
                rec = recommendation.classify(recommendation.ResourceStats(
                    cpu_p95_pct=raw.cpu_p95_pct, cpu_peak_pct=raw.cpu_peak_pct,
                    mem_p95_pct=raw.mem_p95_pct, swap_used=raw.swap_used, net_avg_kbps=None,
                ))
                if rec == "under_provisioned" and len(capacity_warnings) < limit_each:
                    capacity_warnings.append(to_capacity_warning_item(raw))
                eol = to_os_eol_warning_item(raw)
                if eol and len(os_eol_warnings) < limit_each:
                    os_eol_warnings.append(eol)
                mount_tuple = mount_worst.get(raw.server_id)
                if mount_tuple:
                    mount, used_pct, days = mount_tuple
                    if days is not None and days <= days_until_full_threshold and len(days_warnings) < limit_each:
                        days_warnings.append(to_disk_days_warning_item(
                            raw.public_id, raw.hostname, mount, days, used_pct,
                        ))

        # Agent 재시작 빈번 — Redis 1h 카운터 mget
        agent_unstable: list[AgentUnstableItem] = []
        if server_ids:
            restart_keys = [
                web_settings.redis_key_agent_restarts.format(sid) for sid in server_ids
            ]
            counts = await safe_mget(self.redis, restart_keys)
            if counts is not None:
                threshold_n = web_settings.agent_restart_alert_threshold
                # raws_period에 hostname·public_id 있어 zip 가능
                raws_by_id = {r.server_id: r for r in raws_period}
                for sid, count_str in zip(server_ids, counts):
                    if count_str is None:
                        continue
                    try:
                        count = int(count_str)
                    except ValueError:
                        continue
                    if count >= threshold_n:
                        raw = raws_by_id.get(sid)
                        if raw and len(agent_unstable) < limit_each:
                            agent_unstable.append(to_agent_unstable_item(
                                raw.public_id, raw.hostname, count,
                            ))

        return AttentionSignals(
            disk_warnings=[to_disk_warning_item(r) for r in disk_raws],
            gap_warnings=[to_gap_warning_item(r, now) for r in gap_raws],
            capacity_warnings=capacity_warnings,
            capacity_breakdown=build_capacity_breakdown(capacity_warnings),
            days_until_full_warnings=days_warnings,
            os_eol_warnings=os_eol_warnings,
            agent_unstable=agent_unstable,
        )

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
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return EnvironmentOverview(
                total=0, online=0, offline=0,
                total_vcpus=0, total_memory_gb=0.0, total_disk_gb=0,
                role_distribution={},
            )
        details = await self.repo.get_servers(server_ids)
        util = await self.repo.environment_utilization(period_days=recommendation.WINDOW_DAYS)

        # 프로비저닝 분포 — 14일 윈도우 USE Method 분류 후 도넛 3 카테고리 카운트
        now = datetime.now(timezone.utc)
        risk_counts: dict[str, int] = {"under": 0, "over": 0, "normal": 0}
        raws_period = await self.repo.report_aggregate(server_ids, period_days=recommendation.WINDOW_DAYS, end=now)
        for raw in raws_period:
            rec = recommendation.classify(recommendation.ResourceStats(
                cpu_p95_pct=raw.cpu_p95_pct, cpu_peak_pct=raw.cpu_peak_pct,
                mem_p95_pct=raw.mem_p95_pct, swap_used=raw.swap_used, net_avg_kbps=None,
            ))
            seg = _DONUT_SEGMENT_FROM_REC.get(rec, "normal")
            risk_counts[seg] = risk_counts.get(seg, 0) + 1

        online_keys = [web_settings.redis_key_online.format(d.id) for d in details]
        flags = await safe_mget(self.redis, online_keys)
        threshold = datetime.now(timezone.utc) - timedelta(seconds=web_settings.redis_ttl_online)
        online_count = 0
        for i, d in enumerate(details):
            if flags is None:
                online_count += int(bool(d.last_seen_at and d.last_seen_at > threshold))
            else:
                online_count += int(flags[i] is not None)
        return build_environment_overview(details, online_count, util, risk_counts)

    async def get_report(
        self,
        server_ids: list[int],
        period_days: int = 14,
        end: datetime | None = None,
    ) -> ReportSummary:
        """Assessment 보고서 — raw → ViewModel + KPI 집계 (P2 단일 변환).

        repo는 raw stats(`ReportRowRaw`)만 산출. mapper(`to_report_row_item`)가 표시 파생
        (role/recommendation/badge_class/os_display) 채움. KPI도 service 책임.
        is_online은 Redis mget 일괄 (N+1 회피, fail-open 시 last_seen_at fallback).
        """
        end_dt = end or datetime.now(timezone.utc)
        # 5개 SQL 단일 round-trip씩. 결과 dict는 server_id 키로 zip.
        raws = await self.repo.report_aggregate(server_ids, period_days, end_dt)
        mount_worst = await self.repo.report_mount_worst(server_ids, period_days, end_dt)
        uptime_stats = await self.repo.report_uptime_stats(server_ids, period_days, end_dt)
        disk_io = await self.repo.report_disk_io_baseline(server_ids, period_days, end_dt)
        net_io = await self.repo.report_net_io_baseline(server_ids, period_days, end_dt)

        # raws에 결과 주입 (P1 raw 단계 합성)
        for raw in raws:
            mount_tuple = mount_worst.get(raw.server_id)
            if mount_tuple is not None:
                raw.worst_mount, raw.worst_mount_used_pct, raw.worst_mount_days_until_full = mount_tuple
            raw.reboot_count = uptime_stats.get(raw.server_id, 0)
            disk_tuple = disk_io.get(raw.server_id)
            if disk_tuple is not None:
                (raw.disk_iops_baseline, raw.disk_throughput_kbps,
                 raw.disk_iops_p95, raw.disk_iops_peak,
                 raw.disk_throughput_kbps_p95, raw.disk_throughput_kbps_peak) = disk_tuple
            net_tuple = net_io.get(raw.server_id)
            if net_tuple is not None:
                (raw.net_rx_kbps, raw.net_tx_kbps,
                 raw.net_rx_kbps_p95, raw.net_rx_kbps_peak,
                 raw.net_tx_kbps_p95, raw.net_tx_kbps_peak) = net_tuple

        online_keys = [web_settings.redis_key_online.format(r.server_id) for r in raws]
        flags = await safe_mget(self.redis, online_keys)
        threshold = end_dt - timedelta(seconds=web_settings.redis_ttl_online)

        items: list[ReportRowItem] = []
        for i, raw in enumerate(raws):
            if flags is None:
                online = bool(raw.last_seen_at and raw.last_seen_at > threshold)
            else:
                online = flags[i] is not None
            items.append(to_report_row_item(raw, online, end_dt))

        return ReportSummary(
            rows=items,
            period_days=period_days,
            total=len(items),
            online=sum(1 for it in items if it.is_online),
            risk_attention=sum(1 for it in items if it.risk_level == "attention"),
            risk_high=sum(1 for it in items if it.risk_level == "high"),
            totals=compute_report_totals_from_raw(raws),
            summary_bullets=build_report_summary_bullets(items, raws),
            role_distribution=build_role_distribution(raws),
        )

    async def get_inventory_export(
        self,
        server_ids: list[int],
        period_days: int = 7,
    ) -> list[InventoryExportEntry]:
        """선택 서버 N대의 정제 inventory JSON 항목 list (v2).

        Right-sizing stats(cpu_p95/peak·mem_p95/peak·load·swap)도 같이 fetch하여 export에 포함.
        USE Method 보고서 SQL(`report_aggregate`) 재사용 — period_days 윈도우 통계.

        각 서버는 ServerDetail + ReportRowRaw -> mapper로 변환. 누락된 server_id는 silent skip
        (운영자가 발행 시점에 삭제했을 가능성). stats 누락은 신규 서버라 null로 발행.

        C5: `get_servers` + `report_aggregate` 단일 SQL 각 1회 — 입력 server_ids 순서 보존.
        스키마·정제 원칙·사용처: docs/architecture/inventory-export.md.
        """
        end_dt = datetime.now(timezone.utc)
        details = await self.repo.get_servers(server_ids)
        stats_rows = await self.repo.report_aggregate(server_ids, period_days, end_dt)
        disk_io = await self.repo.report_disk_io_baseline(server_ids, period_days, end_dt)
        net_io = await self.repo.report_net_io_baseline(server_ids, period_days, end_dt)

        # stats에 disk_io·net_io baseline + p95/peak 주입 (inventory-export v3 확장)
        for row in stats_rows:
            disk_tuple = disk_io.get(row.server_id)
            if disk_tuple is not None:
                (row.disk_iops_baseline, row.disk_throughput_kbps,
                 row.disk_iops_p95, row.disk_iops_peak,
                 row.disk_throughput_kbps_p95, row.disk_throughput_kbps_peak) = disk_tuple
            net_tuple = net_io.get(row.server_id)
            if net_tuple is not None:
                (row.net_rx_kbps, row.net_tx_kbps,
                 row.net_rx_kbps_p95, row.net_rx_kbps_peak,
                 row.net_tx_kbps_p95, row.net_tx_kbps_peak) = net_tuple

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
        end_dt = end or datetime.now(timezone.utc)
        start = end_dt - TIME_RANGE_TD[time_range]
        return await self.repo.reboot_events(server_id, start, end_dt)

    async def stream_metrics_events(self, server_id: int) -> AsyncIterator[str]:
        async with self.redis.pubsub() as pubsub:
            await pubsub.subscribe(web_settings.redis_channel_metrics)
            try:
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                    except (ValueError, TypeError):
                        continue
                    if payload.get("server_id") == server_id:
                        yield message["data"]
            except RedisError:
                pass