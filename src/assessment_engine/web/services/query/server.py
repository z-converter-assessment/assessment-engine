"""서버 조회 mixin — 식별자 해석·목록·상세·스토리지·네트워크·수집상태."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from assessment_engine.cache.redis import safe_get, safe_mget, safe_set
from assessment_engine.domain import right_sizing
from assessment_engine.web.services.cache_serializer import (
    server_detail_from_json,
    server_detail_to_json,
)
from assessment_engine.web.services.mappers.metric import to_collection_status_item
from assessment_engine.web.services.mappers.metric_dashboard import build_error_signals
from assessment_engine.web.services.mappers.os_eol import (
    lookup_os_eol,
)
from assessment_engine.web.services.mappers.period_assessment import build_period_assessment
from assessment_engine.web.services.mappers.resource_stats import build_resource_stats
from assessment_engine.web.services.mappers.server import (
    to_network_detail,
    to_server_detail,
    to_server_list_item,
    to_storage_detail,
)
from assessment_engine.web.services.query._base import _BaseQueryServiceMixin
from assessment_engine.web.services.query.task import latest_task_summaries
from assessment_engine.web.settings import get_web_settings
from assessment_engine.web.view_models.server import (
    NetworkDetailResponse,
    ServerDetailResponse,
    ServerListItem,
    ServerStabilitySignals,
    StorageDetailResponse,
)

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import ReportRowRaw
    from assessment_engine.web.view_models.metric import CollectionStatusItem, PeriodAssessment


_DETAIL_ALL_TIME_DAYS = 36500


class ServerQueryMixin(_BaseQueryServiceMixin):
    async def resolve_server_id(self, public_id: str) -> int | None:
        cache_key = get_web_settings().redis_key_cache_resolve.format(public_id)
        cached = await safe_get(self.redis, cache_key)
        if cached:
            return int(cached)
        server_id = await self.repo.resolve_server_id(public_id)
        if server_id is not None:
            await safe_set(self.redis, cache_key, str(server_id))
        return server_id

    async def resolve_server_ids(self, public_ids: list[str]) -> dict[str, int]:
        """N개 public_id -> {public_id: server_id} 단일 SQL.

        캐시를 쓰지 않는다 — 미스여도 DB 조회가 단일 SQL 이라 mget 복잡도 대비 이득이 없다.
        """
        if not public_ids:
            return {}
        return await self.repo.resolve_server_ids(public_ids)

    async def list_server_public_ids(self) -> list[str]:
        return await self.repo.list_server_public_ids()

    async def _is_online(self, server_id: int) -> bool:
        flag = await safe_get(self.redis, get_web_settings().redis_key_online.format(server_id))
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
        os_eol: str | None = None,
    ) -> list[ServerListItem]:
        dtos = await self.repo.list_servers(page, limit, search)
        if not dtos:
            return []
        keys = [get_web_settings().redis_key_online.format(dto.id) for dto in dtos]
        online_flags = await safe_mget(self.redis, keys)

        page_server_ids = [dto.id for dto in dtos]
        now = datetime.now(UTC)
        raws_period = await self.repo.get_report_aggregate(
            page_server_ids,
            period_days=right_sizing.WINDOW_DAYS,
            end=now,
        )

        raws_period = await self._with_net_baseline(raws_period, page_server_ids, right_sizing.WINDOW_DAYS, now)
        raws_by_id: dict[int, ReportRowRaw] = {r.server_id: r for r in raws_period}

        last_tasks = await latest_task_summaries(self.repo, page_server_ids)

        error_hosts = await self.repo.get_fleet_error_hosts(page_server_ids, datetime(1970, 1, 1, tzinfo=UTC))

        items: list[ServerListItem] = []
        if online_flags is None:
            threshold = datetime.now(UTC) - timedelta(seconds=get_web_settings().redis_ttl_online)
            for dto in dtos:
                item = to_server_list_item(dto, raws_by_id.get(dto.id), today=now.date(), error_hosts=error_hosts)
                item.is_online = dto.last_seen_at is not None and dto.last_seen_at > threshold
                item.last_task = last_tasks.get(dto.id)
                items.append(item)
        else:
            for dto, flag in zip(dtos, online_flags, strict=True):
                item = to_server_list_item(dto, raws_by_id.get(dto.id), today=now.date(), error_hosts=error_hosts)
                item.is_online = flag is not None
                item.last_task = last_tasks.get(dto.id)
                items.append(item)

        if is_online is not None:
            items = [i for i in items if i.is_online == is_online]
        if service:
            items = [i for i in items if any(s.category == service for s in i.known_services)]
        if os_distro:
            items = [i for i in items if i.os_distro == os_distro]
        if classification:
            items = [i for i in items if i.provisioning_class == classification]

        if os_eol == "unknown":
            items = [i for i in items if i.os_eol_status == "unknown"]
        elif os_eol == "eol":
            items = [i for i in items if i.os_eol_status in ("paid_only", "ended")]
        elif os_eol == "supported":
            items = [i for i in items if i.os_eol_status in ("full", "security_only")]

        items.sort(key=lambda i: (not i.is_online, i.hostname.lower()))
        return items

    async def get_server(self, server_id: int) -> ServerDetailResponse | None:
        cache_key = get_web_settings().redis_key_cache_inventory.format(server_id)
        cached = await safe_get(self.redis, cache_key)
        if cached:
            return server_detail_from_json(cached)

        dto = await self.repo.get_server(server_id)
        if not dto:
            return None
        result = to_server_detail(dto)
        await safe_set(
            self.redis, cache_key, server_detail_to_json(result), ex=get_web_settings().redis_ttl_cache_detail
        )
        return result

    async def get_server_stability(
        self, detail: ServerDetailResponse, end: datetime | None = None
    ) -> ServerStabilitySignals:
        """서버 세부 운영 신호 — 전구간 재부팅·에이전트 재시작 + OS 지원종료.

        엔지니어 보고서(7일 window)와 달리 전체 수집 기간 기준. window 집계라 inventory 캐시에 태우지 않는다.
        """
        end_dt = end or datetime.now(UTC)
        sid = detail.id
        uptime = await self.repo.get_report_uptime_stats([sid], _DETAIL_ALL_TIME_DAYS, end_dt)
        restart = await self.repo.get_report_agent_restart_stats([sid], _DETAIL_ALL_TIME_DAYS, end_dt)
        # 인벤토리 표시라 today-gate 를 두지 않는다 — 아직 안 온 EOL 도 그대로 노출.
        info = lookup_os_eol(detail.os_id, detail.os_version, detail.kernel_version, end_dt.date())
        os_eol_label = None
        if info is not None:
            _phase = {
                "ended": "패치 없음",
                "paid_only": "무상 패치 종료(연장 지원 단계)",
                "security_only": "보안 패치만",
                "full": "지원 중",
            }.get(info.status, "지원 중")
            os_eol_label = f"EOL {info.eol_iso} · {_phase}"
        return ServerStabilitySignals(
            reboot_count=uptime.get(sid, 0),
            agent_restart_count=restart.get(sid, 0),
            os_eol_label=os_eol_label,
        )

    async def get_period_assessment(self, server_id: int, end: datetime | None = None) -> PeriodAssessment | None:
        """서버 세부 '최근 N일' 카드 — 자원별 이용률(p95)+포화 2축.

        목록 분류와 같은 입력·창(WINDOW_DAYS)을 쓴다. 창 집계라 inventory 캐시에 태우지 않는다.
        """
        end_dt = end or datetime.now(UTC)
        raws = await self.repo.get_report_aggregate([server_id], period_days=right_sizing.WINDOW_DAYS, end=end_dt)
        if not raws:
            return None
        raws = await self._with_net_baseline(raws, [server_id], right_sizing.WINDOW_DAYS, end_dt)

        win_days = right_sizing.WINDOW_DAYS
        err = await self.repo.get_latest_errors(server_id, end_dt - timedelta(days=win_days))
        errors = build_error_signals(err, window_label=f"최근 {win_days}일", os_family=raws[0].os_family)
        return build_period_assessment(
            build_resource_stats(raws[0], disk_baseline=None),
            errors,
            disk_worst_mount=raws[0].disk_capacity_worst_mount,
            window_days=win_days,
        )

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
