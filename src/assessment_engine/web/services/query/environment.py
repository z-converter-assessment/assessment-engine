"""환경 개요·실시간·자원평가·토폴로지 조회 mixin."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from assessment_engine.db.dtos.outbound import (
    EnvironmentUtilizationRaw,
    FleetErrorRaw,
    MountCapacityRaw,
    ReportRowRaw,
    SaturationRaw,
    ServerDetail,
)
from assessment_engine.db.repositories.query.types import DIAGNOSTIC_RANGE_DAYS, TimeRange
from assessment_engine.domain import right_sizing
from assessment_engine.json_types import JsonObject, json_list
from assessment_engine.web.services.mappers.assessment_api import build_assessment_entry
from assessment_engine.web.services.mappers.attention import (
    build_action_targets,
    build_environment_overview,
    build_environment_realtime,
    to_capacity_warning_item,
)
from assessment_engine.web.services.mappers.resource_stats import build_resource_stats
from assessment_engine.web.services.mappers.right_sizing_api import build_right_sizing_entry
from assessment_engine.web.services.mappers.topology import build_network_topology
from assessment_engine.web.services.query._base import _BaseQueryServiceMixin, _empty_overview
from assessment_engine.web.services.query.metric import latest_metric
from assessment_engine.web.settings import get_web_settings
from assessment_engine.web.view_models.attention import (
    CapacityWarningItem,
    EnvironmentAssessment,
    EnvironmentOverview,
    EnvironmentRealtime,
)
from assessment_engine.web.view_models.metric import FleetStatus, HostSearchItem

if TYPE_CHECKING:
    from collections.abc import Iterator

    from assessment_engine.web.view_models.topology import NetworkTopology


def assemble_overview(
    details: list[ServerDetail],
    util: EnvironmentUtilizationRaw,
    raws_period: list[ReportRowRaw],
    online_by_id: dict[int, bool],
    full_under: bool = False,
    error_summary: FleetErrorRaw | None = None,
) -> EnvironmentOverview:
    """raws -> 분류 도넛 + 자원 부족 상세, online_by_id -> online_count.

    호출자가 `_with_net_baseline` 로 raw 에 net baseline 을 주입한 뒤 부른다 — 보고서 세부행과 같은
    입력이라야 유휴 판정이 어긋나지 않는다. full_under=True 는 자원 부족 호스트 상위 N 절단을 푼다.

    본문에 `self` 가 한 번도 없다 — mixin 메서드로 두면 보고서 도메인이 형제 호출을 하게 되고
    그 결합을 Protocol 로 덮어야 한다.
    """
    risk_counts: dict[str, int] = {}
    under_hosts: list[CapacityWarningItem] = []
    # net 혼잡은 사이징 판정이 아니라 별도 품질 플래그 — 분류와 무관하게 포화 도넛에만 얹는다.
    cpu_sat = mem_sat = disk_sat = net_cong = 0
    for raw in raws_period:
        # 호출자마다 raw 의 disk baseline 주입 여부가 다르다 — 보고서 경로만 채워 오고 환경 개요는
        # 비어 있다. 여기서 None 으로 고정하면 보고서 화면 분류가 바뀌므로 raw 가 실은 것을 그대로 쓴다.
        stats = build_resource_stats(raw, disk_baseline=raw.disk_iops_baseline)
        rec = right_sizing.classify_host(stats)
        risk_counts[rec] = risk_counts.get(rec, 0) + 1
        if rec == "under_provisioned":
            under_hosts.append(to_capacity_warning_item(raw))
        if right_sizing.cpu_saturated(stats):
            cpu_sat += 1
        if right_sizing.mem_saturated(stats):
            mem_sat += 1
        if right_sizing.disk_io_saturated(stats):
            disk_sat += 1
        if right_sizing.assess_network(stats).status == "congested":
            net_cong += 1
    online_count = sum(1 for d in details if online_by_id.get(d.id))
    sat_total = util.sample_size
    # 분자는 cagg 버킷(raws_period), 분모는 raw 테이블 기준이라 원천이 다르다 — raw 보존이 WINDOW_DAYS 보다
    # 짧은 구성에서 분자>분모(도넛 비율 100% 초과)가 나올 수 있어 분모를 최대 분자 이상으로 클램프.
    sat_total = max(sat_total, cpu_sat, mem_sat, disk_sat, net_cong)
    sat_counts = {"cpu": cpu_sat, "mem": mem_sat, "disk_io": disk_sat, "net": net_cong, "total": sat_total}
    return build_environment_overview(
        details,
        online_count,
        util,
        risk_counts,
        under_hosts,
        saturation_counts=sat_counts,
        error_summary=error_summary,
        **({"under_limit": None} if full_under else {}),
    )


class EnvironmentQueryMixin(_BaseQueryServiceMixin):
    async def get_environment_realtime(self, server_ids: list[int] | None = None) -> EnvironmentRealtime:
        """각 서버 최신 스냅샷 집계 — 현황 모니터링 전용이라 right-sizing 평가 창과 무관하다."""
        now = datetime.now(UTC)
        if server_ids is None:
            server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return build_environment_realtime(0, 0, [], None)
        details = await self.repo.get_servers(server_ids)
        return await self._assemble_realtime(server_ids, details, now)

    def _assemble_overview(
        self,
        details: list[ServerDetail],
        util: EnvironmentUtilizationRaw,
        raws_period: list[ReportRowRaw],
        online_by_id: dict[int, bool],
        full_under: bool = False,
        error_summary: FleetErrorRaw | None = None,
    ) -> EnvironmentOverview:
        return assemble_overview(details, util, raws_period, online_by_id, full_under, error_summary)

    async def get_environment_assessment(
        self, time_range: TimeRange, anchor_at: datetime | None = None
    ) -> EnvironmentAssessment:
        """자원 평가 페이지 — 윈도우·앵커 가변 분류 분포 + 서버별 자원 적정성 표(보고서와 동일 산식)."""
        end = anchor_at or datetime.now(UTC)
        period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return EnvironmentAssessment(overview=_empty_overview())
        details = await self.repo.get_servers(server_ids)
        raws_period = await self.repo.get_report_aggregate(
            server_ids,
            period_days=period_days,
            end=end,
        )
        raws_period = await self._with_net_baseline(raws_period, server_ids, period_days, end)
        util = await self.repo.get_environment_utilization(period_days=period_days, end=end)
        online_by_id = await self._online_map(server_ids, details, end)
        overview = self._assemble_overview(details, util, raws_period, online_by_id)
        action = build_action_targets(raws_period)
        return EnvironmentAssessment(overview=overview, action=action)

    async def get_right_sizing(
        self,
        window_days: float,
        end: datetime,
        hostnames: list[str] | None = None,
        ips: list[str] | None = None,
        public_ids: list[str] | None = None,
        pairs: list[tuple[str, str]] | None = None,
    ) -> JsonObject:
        """외부 자동화 소비용 right-sizing 판정 — 필터 매칭 서버의 자원 3축 + 네트워크. 산식은 보고서와 동일.

        hostname 은 유일하지 않다(public_id 만 유일) — 중복명을 정확히 지정하려면 pairs=[(hostname, IP|public_id)].
        모호하거나 해석되지 않은 필터는 반환 dict 의 경고 키로 알린다.
        필터 미지정이면 전체 서버. 데이터가 창보다 짧으면 신뢰도가 자동 하향된다.
        """
        matched_details, ambiguous, ambiguous_in_filter, unresolved, _unmatched = await self._resolve_matches(
            hostnames, ips, public_ids, pairs
        )
        matched_ids = [d.id for d in matched_details]
        raws: list[ReportRowRaw] = []
        online_by_id: dict[int, bool] = {}
        if matched_ids:
            raws = await self.repo.get_report_aggregate(
                matched_ids,
                period_days=window_days,
                end=end,
            )
            raws = await self._with_net_baseline(raws, matched_ids, window_days, end)
            online_by_id = await self._online_map(matched_ids, matched_details, end)
        servers = [
            build_right_sizing_entry(
                raw, online_by_id.get(raw.server_id, False), hostname_ambiguous=raw.hostname in ambiguous
            )
            for raw in raws
        ]
        return {
            "servers": servers,
            "ambiguous_hostnames": ambiguous_in_filter,
            "unresolved_pairs": unresolved,
        }

    async def _resolve_matches(
        self,
        hostnames: list[str] | None,
        ips: list[str] | None,
        public_ids: list[str] | None,
        pairs: list[tuple[str, str]] | None,
    ) -> tuple[list[ServerDetail], set[str], list[str], list[str], list[str]]:
        """필터 -> 매칭 서버 details + 안전 경고. 경량 inventory 로 먼저 좁힌다(비싼 집계 앞 pushdown).

        반환: (matched_details, ambiguous_set, ambiguous_in_filter, unresolved_pairs, unmatched_filters).
        ambiguous_set = 환경 내 2대+ 가 공유하는 hostname.
        """
        # 모집단에 캡을 씌우면 상위 id 밖 서버가 매칭에서 빠지고 동명 판정도 무력화된다.
        all_ids = await self.repo.list_server_ids(limit=None)
        if not all_ids:
            return [], set(), [], [], []
        all_details = await self.repo.get_servers(all_ids)
        hn = {h.strip() for h in (hostnames or []) if h.strip()}
        ipset = {i.strip() for i in (ips or []) if i.strip()}
        pid = {p.strip() for p in (public_ids or []) if p.strip()}
        pairs = pairs or []
        name_counts = Counter(d.hostname for d in all_details)
        ambiguous = {h for h, c in name_counts.items() if c > 1}

        def _addrs(s: ServerDetail) -> Iterator[str | None]:
            return (a.get("address") for i in s.net_interfaces or [] for a in json_list(i, "addresses"))

        def _disc_match(s: ServerDetail, disc: str) -> bool:
            return disc == s.public_id or any(addr == disc for addr in _addrs(s))

        def _match(s: ServerDetail) -> bool:
            if not hn and not ipset and not pid and not pairs:
                return True
            if hn and s.hostname in hn:
                return True
            if pid and s.public_id in pid:
                return True
            if ipset and any(addr in ipset for addr in _addrs(s)):
                return True
            return bool(pairs) and any(s.hostname == h and _disc_match(s, disc) for h, disc in pairs)

        matched = [d for d in all_details if _match(d)]
        ambiguous_in_filter = sorted(hn & ambiguous)
        unresolved = [
            f"{h}~{disc}" for h, disc in pairs if not any(d.hostname == h and _disc_match(d, disc) for d in all_details)
        ]
        all_hostnames = {d.hostname for d in all_details}
        all_addrs = {addr for d in all_details for addr in _addrs(d)}
        all_pids = {d.public_id for d in all_details}
        unmatched = (
            [h for h in sorted(hn) if h not in all_hostnames]
            + [i for i in sorted(ipset) if i not in all_addrs]
            + [p for p in sorted(pid) if p not in all_pids]
        )
        return matched, ambiguous, ambiguous_in_filter, unresolved, unmatched

    async def get_assessment(
        self,
        window_days: float,
        end: datetime,
        hostnames: list[str] | None = None,
        ips: list[str] | None = None,
        public_ids: list[str] | None = None,
        pairs: list[tuple[str, str]] | None = None,
    ) -> JsonObject:
        """통합 프로비저닝 어세스먼트(/api/assessment) — 계약: docs/reference/contracts/assessment-api.md.

        right-sizing 이 자원 판정만 내는 데 비해 per-mount 디스크 사이징 + reproduction 팩트까지 한 응답에 담는다.
        """
        matched_details, ambiguous, ambiguous_in_filter, unresolved, unmatched = await self._resolve_matches(
            hostnames, ips, public_ids, pairs
        )
        matched_ids = [d.id for d in matched_details]
        raws: list[ReportRowRaw] = []
        online_by_id: dict[int, bool] = {}
        mounts_by_id: dict[int, list[MountCapacityRaw]] = {}
        link_speeds: dict[int, dict[str, int]] = {}
        if matched_ids:
            raws = await self.repo.get_report_aggregate(
                matched_ids,
                period_days=window_days,
                end=end,
            )
            raws = await self._with_net_baseline(raws, matched_ids, window_days, end)
            online_by_id = await self._online_map(matched_ids, matched_details, end)
            mounts_by_id = await self.repo.get_report_mount_capacity_batch(matched_ids, end)
            # inventory 가 speed_mbps 를 안 싣는 환경(virtio·Windows NT5.2)의 폴백 — metrics link.speed.
            link_speeds = await self.repo.get_latest_link_speed(matched_ids, end - timedelta(days=window_days))
        servers = [
            build_assessment_entry(
                raw,
                mounts_by_id.get(raw.server_id, []),
                online_by_id.get(raw.server_id, False),
                hostname_ambiguous=raw.hostname in ambiguous,
                link_speeds=link_speeds.get(raw.server_id),
            )
            for raw in raws
        ]
        return {
            "servers": servers,
            "ambiguous_hostnames": ambiguous_in_filter,
            "unresolved_pairs": unresolved,
            "unmatched_filters": unmatched,
        }

    async def _assemble_realtime(
        self, server_ids: list[int], details: list[ServerDetail], now: datetime
    ) -> EnvironmentRealtime:
        """최신 스냅샷 집계 — 표본은 TTL 안에 들어온 서버만, stale 메트릭이 현황 평균을 왜곡한다.

        온라인 판정도 같은 신선도 기준이다(차트의 '그 시점 발행 서버' 와 정렬).
        """
        detail_by_id = {d.id: d for d in details}
        fresh_threshold = now - timedelta(seconds=get_web_settings().redis_ttl_online)
        sat_map = await self.repo.get_latest_saturation(server_ids, fresh_threshold)
        online = 0
        snapshots: list[JsonObject] = []
        last_collected = None
        for sid in server_ids:
            d = detail_by_id.get(sid)
            if d is None:
                continue
            # 벌크로 받아 둔 포화 원자료를 주입 — per-server 재조회를 없앤다.
            sat = sat_map.get(sid) or SaturationRaw()
            m = await latest_metric(self.repo, self.redis, sid, sat)
            if not m or not m.collected_at or m.collected_at < fresh_threshold:
                continue
            online += 1
            mem = m.memory
            # 전 인터페이스 rx+tx 합(kB/s) — 혼잡 신호의 저트래픽 게이트 입력.
            net_rate = sum((n.rx_kbps or 0) + (n.tx_kbps or 0) for n in m.net_io) if m.net_io else None
            snapshots.append(
                {
                    "hostname": d.hostname,
                    "public_id": d.public_id,
                    "os_family": d.os_family,
                    # 디스크 용량은 느린 신호라 실시간 랭킹에서 뺀다.
                    "cpu_pct": m.cpu.usage_pct if m.cpu else None,
                    "mem_pct": mem.usage_pct if mem else None,
                    # os-aware 포화 지수 — 1 이상이 포화.
                    "cpu_sat_index": right_sizing.cpu_saturation_index(sat.run_queue, d.cpu_cores, d.os_family),
                    "disk_sat_index": right_sizing.disk_io_saturation_index(sat.await_ms, sat.pending_ops, d.os_family),
                    # worst device busy% — 위 응답 지연(saturation)과 다른 축.
                    "disk_util_pct": sat.disk_io_util_pct,
                    # 단위 — 페이징은 major fault/s, 네트워크는 kB/s.
                    "paging_rate": sat.paging_major_rate,
                    "net_kbps": net_rate,
                    "mem_pressure": right_sizing.mem_pressure_active(sat.paging_major_rate, d.os_family),
                    "net_congested": right_sizing.net_signal_active(
                        sat.retrans_pct, sat.drop_pct, sat.conntrack_ratio, net_rate
                    ),
                    # capacity-weighted 평균 도넛용 가중치 — cpu 는 코어, mem 은 절대 총량 sum/sum.
                    "cpu_cores": d.cpu_cores,
                    "mem_used_bytes": mem.used_bytes if mem else None,
                    "mem_total_bytes": mem.total_bytes if mem else None,
                }
            )
            if last_collected is None or m.collected_at > last_collected:
                last_collected = m.collected_at
        return build_environment_realtime(len(server_ids), online, snapshots, last_collected)

    async def get_dashboard_overview(self) -> EnvironmentOverview:
        """환경 개요(`/`) 집계 — 분류·이용률·포화 전부 right-sizing 표준 창이라 서버 목록·보고서와 정합(#E3).

        운영 신호(attention)는 실시간 현황 페이지로 분리. 앵커는 now(라이브 첫 화면).
        """
        now = datetime.now(UTC)
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return _empty_overview()
        details = await self.repo.get_servers(server_ids)
        raws_period = await self.repo.get_report_aggregate(server_ids, period_days=right_sizing.WINDOW_DAYS, end=now)
        raws_period = await self._with_net_baseline(raws_period, server_ids, right_sizing.WINDOW_DAYS, now)
        util = await self.repo.get_environment_utilization(period_days=right_sizing.WINDOW_DAYS, end=now)
        online_by_id = await self._online_map(server_ids, details, now)
        # 에러는 드문 이벤트라 창을 제한하지 않는다 — #C5 partition pruning 의 의식적 예외(집계 비용 낮음).
        errors = await self.repo.get_fleet_error_summary(server_ids, datetime(1970, 1, 1, tzinfo=UTC))
        return self._assemble_overview(details, util, raws_period, online_by_id, error_summary=errors)

    async def get_topology(self) -> NetworkTopology:
        """네트워크 토폴로지 — L3 subnet 공동소속 그래프. 노드 규모가 커 개요와 다른 페이지에서 렌더한다."""
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return build_network_topology([])
        details = await self.repo.get_servers(server_ids)
        online_by_id = await self._online_map(server_ids, details, datetime.now(UTC))
        return build_network_topology(details, online_by_id)

    async def get_fleet_status(self) -> FleetStatus:
        """전역 상단 바 — 온라인 대수/전체 + 마지막 메트릭 수집 시각."""
        now = datetime.now(UTC)
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return FleetStatus(online_count=0, total_count=0, last_collected_at=None)
        details = await self.repo.get_servers(server_ids)
        online_by_id = await self._online_map(server_ids, details, now)
        online_count = sum(1 for v in online_by_id.values() if v)
        last_collected = await self.repo.get_latest_metric_at()
        return FleetStatus(online_count=online_count, total_count=len(server_ids), last_collected_at=last_collected)

    async def search_hosts(self, q: str, limit: int = 8) -> list[HostSearchItem]:
        """전역 호스트 검색(jump-to) — hostname 부분일치."""
        summaries = await self.repo.list_servers(page=1, limit=limit, search=q)
        return [HostSearchItem(hostname=s.hostname, public_id=str(s.public_id), os_id=s.os_id) for s in summaries]
