"""서버 조회 mixin — 식별자 해석·목록·상세·스토리지·네트워크·수집상태."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from assessment_engine import recommendation
from assessment_engine.cache.redis import safe_get, safe_mget, safe_set
from assessment_engine.web.services.cache_serializer import (
    server_detail_from_json,
    server_detail_to_json,
)
from assessment_engine.web.services.mappers.metric import to_collection_status_item
from assessment_engine.web.services.mappers.server import (
    to_network_detail,
    to_server_detail,
    to_server_list_item,
    to_storage_detail,
)
from assessment_engine.web.services.mappers.shared import lookup_os_eol
from assessment_engine.web.services.query._base import _BaseQueryServiceMixin
from assessment_engine.web.settings import get_web_settings
from assessment_engine.web.view_models.metric import CollectionStatusItem, PeriodAssessment
from assessment_engine.web.view_models.server import (
    NetworkDetailResponse,
    ServerDetailResponse,
    ServerListItem,
    ServerStabilitySignals,
    StorageDetailResponse,
)

if TYPE_CHECKING:
    from assessment_engine.web.services.query._base import _TaskSibling

# 서버 세부 운영 신호 전구간 — 재부팅·에이전트 재시작을 window 제한 없이 전체 수집 기간 카운트(약 100년).
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
        """N개 public_id → {public_id: server_id} 단일 SQL (C5 N+1 회피).

        cache 미활용 — batch 미스 시 DB도 단일 SQL이라 cache mget 복잡도 대비 이득 미미.
        """
        if not public_ids:
            return {}
        return await self.repo.resolve_server_ids(public_ids)

    async def list_all_server_public_ids(self) -> list[str]:
        return await self.repo.list_all_server_public_ids()

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

        # USE Method 분류 — 보고서·right-sizing과 동일 윈도우·입력(net 포함).
        page_server_ids = [dto.id for dto in dtos]
        now = datetime.now(UTC)
        raws_period = await self.repo.report_aggregate(
            page_server_ids,
            period_days=recommendation.WINDOW_DAYS,
            end=now,
        )
        # net baseline 주입 — build_resource_stats 의 유휴 판정이 net 사용 (도넛·보고서와 정합).
        await self._inject_net_baseline(raws_period, page_server_ids, recommendation.WINDOW_DAYS, now)
        raws_by_id: dict[int, object] = {r.server_id: r for r in raws_period}

        last_tasks = await cast("_TaskSibling", self).latest_tasks_by_servers(page_server_ids)

        # 운영 이벤트 — 전체 기간 에러 발생 호스트 집합(벌크 1회, N+1 회피). since=epoch 전기간은 환경 개요
        # 운영 이벤트 카드(fleet_error_summary 를 epoch 호출)와 동일 창 — 개요-목록 정합(#F10 화면 간 의미 단일).
        error_hosts = await self.repo.fleet_error_hosts(page_server_ids, datetime(1970, 1, 1, tzinfo=UTC))

        items: list[ServerListItem] = []
        if online_flags is None:
            # Redis 장애 fallback: last_seen_at 기반 판정 (TTL 임계와 동일)
            threshold = datetime.now(UTC) - timedelta(seconds=get_web_settings().redis_ttl_online)
            for dto in dtos:
                item = to_server_list_item(dto, raws_by_id.get(dto.id), today=now.date(), error_hosts=error_hosts)
                item.is_online = dto.last_seen_at is not None and dto.last_seen_at > threshold
                item.last_task = last_tasks.get(dto.id)
                items.append(item)
        else:
            # dtos와 online_flags는 동일 길이 보장 — mget이 키 개수만큼 반환.
            for dto, flag in zip(dtos, online_flags, strict=True):
                item = to_server_list_item(dto, raws_by_id.get(dto.id), today=now.date(), error_hosts=error_hosts)
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
        # 3분기 필터 — eol(무상 패치 종료) / supported(패치 유지) / unknown(판정 불가).
        # 미매칭을 supported 로 뭉개지 않는다. 판정 4단계를 필터에서는 셋으로 접는다.
        if os_eol == "unknown":
            items = [i for i in items if i.os_eol_status == "unknown"]
        elif os_eol == "eol":
            items = [i for i in items if i.os_eol_status in ("paid_only", "ended")]
        elif os_eol == "supported":
            items = [i for i in items if i.os_eol_status in ("full", "security_only")]
        # 1차 online(온라인 우선) · 2차 hostname ASC. online 판정은 Redis 기반이라 DB ORDER BY 불가 →
        # service 레이어 정렬(P2). repo 는 hostname ASC raw 반환(2차 기준 보존).
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
        """서버 세부 운영 신호 — 전구간 재부팅·에이전트 재시작 + OS 지원종료(P2).

        selection 엔지니어 보고서는 7일 window 카운트지만 서버 세부는 전체 수집 기간(전구간) 기준.
        ServerDetailResponse 캐시(inventory)와 분리 — window 집계라 매 요청 산출.
        """
        end_dt = end or datetime.now(UTC)
        sid = detail.id
        uptime = await self.repo.report_uptime_stats([sid], _DETAIL_ALL_TIME_DAYS, end_dt)
        restart = await self.repo.report_agent_restart_stats([sid], _DETAIL_ALL_TIME_DAYS, end_dt)
        # 인벤토리 표시 — 미래 EOL 도 노출(lookup, today-gate 없음). 판정 4단계를 그대로 문구로 옮긴다.
        info = lookup_os_eol(detail.os_id, detail.os_version, detail.kernel_version, end_dt.date())
        os_eol_label = None
        if info is not None:
            _phase = {
                "ended": "패치 없음",
                "paid_only": "무상 패치 종료(유상 연장만)",
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
        """서버 세부 '최근 N일' 카드 — 자원별 이용률(p95)+포화 2축 (right-sizing 분류 창=WINDOW_DAYS).

        ServerDetailResponse 캐시(inventory)와 분리 — 14일 집계라 매 요청 산출(목록 분류와 동일 입력·창, #E3).
        build_resource_stats/build_period_assessment 는 report mapper 지연 import(report->server 모듈 순환 회피).
        """
        from assessment_engine.web.services.mappers.report import build_period_assessment, build_resource_stats
        from assessment_engine.web.services.metrics_calculator import build_error_signals

        end_dt = end or datetime.now(UTC)
        raws = await self.repo.report_aggregate([server_id], period_days=recommendation.WINDOW_DAYS, end=end_dt)
        if not raws:
            return None
        await self._inject_net_baseline(raws, [server_id], recommendation.WINDOW_DAYS, end_dt)
        # 에러축(E) — 창 통일(14일). 실시간 카드 24h 와 분리, 분류 카드 창(WINDOW_DAYS)에 맞춤.
        win_days = recommendation.WINDOW_DAYS
        err = await self.repo.latest_errors(server_id, end_dt - timedelta(days=win_days))
        errors = build_error_signals(err, window_label=f"최근 {win_days}일", os_family=raws[0].os_family)
        return build_period_assessment(
            build_resource_stats(raws[0]), errors, disk_worst_mount=raws[0].disk_capacity_worst_mount,
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
