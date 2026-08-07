"""Query service 공통 mixin — repo/redis 보유 + 다중 도메인 공유 helper.

도메인 mixin 이 전부 이걸 상속해 `QueryService` 결합 시 `__init__` 이 한 번만 돌고 repo·redis 를 공유한다.
repo 계층 `db/repositories/query/_base.py` 와 동형.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from assessment_engine.cache.redis import safe_mget
from assessment_engine.web.settings import get_web_settings
from assessment_engine.web.view_models.attention import AttentionSignals, EnvironmentOverview

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from assessment_engine.db.dtos.outbound import NetIoBaselineRaw, ReportRowRaw, ServerDetail
    from assessment_engine.db.repositories.query import QueryRepository


def _net_baseline_fields(net: NetIoBaselineRaw | None) -> dict[str, float | None]:
    """net baseline 을 `dataclasses.replace` 키워드로. 미측정(None)이면 빈 dict — 기존 값을 덮지 않는다.

    보고서 경로(`_with_report_baselines`)와 공유한다. 두 경로가 net 을 서로 다른 필드 집합으로 채우면
    같은 호스트가 화면마다 다른 유휴 판정을 받는다.
    """
    if net is None:
        return {}
    return {
        "net_rx_kbps": net.rx_kbps_baseline,
        "net_tx_kbps": net.tx_kbps_baseline,
        "net_rx_kbps_p95": net.rx_p95,
        "net_rx_kbps_peak": net.rx_peak,
        "net_tx_kbps_p95": net.tx_p95,
        "net_tx_kbps_peak": net.tx_peak,
    }


class _BaseQueryServiceMixin:
    """전 도메인 mixin 이 공유하는 repo·redis 보유부 + online/net baseline helper."""

    def __init__(self, repo: QueryRepository, redis: Redis):
        self.repo = repo
        self.redis = redis

    async def _online_map(self, server_ids: list[int], details: list[ServerDetail], now: datetime) -> dict[int, bool]:
        """server_id -> online bool. Redis 우선, `safe_mget` 이 None(=Redis 장애)이면 last_seen_at fallback.

        `get_servers` 가 순서를 보존하지 않아 details 가 아니라 server_ids 로 매칭한다.
        """
        online_keys = [get_web_settings().redis_key_online.format(sid) for sid in server_ids]
        flags = await safe_mget(self.redis, online_keys)
        threshold = now - timedelta(seconds=get_web_settings().redis_ttl_online)
        if flags is None:
            return {d.id: bool(d.last_seen_at and d.last_seen_at > threshold) for d in details}
        return {sid: (flags[i] is not None) for i, sid in enumerate(server_ids)}

    async def _with_net_baseline(
        self, raws: list[ReportRowRaw], server_ids: list[int], period_days: float, end: datetime
    ) -> list[ReportRowRaw]:
        """raws 에 net I/O baseline 을 얹은 새 list.

        net 이 비면 `build_resource_stats` 의 유휴 판정이 구조적으로 빠져 분류가 get_report(세부행)와
        어긋난다. 호출부는 반환값을 반드시 다시 묶는다 — 제자리 수정이 아니라 새 행을 만들므로,
        안 묶으면 net 이 통째로 빠진 채 조용히 진행된다.
        """
        # period_days 는 15m 창(=0.0104일)까지 내려가는 float 이고 repo 는 timedelta(days=)로 그대로 받는다.
        net_io = await self.repo.get_report_net_io_baseline(
            server_ids,
            period_days,
            end,
        )
        return [replace(raw, **_net_baseline_fields(net_io.get(raw.server_id))) for raw in raws]


def _filter_attention(attention: AttentionSignals, hostnames: set[str]) -> AttentionSignals:
    """전체 운영 신호를 선택 N대 호스트로 좁힌다 (신호 행의 link_text 가 hostname)."""
    return AttentionSignals(
        gap_warnings=[w for w in attention.gap_warnings if w.link_text in hostnames],
        os_eol_warnings=[w for w in attention.os_eol_warnings if w.link_text in hostnames],
        agent_unstable=[w for w in attention.agent_unstable if w.link_text in hostnames],
    )


def _empty_overview() -> EnvironmentOverview:
    """등록 서버 0대일 때의 환경 요약."""
    return EnvironmentOverview(
        total=0,
        online=0,
        offline=0,
        total_vcpus=0,
        total_memory_gb=0.0,
        total_disk_gb=0,
        role_distribution={},
    )
