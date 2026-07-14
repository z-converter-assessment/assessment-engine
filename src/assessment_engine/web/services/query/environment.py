"""환경 개요·실시간·자원평가·토폴로지 조회 mixin."""

from collections import Counter
from datetime import UTC, datetime, timedelta

from assessment_engine import recommendation
from assessment_engine.db.dtos.outbound import FleetErrorRaw, SaturationRaw
from assessment_engine.db.repositories.query.types import DIAGNOSTIC_RANGE_DAYS, TimeRange
from assessment_engine.web.services.mappers.assessment_api import build_assessment_entry
from assessment_engine.web.services.mappers.attention import (
    build_action_targets,
    build_environment_overview,
    build_environment_realtime,
    to_capacity_warning_item,
)
from assessment_engine.web.services.mappers.report import build_resource_stats
from assessment_engine.web.services.mappers.right_sizing_api import build_right_sizing_entry
from assessment_engine.web.services.mappers.shared import _DONUT_SEGMENT_FROM_REC
from assessment_engine.web.services.mappers.topology import build_network_topology
from assessment_engine.web.services.query._base import _BaseQueryServiceMixin, _empty_overview
from assessment_engine.web.settings import web_settings
from assessment_engine.web.view_models.attention import (
    CapacityWarningItem,
    EnvironmentAssessment,
    EnvironmentOverview,
    EnvironmentRealtime,
)
from assessment_engine.web.view_models.metric import FleetStatus, HostSearchItem
from assessment_engine.web.view_models.topology import NetworkTopology


class EnvironmentQueryMixin(_BaseQueryServiceMixin):
    async def get_environment_realtime(self, server_ids: list[int] | None = None) -> EnvironmentRealtime:
        """list 화면 '환경 실시간 메트릭' 카드 — 각 서버 최신 스냅샷(get_latest_metric, Redis cache 우선) 집계.

        현황 모니터링 용도(right-sizing 14일 통계와 별개). server_ids=None 이면 전체 환경, 주어지면 선택 N대 한정.
        조립은 _assemble_realtime 단일 진실.
        """
        now = datetime.now(UTC)
        if server_ids is None:
            server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return build_environment_realtime(0, 0, [], None)
        details = await self.repo.get_servers(server_ids)
        return await self._assemble_realtime(server_ids, details, now)

    def _assemble_overview(
        self, details, util, raws_period, online_by_id: dict[int, bool], full_under: bool = False,
        error_summary: FleetErrorRaw | None = None,
    ) -> EnvironmentOverview:
        """report_aggregate raws -> USE Method 분류 도넛 + under_provisioned 상세, online_by_id -> online_count.

        분류는 `build_resource_stats`(net 반영) 단일 진실 — 호출자가 `_inject_net_baseline` 로 raw 에 net
        baseline 을 주입한 뒤 호출한다 (get_report 세부행과 동일 입력 -> 유휴 정합).
        full_under=True 면 자원 부족 호스트 전체(상위 N 절단 해제) — 자원 평가 전용 페이지용.
        """
        risk_counts: dict[str, int] = {}
        under_hosts: list[CapacityWarningItem] = []
        # 포화 4축 호스트 카운트 — 자원 적정성 창(raws_period) 기준. stats 1회 산출로 분류·포화 공용(임계 재계산 0).
        # net 혼잡은 사이징 아닌 별도 품질 플래그(assess_network congested, 단일소스) — 포화 도넛에 orthogonal 노출.
        cpu_sat = mem_sat = disk_sat = net_cong = 0
        for raw in raws_period:
            stats = build_resource_stats(raw)
            rec = recommendation.classify_host(stats)
            seg = _DONUT_SEGMENT_FROM_REC.get(rec, "insufficient_data")
            risk_counts[seg] = risk_counts.get(seg, 0) + 1
            if rec == "under_provisioned":
                under_hosts.append(to_capacity_warning_item(raw))
            if recommendation.cpu_saturated(stats):
                cpu_sat += 1
            if recommendation.mem_saturated(stats):
                mem_sat += 1
            if recommendation.disk_io_saturated(stats):
                disk_sat += 1
            if recommendation.assess_network(stats).status == "congested":
                net_cong += 1
        online_count = sum(1 for d in details if online_by_id.get(d.id))
        # 포화 도넛 표본 = 윈도우 내 metric 발행 호스트 수(util.sample_size). util 부재 시 분류된 호스트 수로 폴백.
        sat_total = util.sample_size if util is not None else len(raws_period)
        sat_counts = {"cpu": cpu_sat, "mem": mem_sat, "disk_io": disk_sat, "net": net_cong, "total": sat_total}
        # full_under 면 under_limit=None(상위 N 절단 해제), 아니면 build_environment_overview 기본값 적용.
        return build_environment_overview(
            details, online_count, util, risk_counts, under_hosts,
            saturation_counts=sat_counts, error_summary=error_summary,
            **({"under_limit": None} if full_under else {}),
        )

    async def get_environment_assessment(
        self, time_range: TimeRange, anchor_at: datetime | None = None
    ) -> EnvironmentAssessment:
        """자원 평가 전용 — 윈도우(time_range)·앵커(anchor) 기준 분류 분포 도넛 + 서버별 자원 적정성 통합 표.

        get_dashboard_overview 의 overview 조립부를 가변 윈도우·앵커로 재사용(attention/trend 제외, 경량).
        서버별 표는 보고서와 동일 산식(`build_action_targets`) — 전 서버(모든 분류) 한 표, 분류 순서 정렬 일관.
        """
        end = anchor_at or datetime.now(UTC)
        period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return EnvironmentAssessment(overview=_empty_overview())
        details = await self.repo.get_servers(server_ids)
        raws_period = await self.repo.report_aggregate(server_ids, period_days=period_days, end=end)
        await self._inject_net_baseline(raws_period, server_ids, period_days, end)
        util = await self.repo.environment_utilization(period_days=period_days, end=end)
        online_by_id = await self._online_map(server_ids, details, end)
        overview = self._assemble_overview(details, util, raws_period, online_by_id)
        # 통합 조치 대상 표 — 자원 부족/과다 할당/유휴 를 한 표에 (build_action_targets 단일 진실).
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
    ) -> dict:
        """외부/자동화 소비용 right-sizing 판정 — 필터(hostname·ip·public_id·순서쌍) 매칭 서버의 자원 3축 + 네트워크.

        분류·근거·신뢰도·권고 전부 보고서/자원평가와 동일 산식(report_aggregate -> rollup_host). 윈도우는
        window_days 종료 end 기준(자원 적정성 표준 14일 기본). 데이터가 창보다 짧으면 신뢰도가 자동 하향.
        필터 미지정이면 전체 서버.

        안전(호스트명 중복): hostname 은 유일하지 않다(public_id UUID 만 유일). 중복 hostname 을 정확히
        지정하려면 순서쌍 pairs=[(hostname, discriminator)] — discriminator 는 IP 또는 public_id — 로 host AND
        판별자를 동시 만족하는 서버만 매칭. 반환 dict 에 안전 신호를 담는다: 각 서버 entry.hostname_ambiguous(그
        hostname 이 환경 내 2대+ 공유), warnings.ambiguous_hostnames(plain hostname 필터가 중복명 명중 -> pair/uuid
        권장), warnings.unresolved_pairs(순서쌍이 어떤 서버로도 해석 안 됨 = 오타/불일치).
        """
        matched_details, ambiguous, ambiguous_in_filter, unresolved, _unmatched = await self._resolve_matches(
            hostnames, ips, public_ids, pairs
        )
        matched_ids = [d.id for d in matched_details]
        raws: list = []
        online_by_id: dict[int, bool] = {}
        if matched_ids:
            raws = await self.repo.report_aggregate(matched_ids, period_days=window_days, end=end)
            await self._inject_net_baseline(raws, matched_ids, window_days, end)
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
    ) -> tuple[list, set, list, list, list]:
        """필터(hostname/ip/public_id/순서쌍) -> 매칭 서버 details + 안전 경고. get_right_sizing/get_assessment 공용.

        매칭/호스트명 충돌 판정은 경량 inventory(get_servers)로 — 비싼 report_aggregate 이전 pushdown(B3).
        반환: (matched_details, ambiguous_set, ambiguous_in_filter, unresolved_pairs, unmatched_filters).
        ambiguous_set = 환경 내 2대+ 공유 hostname(per-server hostname_ambiguous 판정용).
        """
        # 매칭/동명 판정은 전체 fleet 모집단 필요(캡 없음) — 캡 시 상위 id 밖 서버 미매칭 + 안전신호 무력화.
        all_ids = await self.repo.list_server_ids(limit=None)
        if not all_ids:
            return [], set(), [], [], []
        all_details = await self.repo.get_servers(all_ids)
        hn = {h.strip() for h in (hostnames or []) if h.strip()}
        ipset = {i.strip() for i in (ips or []) if i.strip()}
        pid = {p.strip() for p in (public_ids or []) if p.strip()}
        pairs = pairs or []
        # 호스트명 충돌 — 전체 환경 기준(필터 무관 안전 신호). 2대+ 공유 hostname = 모호.
        name_counts = Counter(d.hostname for d in all_details)
        ambiguous = {h for h, c in name_counts.items() if c > 1}

        def _addrs(s):
            return (a.get("address") for i in s.net_interfaces or [] for a in i.get("addresses") or [])

        def _disc_match(s, disc: str) -> bool:
            return disc == s.public_id or any(addr == disc for addr in _addrs(s))

        def _match(s) -> bool:
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
            f"{h}~{disc}"
            for h, disc in pairs
            if not any(d.hostname == h and _disc_match(d, disc) for d in all_details)
        ]
        # 어떤 서버에도 매칭 안 된 필터 토큰 (오타/불일치 안전 신호).
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
    ) -> dict:
        """통합 프로비저닝 어세스먼트(/api/assessment) — identity/reproduction/sizing/assessment/diagnostics.

        매칭/윈도우/안전경고는 get_right_sizing 과 동일(_resolve_matches 공용). right-sizing 이 자원 판정만 내는 데
        비해, per-mount 디스크 사이징 + reproduction 팩트까지 담아 재해복구/마이그레이션 타겟 생성에 필요한 것을
        한 응답으로 제공한다(계약: docs/reference/contracts/assessment-api.md). 분류/사이징은 동일 산식(값 정합).
        """
        matched_details, ambiguous, ambiguous_in_filter, unresolved, unmatched = await self._resolve_matches(
            hostnames, ips, public_ids, pairs
        )
        matched_ids = [d.id for d in matched_details]
        raws: list = []
        online_by_id: dict[int, bool] = {}
        mounts_by_id: dict = {}
        link_speeds: dict[int, dict[str, int]] = {}
        if matched_ids:
            raws = await self.repo.report_aggregate(matched_ids, period_days=window_days, end=end)
            await self._inject_net_baseline(raws, matched_ids, window_days, end)
            online_by_id = await self._online_map(matched_ids, matched_details, end)
            mounts_by_id = await self.repo.report_mount_capacity_batch(matched_ids, end)
            # inventory speed_mbps null(virtio/Windows NT5.2) 폴백용 최신 link.speed (agent 확정 규약).
            link_speeds = await self.repo.latest_link_speed(matched_ids, end - timedelta(days=window_days))
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

    async def _assemble_realtime(self, server_ids, details, now) -> EnvironmentRealtime:
        """각 서버 최신 스냅샷(get_latest_metric) 집계 — 신선한 데이터 있으면 포함(데이터 유무 = 온라인).

        표본은 최신 스냅샷 collected_at 이 신선(now-TTL 이내)한 서버만 — stale 메트릭이 현황 평균 왜곡 방지.
        online = 신선 데이터 서버 수(차트의 '그 시점 발행 서버' 기준과 동일 정렬). sample_size/total 표기.
        """
        detail_by_id = {d.id: d for d in details}
        fresh_threshold = now - timedelta(seconds=web_settings.redis_ttl_online)
        # 실시간 포화 원자료(CPU 실행큐·디스크 queue/await·메모리 paging) — 신선 표본 1쿼리 벌크(전용 경량 쿼리).
        sat_map = await self.repo.latest_saturation(server_ids, fresh_threshold)
        online = 0
        snapshots: list[dict] = []
        last_collected = None
        for sid in server_ids:
            d = detail_by_id.get(sid)
            if d is None:
                continue
            # 포화 원자료 = 벌크 sat_map 재사용(B4) — get_latest_metric 에 주입해 per-server 재조회 생략.
            sat = sat_map.get(sid) or SaturationRaw()
            m = await self.get_latest_metric(sid, saturation=sat)
            if not m or not m.collected_at or m.collected_at < fresh_threshold:
                continue  # 데이터 없음/stale = 오프라인 (통일: 데이터 신선도가 곧 온라인)
            online += 1
            mem = m.memory
            # 호스트 net rate(kBps) = 전 인터페이스 rx+tx 합 — 네트워크 혼잡 신호 저트래픽 게이트 + 부하 상위 랭킹 입력.
            net_rate = sum((n.rx_kbps or 0) + (n.tx_kbps or 0) for n in m.net_io) if m.net_io else None
            snapshots.append(
                {
                    "hostname": d.hostname,
                    "public_id": d.public_id,
                    # 부하 상위(서버별 값) — 개별 호스트 랭킹. 실시간 부하 신호만(디스크 용량은 느린 신호라 제외).
                    "cpu_pct": m.cpu.usage_pct if m.cpu else None,
                    "mem_pct": mem.usage_pct if mem else None,
                    # 실시간 포화 지수 (os-aware, >=1 포화) — 부하 상위 "실행 큐"·"응답 지연" 랭킹.
                    "cpu_sat_index": recommendation.cpu_saturation_index(
                        sat.run_queue, d.cpu_cores, d.os_family
                    ),
                    "disk_sat_index": recommendation.disk_io_saturation_index(
                        sat.await_ms, sat.pending_ops, d.os_family
                    ),
                    # 페이징 rate(major fault/s)·네트워크 rate(kB/s) — 부하 상위 랭킹 raw + 포화 도넛(불리언) 입력.
                    "paging_rate": sat.paging_major_rate,
                    "net_kbps": net_rate,
                    "mem_pressure": recommendation.mem_pressure_active(sat.paging_major_rate, d.os_family),
                    "net_congested": recommendation.net_signal_active(
                        sat.retrans_pct, sat.drop_pct, sat.conntrack_ratio, net_rate
                    ),
                    # capacity-weighted 평균 도넛용 가중치 (cpu=코어 가중, mem=절대 총량 sum/sum).
                    "cpu_cores": d.cpu_cores,
                    "mem_used_bytes": mem.used_bytes if mem else None,
                    "mem_total_bytes": mem.total_bytes if mem else None,
                }
            )
            if last_collected is None or m.collected_at > last_collected:
                last_collected = m.collected_at
        return build_environment_realtime(len(server_ids), online, snapshots, last_collected)

    async def get_dashboard_overview(self) -> EnvironmentOverview:
        """환경 개요(`/`) 집계 — 자원 적정성 분류·평균 활용률 모두 14일 표준 창(WINDOW_DAYS).

        분류(risk_donut)·활용률 게이지 전부 right-sizing 표준 창(14일)이라 서버 목록·보고서와 화면 간 정합(#E3).
        운영 신호(attention)는 실시간 현황 페이지(`get_attention_signals`)로 분리. 앵커=now(라이브 첫 화면).
        """
        now = datetime.now(UTC)
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return _empty_overview()
        details = await self.repo.get_servers(server_ids)
        # 분류 raws — 14일 표준 창(서버 목록·보고서 정합). net baseline 도 동일 창.
        raws_period = await self.repo.report_aggregate(server_ids, period_days=recommendation.WINDOW_DAYS, end=now)
        await self._inject_net_baseline(raws_period, server_ids, recommendation.WINDOW_DAYS, now)
        # 이용률·포화 도넛 6개 모두 자원 적정성 창(14일) 기준 — 분류·포화·이용률 한 창으로 통일(#E3 화면 간 정합).
        util = await self.repo.environment_utilization(period_days=recommendation.WINDOW_DAYS, end=now)
        online_by_id = await self._online_map(server_ids, details, now)
        # fleet 에러 표시자 — 전체 기간 조회(에러는 드문 이벤트라 창 제한 부적합).
        # #C5 pruning 의식적 예외(에러 delta 저비용 집계).
        errors = await self.repo.fleet_error_summary(server_ids, datetime(1970, 1, 1, tzinfo=UTC))
        return self._assemble_overview(details, util, raws_period, online_by_id, error_summary=errors)

    async def get_topology(self) -> NetworkTopology:
        """네트워크 토폴로지 — 전체 인벤토리의 L3 subnet 공동소속 그래프.

        개요(get_dashboard_overview)와 분리: 노드 규모가 커 별도 페이지(`/servers/topology`)에서 렌더.
        """
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return build_network_topology([])
        details = await self.repo.get_servers(server_ids)
        return build_network_topology(details)

    async def get_fleet_status(self) -> FleetStatus:
        """전역 상단 바 — 온라인 대수/전체 + 마지막 메트릭 수집 시각. 온라인·최신성 각각 단일 소스 경유."""
        now = datetime.now(UTC)
        server_ids = await self.repo.list_server_ids()
        if not server_ids:
            return FleetStatus(online_count=0, total_count=0, last_collected_at=None)
        details = await self.repo.get_servers(server_ids)
        online_by_id = await self._online_map(server_ids, details, now)
        online_count = sum(1 for v in online_by_id.values() if v)
        last_collected = await self.repo.latest_metric_at()
        return FleetStatus(online_count=online_count, total_count=len(server_ids), last_collected_at=last_collected)

    async def search_hosts(self, q: str, limit: int = 8) -> list[HostSearchItem]:
        """전역 호스트 검색(jump-to) — hostname 부분일치. list_servers(search) 재사용, public_id 로 이동."""
        summaries = await self.repo.list_servers(page=1, limit=limit, search=q)
        return [HostSearchItem(hostname=s.hostname, public_id=str(s.public_id), os_id=s.os_id) for s in summaries]
