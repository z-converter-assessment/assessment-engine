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
from assessment_engine.web.services.mappers import (
    to_collection_status_item,
    to_disk_warning_item,
    to_gap_warning_item,
    to_inventory_export_entry,
    to_metric_series_item,
    to_network_detail,
    to_report_row_item,
    to_risk_server_item,
    to_server_detail,
    to_server_list_item,
    to_storage_detail,
)
from assessment_engine.web.services.metrics_calculator import build_dashboard
from assessment_engine.web.view_models import (
    AttentionSignals,
    CollectionStatusItem,
    MetricDashboard,
    MetricSeriesItem,
    NetworkDetailResponse,
    ReportRowItem,
    ReportSummary,
    RiskServerItem,
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

        items: list[ServerListItem] = []
        if online_flags is None:
            # Redis 장애 fallback: last_seen_at 기반 판정 (TTL 임계와 동일)
            threshold = datetime.now(timezone.utc) - timedelta(seconds=web_settings.redis_ttl_online)
            for dto in dtos:
                item = to_server_list_item(dto)
                item.is_online = dto.last_seen_at is not None and dto.last_seen_at > threshold
                items.append(item)
        else:
            for dto, flag in zip(dtos, online_flags):
                item = to_server_list_item(dto)
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
    ) -> AttentionSignals:
        """list 화면 상단 "주의 필요 신호" — risk_top과 차별 (시간 축·도메인).

        - disk_warnings: 마이그레이션 전 cleanup 직접 액션 (스토리지 임박 mount).
        - gap_warnings: 모니터링 사각지대 (한때 살아있다 끊김).
        둘 다 단일 SQL — N+1 없음. limit_each로 표시 부담 제어.
        """
        disk_raws = await self.repo.disk_usage_warnings(disk_threshold_pct, limit_each)
        gap_raws = await self.repo.metric_gap_warnings(gap_minutes, gap_recent_hours, limit_each)
        now = datetime.now(timezone.utc)
        return AttentionSignals(
            disk_warnings=[to_disk_warning_item(r) for r in disk_raws],
            gap_warnings=[to_gap_warning_item(r, now) for r in gap_raws],
        )

    async def get_risk_top(self, limit: int = 3) -> list[RiskServerItem]:
        """주의 필요 상위 N서버 — 24h USE 통계 + 온라인 상태 기반.

        흐름: list_server_ids (ID만 fetch) → report_aggregate (단일 SQL) → mapper score 계산 + 정렬.
        page=1 첫 호출에서만 사용 (scroll·검색 화면은 미사용 — context 의존성 격리).
        """
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return []
        end_dt = datetime.now(timezone.utc)
        raws = await self.repo.report_aggregate(server_ids, period_days=1, end=end_dt)
        # 카드 도넛 3개 중 disk는 별도 SQL — server_metrics와 별 테이블이라 단일 SQL 부담 회피.
        disk_max_map = await self.repo.latest_disk_max_pct([r.server_id for r in raws])

        online_keys = [web_settings.redis_key_online.format(r.server_id) for r in raws]
        flags = await safe_mget(self.redis, online_keys)
        threshold = end_dt - timedelta(seconds=web_settings.redis_ttl_online)

        items: list[RiskServerItem] = []
        for i, raw in enumerate(raws):
            if flags is None:
                online = bool(raw.last_seen_at and raw.last_seen_at > threshold)
            else:
                online = flags[i] is not None
            items.append(
                to_risk_server_item(raw, online, disk_max_pct=disk_max_map.get(raw.server_id))
            )
        items.sort(key=lambda item: item.risk_score, reverse=True)
        return items[:limit]

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
        raws = await self.repo.report_aggregate(server_ids, period_days, end_dt)

        online_keys = [web_settings.redis_key_online.format(r.server_id) for r in raws]
        flags = await safe_mget(self.redis, online_keys)
        threshold = end_dt - timedelta(seconds=web_settings.redis_ttl_online)

        items: list[ReportRowItem] = []
        for i, raw in enumerate(raws):
            if flags is None:
                online = bool(raw.last_seen_at and raw.last_seen_at > threshold)
            else:
                online = flags[i] is not None
            items.append(to_report_row_item(raw, online))

        return ReportSummary(
            rows=items,
            period_days=period_days,
            total=len(items),
            online=sum(1 for it in items if it.is_online),
            over=sum(1 for it in items if it.recommendation == "over_provisioned"),
            under=sum(1 for it in items if it.recommendation == "under_provisioned"),
        )

    async def get_inventory_export(self, server_ids: list[int]) -> list[InventoryExportEntry]:
        """선택 서버 N대의 정제 inventory JSON 항목 list.

        각 서버는 ServerDetail(outbound) → mapper로 변환. 누락된 server_id는 silent skip
        (운영자가 발행 시점에 삭제했을 가능성 — 부분 결과 반환).

        C5: `get_servers` 단일 SQL — 입력 server_ids 순서로 정렬 후 반환.
        """
        details = await self.repo.get_servers(server_ids)
        order = {sid: i for i, sid in enumerate(server_ids)}
        details.sort(key=lambda d: order.get(d.id, len(server_ids)))
        return [to_inventory_export_entry(d) for d in details]

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