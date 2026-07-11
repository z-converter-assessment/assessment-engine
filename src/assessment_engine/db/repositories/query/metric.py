"""Metric chart 도메인 concrete — dashboard snapshot · 시계열 cursor · 차트 dispatch · reboot marker.

v2: 단위 s/By, device_id/iface_id/mountpoint 안정키. child 시계열(disk_io/net_io)은 boot_time 미보유 ->
rate/CPU reset 은 GREATEST(delta,0)/d_total>0 로 흡수(boot gate 폐기). 물리/가상 필터는 types 상수(현재 no-op).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from assessment_engine import recommendation  # 순수 도메인 커널 — right-sizing 정책 상수(순환 없음)
from assessment_engine.db.dtos.outbound import (
    DashboardRaw,
    DiskIoRaw,
    ErrorFleetRaw,
    FleetErrorRaw,
    MetricPairRaw,
    MetricSeries,
    MountUsageRaw,
    NetIoRaw,
    RebootEvent,
    SaturationRaw,
)
from assessment_engine.db.models.server_disk_io import ServerDiskIo
from assessment_engine.db.models.server_filesystem import ServerFilesystem
from assessment_engine.db.models.server_inventory import ServerInventory
from assessment_engine.db.models.server_metrics import ServerMetrics
from assessment_engine.db.models.server_net_io import ServerNetIo
from assessment_engine.db.repositories.query._base import _BaseQueryMixin
from assessment_engine.db.repositories.query.base_metric import BaseMetricQueryRepository
from assessment_engine.db.repositories.query.types import (
    _AGG,
    _BUCKET_INFO,
    _CPU_NUMERATOR,
    _CPU_TOTAL_EXPR,
    _DATA_VOLUME_SQL_FILTER,
    _ENV_SCALAR_WEIGHTED,
    _PHYS_DISK_SQL_FILTER,
    _PHYS_IFACE_SQL_FILTER,
    _RATE_PER_DIM_DEFS,
    BOOT_JITTER_SEC,
    TIME_RANGE_TD,
    AggFunc,
    BucketSize,
    MetricType,
    TimeRange,
)

# table 매핑 — types.py 가 ORM import 안 하므로 여기서 __tablename__ 결합.
_RATE_PER_DIM: dict[str, tuple[str, str, str]] = {
    k: (
        (ServerDiskIo if k.startswith("disk.") else ServerNetIo).__tablename__,
        _RATE_PER_DIM_DEFS[k][0],
        _RATE_PER_DIM_DEFS[k][1],
    )
    for k in _RATE_PER_DIM_DEFS
}


class MetricQueryRepository(_BaseQueryMixin, BaseMetricQueryRepository):
    # cursor pagination 윈도우 — C5 partition pruning 하한. cursor 마다 cursor-30d 동적.
    _METRIC_SNAPSHOTS_WINDOW = timedelta(days=30)

    async def latest_dashboard(self, server_id: int) -> DashboardRaw | None:
        inv = await self.session.execute(
            select(ServerInventory.os_family, ServerInventory.kernel_version).where(ServerInventory.id == server_id)
        )
        inv_row = inv.first()
        if inv_row is None:
            return None
        os_family = inv_row[0]  # os-aware 스냅샷 포화 판정 입력 (nullable)
        kernel_version = inv_row[1]  # PSI 지원(Linux 4.20+) 판정 입력 (nullable)

        # 미래 timestamp 방어 — 시계 어긋난 agent 의 미래 collected_at 행이 "가짜 최신"으로 잡혀
        # CPU delta(연속 2행)를 깨뜨리는 것 차단 (now()+skew 상한).
        m_result = await self.session.execute(
            select(ServerMetrics)
            .where(
                ServerMetrics.server_id == server_id,
                ServerMetrics.collected_at <= text("now() + interval '2 minutes'"),
            )
            .order_by(ServerMetrics.collected_at.desc())
            .limit(2)
        )
        metrics = [
            MetricPairRaw(
                collected_at=m.collected_at,
                cpu_user_s=m.cpu_user_s,
                cpu_nice_s=m.cpu_nice_s,
                cpu_system_s=m.cpu_system_s,
                cpu_idle_s=m.cpu_idle_s,
                cpu_iowait_s=m.cpu_iowait_s,
                cpu_irq_s=m.cpu_irq_s,
                cpu_softirq_s=m.cpu_softirq_s,
                cpu_steal_s=m.cpu_steal_s,
                mem_limit_bytes=m.mem_limit_bytes,
                mem_free_bytes=m.mem_free_bytes,
                mem_available_bytes=m.mem_available_bytes,
                mem_buffered_bytes=m.mem_buffered_bytes,
                mem_cached_bytes=m.mem_cached_bytes,
                mem_used_bytes=m.mem_used_bytes,
                cpu_run_queue=m.cpu_run_queue,
                cpu_logical_count=m.cpu_logical_count,
                boot_time=m.boot_time,
                agent_started_at=m.agent_started_at,
            )
            for m in m_result.scalars().all()
        ]

        d_rows = await self._latest_per_dimension(ServerDiskIo.__tablename__, "device_id", server_id, n=2)
        disk_io = [
            DiskIoRaw(
                device_id=row.device_id,
                device_name=row.device_name,
                collected_at=row.collected_at,
                io_read_bytes=row.io_read_bytes,
                io_write_bytes=row.io_write_bytes,
                ops_read=row.ops_read,
                ops_write=row.ops_write,
                op_read_time_s=row.op_read_time_s,
                op_write_time_s=row.op_write_time_s,
                io_time_s=row.io_time_s,
                pending_ops=row.pending_ops,
            )
            for row in d_rows
        ]

        n_rows = await self._latest_per_dimension(ServerNetIo.__tablename__, "iface_id", server_id, n=2)
        net_io = [
            NetIoRaw(
                iface_id=row.iface_id,
                iface_name=row.iface_name,
                collected_at=row.collected_at,
                rx_bytes=row.rx_bytes,
                tx_bytes=row.tx_bytes,
                rx_packets=row.rx_packets,
                tx_packets=row.tx_packets,
                rx_errors=row.rx_errors,
                tx_errors=row.tx_errors,
                rx_dropped=row.rx_dropped,
                tx_dropped=row.tx_dropped,
                link_speed_bps=row.link_speed_bps,
            )
            for row in n_rows
        ]

        fs_rows = await self._latest_per_dimension(ServerFilesystem.__tablename__, "mountpoint", server_id, n=1)
        filesystems = [
            MountUsageRaw(
                mountpoint=row.mountpoint,
                used_bytes=row.used_bytes,
                free_bytes=row.free_bytes,
                inodes_used=row.inodes_used,
                inodes_free=row.inodes_free,
                device_id=row.device_id,
                fstype=row.fstype,
                collected_at=row.collected_at,
            )
            for row in fs_rows
        ]

        return DashboardRaw(
            metrics=metrics, disk_io=disk_io, net_io=net_io, filesystems=filesystems,
            os_family=os_family, kernel_version=kernel_version,
        )

    async def latest_saturation(self, server_ids: list[int], since: datetime) -> dict[int, SaturationRaw]:
        """서버별 실시간 포화 원자료 (v2, os 통일):
        - run_queue: 최신 cpu_run_queue gauge (Linux procs_running / Windows Processor Queue).
        - await_ms: server_disk_io op_time delta / ops delta (양 OS, ms). pending_ops 는 큐 폴백.
        - paging_major_rate: server_metrics paging_major delta / dt (하드폴트 rate, Linux refault / Windows).
        - retrans_pct / drop_pct / conntrack_ratio: 네트워크 품질·로컬 포화.
        - psi_cpu / psi_mem / psi_io: PSI %정체(stall_time delta / wall-time delta, server_pressure some, Linux 4.20+ / null).

        since 이후 최신 2행(delta) per server/device. reset(값-감소)은 delta<0 -> None 가드. now+2m skew 상한.
        """
        if not server_ids:
            return {}
        sql = text("""
            WITH m2 AS (
                SELECT server_id, cpu_run_queue, paging_major, net_tcp_retransmits, collected_at,
                       net_conntrack_usage, net_conntrack_limit,
                       row_number() OVER (PARTITION BY server_id ORDER BY collected_at DESC) AS rn
                FROM server_metrics
                WHERE server_id = ANY(:sids) AND collected_at >= :since AND collected_at <= now() + interval '2 minutes'
            ),
            m AS (
                SELECT server_id,
                    max(CASE WHEN rn = 1 THEN cpu_run_queue END) AS run_queue,
                    max(CASE WHEN rn = 1 THEN net_tcp_retransmits END)
                        - max(CASE WHEN rn = 2 THEN net_tcp_retransmits END) AS retrans_delta,
                    CASE WHEN max(CASE WHEN rn = 1 THEN net_conntrack_limit END) > 0
                         THEN max(CASE WHEN rn = 1 THEN net_conntrack_usage END)::float
                              / max(CASE WHEN rn = 1 THEN net_conntrack_limit END) END AS conntrack_ratio,
                    CASE WHEN max(CASE WHEN rn = 1 THEN paging_major END) >= max(CASE WHEN rn = 2 THEN paging_major END)
                              AND max(CASE WHEN rn = 1 THEN collected_at END) > max(CASE WHEN rn = 2 THEN collected_at END)
                         THEN (max(CASE WHEN rn = 1 THEN paging_major END) - max(CASE WHEN rn = 2 THEN paging_major END))::float
                              / EXTRACT(EPOCH FROM (max(CASE WHEN rn = 1 THEN collected_at END)
                                                    - max(CASE WHEN rn = 2 THEN collected_at END)))
                    END AS paging_major_rate
                FROM m2 WHERE rn <= 2 GROUP BY server_id
            ),
            d2 AS (
                SELECT server_id, device_id,
                       (COALESCE(op_read_time_s,0) + COALESCE(op_write_time_s,0)) AS t,
                       (COALESCE(ops_read,0) + COALESCE(ops_write,0)) AS ops,
                       COALESCE(io_time_s,0) AS iot, pending_ops, collected_at,
                       row_number() OVER (PARTITION BY server_id, device_id ORDER BY collected_at DESC) AS rn
                FROM server_disk_io
                WHERE server_id = ANY(:sids) AND collected_at >= :since AND collected_at <= now() + interval '2 minutes'
            ),
            dd AS (
                -- device 별 델타 — await = Δop_time / Δops, util = Δio_time / Δwall.
                SELECT server_id,
                    max(CASE WHEN rn = 1 THEN t END)   - max(CASE WHEN rn = 2 THEN t END)   AS t_delta,
                    max(CASE WHEN rn = 1 THEN ops END) - max(CASE WHEN rn = 2 THEN ops END) AS ops_delta,
                    max(CASE WHEN rn = 1 THEN iot END) - max(CASE WHEN rn = 2 THEN iot END) AS iot_delta,
                    EXTRACT(EPOCH FROM (max(CASE WHEN rn = 1 THEN collected_at END)
                                        - max(CASE WHEN rn = 2 THEN collected_at END))) AS wall,
                    max(CASE WHEN rn = 1 THEN pending_ops END) AS pending_ops
                FROM d2 WHERE rn <= 2 GROUP BY server_id, device_id
            ),
            da AS (
                -- 실제 바쁜(io_time util >= :diskio_util_min) device 만 await 채택 후 worst(MAX) — report.py 와 동일.
                -- 유휴 device 의 writeback 큐 잔류 await 폭증 억제(병목 아님).
                SELECT server_id,
                    max(CASE WHEN ops_delta > 0 AND t_delta >= 0 AND wall > 0
                                  AND iot_delta / wall >= :diskio_util_min
                             THEN t_delta::float / ops_delta * 1000 END) AS await_ms,
                    max(pending_ops) AS pending_ops
                FROM dd GROUP BY server_id
            ),
            n2 AS (
                SELECT server_id, rx_packets, tx_packets, rx_dropped, tx_dropped,
                       row_number() OVER (PARTITION BY server_id, iface_id ORDER BY collected_at DESC) AS rn
                FROM server_net_io
                WHERE server_id = ANY(:sids) AND collected_at >= :since AND collected_at <= now() + interval '2 minutes'
            ),
            nt AS (
                SELECT server_id,
                    SUM(CASE WHEN rn = 1 THEN COALESCE(tx_packets,0)+COALESCE(rx_packets,0) END)
                      - SUM(CASE WHEN rn = 2 THEN COALESCE(tx_packets,0)+COALESCE(rx_packets,0) END) AS pkt_delta,
                    SUM(CASE WHEN rn = 1 THEN COALESCE(tx_packets,0) END)
                      - SUM(CASE WHEN rn = 2 THEN COALESCE(tx_packets,0) END) AS txp_delta,
                    SUM(CASE WHEN rn = 1 THEN COALESCE(rx_dropped,0)+COALESCE(tx_dropped,0) END)
                      - SUM(CASE WHEN rn = 2 THEN COALESCE(rx_dropped,0)+COALESCE(tx_dropped,0) END) AS drop_delta
                FROM n2 WHERE rn <= 2 GROUP BY server_id
            ),
            psi AS (
                -- PSI %정체 = stall_time_s delta / wall-time delta * 100 (scope=some, resource cpu/memory/io).
                -- 에이전트는 stall_time_s(counter)를 발행(ratio_avg10 gauge 는 미발행) -> 페이징과 동형 rate 산출.
                -- Linux 4.20+ 만 행 존재 -> 미지원 OS 는 psi_* null (LEFT JOIN). reset(값-감소)·dt<=0 은 null 가드.
                SELECT server_id,
                    max(CASE WHEN resource = 'cpu'    THEN rate END) AS psi_cpu,
                    max(CASE WHEN resource = 'memory' THEN rate END) AS psi_mem,
                    max(CASE WHEN resource = 'io'     THEN rate END) AS psi_io
                FROM (
                    SELECT server_id, resource,
                        CASE WHEN s1 >= s2 AND t1 > t2
                             THEN (s1 - s2) / EXTRACT(EPOCH FROM (t1 - t2)) * 100 END AS rate
                    FROM (
                        SELECT server_id, resource,
                            max(CASE WHEN rn = 1 THEN stall_time_s END) AS s1,
                            max(CASE WHEN rn = 2 THEN stall_time_s END) AS s2,
                            max(CASE WHEN rn = 1 THEN collected_at END) AS t1,
                            max(CASE WHEN rn = 2 THEN collected_at END) AS t2
                        FROM (
                            SELECT server_id, resource, stall_time_s, collected_at,
                                   row_number() OVER (PARTITION BY server_id, resource
                                                      ORDER BY collected_at DESC) AS rn
                            FROM server_pressure
                            WHERE server_id = ANY(:sids) AND scope = 'some'
                                  AND collected_at >= :since AND collected_at <= now() + interval '2 minutes'
                        ) pp WHERE rn <= 2 GROUP BY server_id, resource
                    ) pd
                ) pr GROUP BY server_id
            )
            SELECT m.server_id, m.run_queue, m.conntrack_ratio, m.paging_major_rate, da.pending_ops,
                   psi.psi_cpu, psi.psi_mem, psi.psi_io,
                   da.await_ms AS await_ms,
                   CASE WHEN nt.txp_delta > 0 AND m.retrans_delta >= 0
                        THEN m.retrans_delta::float / nt.txp_delta * 100 END AS retrans_pct,
                   CASE WHEN nt.pkt_delta > 0 AND nt.drop_delta >= 0
                        THEN nt.drop_delta::float / nt.pkt_delta * 100 END AS drop_pct
            FROM m LEFT JOIN da ON da.server_id = m.server_id LEFT JOIN nt ON nt.server_id = m.server_id
                   LEFT JOIN psi ON psi.server_id = m.server_id
        """)
        result = await self.session.execute(
            sql, {"sids": server_ids, "since": since, "diskio_util_min": recommendation.RS_DISKIO_UTIL_MIN}
        )
        def _f(v: float | None) -> float | None:
            return float(v) if v is not None else None

        return {
            r.server_id: SaturationRaw(
                run_queue=_f(r.run_queue),
                await_ms=_f(r.await_ms),
                pending_ops=_f(r.pending_ops),
                paging_major_rate=_f(r.paging_major_rate),
                retrans_pct=_f(r.retrans_pct),
                drop_pct=_f(r.drop_pct),
                conntrack_ratio=_f(r.conntrack_ratio),
                psi_cpu=_f(r.psi_cpu),
                psi_mem=_f(r.psi_mem),
                psi_io=_f(r.psi_io),
            )
            for r in result
        }

    async def latest_errors(self, server_id: int, since: datetime) -> ErrorFleetRaw:
        """창내 에러 축 카운트 (단일 서버, bounded raw). counter delta = MAX-MIN(reset 은 >=0 자연 클램프).

        server_disk_error 는 정상 시 count=0 -> delta 0 = 정상(no_data 아님). server_metrics/net_io 는 창 안
        표본 없으면 no_data(measured=False). corrupted_bytes 는 gauge(현재값 > 0 = 메모리 손상 존재).
        """
        m = (
            await self.session.execute(
                text("""
                    SELECT COALESCE(MAX(cpu_mce) - MIN(cpu_mce), 0) AS mce,
                           COALESCE(MAX(mem_oom_kill) - MIN(mem_oom_kill), 0) AS oom,
                           count(*) AS n,
                           (SELECT mem_hardware_corrupted_bytes FROM server_metrics
                             WHERE server_id = :sid AND collected_at >= :since
                             ORDER BY collected_at DESC LIMIT 1) AS corrupted
                    FROM server_metrics
                    WHERE server_id = :sid AND collected_at >= :since
                      AND collected_at <= now() + interval '2 minutes'
                """),
                {"sid": server_id, "since": since},
            )
        ).one()
        net = (
            await self.session.execute(
                text("""
                    SELECT COALESCE(SUM(d), 0) AS net_err, count(*) AS ifaces FROM (
                        SELECT MAX(COALESCE(rx_errors,0)+COALESCE(tx_errors,0))
                             - MIN(COALESCE(rx_errors,0)+COALESCE(tx_errors,0)) AS d
                        FROM server_net_io
                        WHERE server_id = :sid AND collected_at >= :since
                          AND collected_at <= now() + interval '2 minutes'
                        GROUP BY iface_id
                    ) x
                """),
                {"sid": server_id, "since": since},
            )
        ).one()
        de = (
            await self.session.execute(
                text("""
                    SELECT COALESCE(SUM(d), 0) AS cnt,
                           COALESCE(array_agg(DISTINCT kc) FILTER (WHERE d > 0), ARRAY[]::text[]) AS kinds,
                           MAX(last_at) FILTER (WHERE d > 0) AS last_at
                    FROM (
                        SELECT error_kind || '/' || error_class AS kc,
                               MAX(count) - MIN(count) AS d, MAX(collected_at) AS last_at
                        FROM server_disk_error
                        WHERE server_id = :sid AND collected_at >= :since
                          AND collected_at <= now() + interval '2 minutes'
                        GROUP BY device_id, error_kind, error_class, member
                    ) x
                """),
                {"sid": server_id, "since": since},
            )
        ).one()
        return ErrorFleetRaw(
            measured=m.n > 0,
            net_measured=net.ifaces > 0,
            disk_err_measured=True,
            mce_count=int(m.mce or 0),
            oom_count=int(m.oom or 0),
            corrupted_bytes=int(m.corrupted) if m.corrupted is not None else None,
            net_error_count=int(net.net_err or 0),
            disk_error_count=int(de.cnt or 0),
            disk_error_kinds=list(de.kinds or []),
            last_error_at=de.last_at,
        )

    async def fleet_error_summary(self, server_ids: list[int], since: datetime) -> FleetErrorRaw:
        """전 서버 에러축 영향 호스트 수 (환경 개요 fleet 표시자). 창내 counter delta > 0(또는 corrupted 현재>0) 호스트 count."""
        if not server_ids:
            return FleetErrorRaw()
        m = (
            await self.session.execute(
                text("""
                    SELECT count(*) AS total,
                           count(*) FILTER (WHERE mce_d > 0)  AS mce_hosts,
                           count(*) FILTER (WHERE oom_d > 0)  AS oom_hosts,
                           count(*) FILTER (WHERE corrupted > 0) AS corrupted_hosts
                    FROM (
                        SELECT server_id,
                            COALESCE(MAX(cpu_mce) - MIN(cpu_mce), 0) AS mce_d,
                            COALESCE(MAX(mem_oom_kill) - MIN(mem_oom_kill), 0) AS oom_d,
                            COALESCE(MAX(mem_hardware_corrupted_bytes), 0) AS corrupted
                        FROM server_metrics
                        WHERE server_id = ANY(:sids) AND collected_at >= :since
                          AND collected_at <= now() + interval '2 minutes'
                        GROUP BY server_id
                    ) x
                """),
                {"sids": server_ids, "since": since},
            )
        ).one()
        net_hosts = (
            await self.session.execute(
                text("""
                    SELECT count(*) FILTER (WHERE net_d > 0) FROM (
                        SELECT server_id, SUM(d) AS net_d FROM (
                            SELECT server_id, MAX(COALESCE(rx_errors,0)+COALESCE(tx_errors,0))
                                            - MIN(COALESCE(rx_errors,0)+COALESCE(tx_errors,0)) AS d
                            FROM server_net_io
                            WHERE server_id = ANY(:sids) AND collected_at >= :since
                              AND collected_at <= now() + interval '2 minutes'
                            GROUP BY server_id, iface_id
                        ) y GROUP BY server_id
                    ) z
                """),
                {"sids": server_ids, "since": since},
            )
        ).scalar_one()
        disk_hosts = (
            await self.session.execute(
                text("""
                    SELECT count(DISTINCT server_id) FROM (
                        SELECT server_id, MAX(count) - MIN(count) AS d
                        FROM server_disk_error
                        WHERE server_id = ANY(:sids) AND collected_at >= :since
                          AND collected_at <= now() + interval '2 minutes'
                        GROUP BY server_id, device_id, error_kind, error_class, member
                    ) w WHERE d > 0
                """),
                {"sids": server_ids, "since": since},
            )
        ).scalar_one()
        return FleetErrorRaw(
            total=int(m.total or 0),
            mce_hosts=int(m.mce_hosts or 0),
            oom_hosts=int(m.oom_hosts or 0),
            corrupted_hosts=int(m.corrupted_hosts or 0),
            net_error_hosts=int(net_hosts or 0),
            disk_error_hosts=int(disk_hosts or 0),
        )

    async def latest_link_speed(self, server_ids: list[int], since: datetime) -> dict[int, dict[str, int]]:
        """서버·iface별 최신 link_speed_bps (bit/s gauge). assessment reproduction 의 inventory speed_mbps
        null(Windows NT5.2/virtio) 폴백용 — 엔진이 metrics network.link.speed 로 대체(agent 확정 규약)."""
        if not server_ids:
            return {}
        rows = (
            await self.session.execute(
                text("""
                    SELECT DISTINCT ON (server_id, iface_id) server_id, iface_id, link_speed_bps
                    FROM server_net_io
                    WHERE server_id = ANY(:sids) AND collected_at >= :since
                      AND collected_at <= now() + interval '2 minutes' AND link_speed_bps IS NOT NULL
                    ORDER BY server_id, iface_id, collected_at DESC
                """),
                {"sids": server_ids, "since": since},
            )
        ).all()
        out: dict[int, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r.server_id, {})[r.iface_id] = int(r.link_speed_bps)
        return out

    async def metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeries]:
        upper = cursor if cursor else datetime.now(UTC)
        lower = upper - self._METRIC_SNAPSHOTS_WINDOW
        stmt = (
            select(ServerMetrics.collected_at)
            .where(
                ServerMetrics.server_id == server_id,
                ServerMetrics.collected_at >= lower,
            )
            .order_by(ServerMetrics.collected_at.desc())
            .limit(limit)
        )
        if cursor:
            stmt = stmt.where(ServerMetrics.collected_at < cursor)
        result = await self.session.execute(stmt)
        return [MetricSeries(collected_at=ts, value=None, dimension=None) for ts in result.scalars().all()]

    async def metric_chart(
        self,
        server_id: int,
        metric_type: MetricType,
        dimension: str | None,
        time_range: TimeRange,
        bucket: BucketSize,
        agg: AggFunc,
        end: datetime | None = None,
    ) -> list[MetricSeries]:
        # 서버 상세 차트 = metric_trend(collapse=False, server_ids=[1대]) 위임.
        end_dt = end or datetime.now(UTC)
        start = end_dt - TIME_RANGE_TD[time_range]
        return await self.metric_trend(
            metric_type,
            start,
            end_dt,
            bucket,
            server_ids=[server_id],
            agg=agg,
            dimension=dimension,
            collapse=False,
        )

    async def metric_trend(
        self,
        metric_type: str,
        start: datetime,
        end: datetime,
        bucket: BucketSize,
        server_ids: list[int] | None = None,
        agg: str = "avg",
        dimension: str | None = None,
        collapse: bool = True,
    ) -> list[MetricSeries]:
        """통일 시계열 — 각 collected_at 마다 환경값 1개 -> time_bucket agg(avg/max/p95).

        시점값 = 활용률 sum(num)/sum(den), 처리량 sum(rate), run_queue sum/sum(cores). server_ids=None 전체·
        [1대]=서버 상세·[N]=선택. collapse=False 면 device/iface/mount dimension 보존(멀티라인).
        v2: child 시계열 boot_time 부재 -> rate reset 은 GREATEST(delta,0), CPU reset 은 d_total>0 로 흡수.
        """
        bi, bucket_td = _BUCKET_INFO[bucket]
        ae = _AGG[agg]
        sid = "AND server_id = ANY(:server_ids)" if server_ids else ""
        params: dict = {"start": start, "end": end}

        if metric_type in _CPU_NUMERATOR:
            num = _CPU_NUMERATOR[metric_type]
            sql = text(f"""
                WITH raw AS (
                    SELECT collected_at, server_id,
                        {num} AS num_s, {_CPU_TOTAL_EXPR} AS total_s
                    FROM {ServerMetrics.__tablename__}
                    WHERE collected_at >= :window_start AND collected_at <= :end {sid}
                ),
                deltas AS (
                    SELECT collected_at,
                        num_s   - LAG(num_s)   OVER w AS d_num,
                        total_s - LAG(total_s) OVER w AS d_total
                    FROM raw WINDOW w AS (PARTITION BY server_id ORDER BY collected_at)
                ),
                valid AS (
                    SELECT collected_at, d_num, d_total FROM deltas
                    WHERE collected_at >= :start AND d_total > 0 AND d_num >= 0
                ),
                per_ts AS (
                    SELECT collected_at, SUM(d_num) * 100.0 / SUM(d_total) AS v
                    FROM valid GROUP BY collected_at HAVING SUM(d_total) > 0
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_ts GROUP BY ts ORDER BY ts
            """)
            params["window_start"] = start - bucket_td
        elif metric_type in _ENV_SCALAR_WEIGHTED:
            num, den, guard = _ENV_SCALAR_WEIGHTED[metric_type]
            ratio = f"SUM({num})::float / NULLIF(SUM({den}), 0) * 100"
            sql = text(f"""
                WITH per_ts AS (
                    SELECT collected_at, {ratio} AS v
                    FROM {ServerMetrics.__tablename__}
                    WHERE collected_at >= :start AND collected_at <= :end {sid} AND {guard}
                    GROUP BY collected_at
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
            """)
        elif metric_type == "cpu.run_queue":
            # 실행 큐/코어 os-aware — v2 단일 cpu_run_queue(Linux procs_running / Windows Processor Queue).
            # 항상 os_family dimension(Linux/Windows 2선). capacity-weighted SUM(run_queue)/SUM(cpu_cores).
            sid_sm = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
            sql = text(f"""
                WITH per_ts AS (
                    SELECT sm.collected_at, si.os_family AS dim,
                        SUM(sm.cpu_run_queue)::float / NULLIF(SUM(si.cpu_cores), 0) AS v
                    FROM {ServerMetrics.__tablename__} sm
                    JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
                    WHERE sm.collected_at >= :start AND sm.collected_at <= :end {sid_sm}
                      AND sm.cpu_run_queue IS NOT NULL AND si.cpu_cores > 0
                    GROUP BY sm.collected_at, si.os_family
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, dim AS dimension, NULL::text AS kind
                FROM per_ts WHERE v IS NOT NULL GROUP BY ts, dim ORDER BY ts
            """)
        elif metric_type == "disk.io_saturation":
            # 디스크 I/O 포화 — v2 await(ms) 양 OS 통일. device 별 Δop_time/Δops, io_time util >= min 인
            # 실제 바쁜 device 만 채택 후 worst(MAX) — report.py·disk_io_saturated 동일(유휴 device writeback await 억제).
            # child 시계열 boot_time 부재 -> reset 은 GREATEST(delta,0). 단일선(os 분기 없음).
            sid_dio = "AND server_id = ANY(:server_ids)" if server_ids else ""
            sql = text(f"""
                WITH l_raw AS (
                    SELECT collected_at, server_id, device_id,
                        (COALESCE(op_read_time_s,0) + COALESCE(op_write_time_s,0)) AS t,
                        (COALESCE(ops_read,0) + COALESCE(ops_write,0)) AS ops,
                        COALESCE(io_time_s,0) AS iot
                    FROM {ServerDiskIo.__tablename__}
                    WHERE collected_at >= :window_start AND collected_at <= :end {sid_dio} AND {_PHYS_DISK_SQL_FILTER}
                ),
                l_delta AS (
                    SELECT collected_at,
                        GREATEST(t   - LAG(t)   OVER w, 0) AS d_t,
                        GREATEST(ops - LAG(ops) OVER w, 0) AS d_ops,
                        GREATEST(iot - LAG(iot) OVER w, 0) AS d_iot,
                        EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS d_wall
                    FROM l_raw WINDOW w AS (PARTITION BY server_id, device_id ORDER BY collected_at)
                ),
                per_dev AS (
                    SELECT collected_at,
                        CASE WHEN d_ops > 0 AND d_wall > 0 AND d_iot / d_wall >= :diskio_util_min
                             THEN d_t::float / d_ops * 1000 END AS await_ms
                    FROM l_delta WHERE collected_at >= :start
                ),
                per_ts AS (
                    SELECT collected_at, MAX(await_ms) AS v FROM per_dev GROUP BY collected_at
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
            """)
            params["window_start"] = start - bucket_td
            params["diskio_util_min"] = recommendation.RS_DISKIO_UTIL_MIN
        elif metric_type == "net.retrans_percent":
            # TCP 재전송율 % = Σ(Δtcp_retrans) / Σ(Δtx_packets) * 100. reset 은 GREATEST(Δ,0).
            sid_sm = "AND server_id = ANY(:server_ids)" if server_ids else ""
            sql = text(f"""
                WITH retrans_ts AS (
                    SELECT collected_at, SUM(d) AS retrans FROM (
                        SELECT collected_at,
                            GREATEST(net_tcp_retransmits - LAG(net_tcp_retransmits)
                                     OVER (PARTITION BY server_id ORDER BY collected_at), 0) AS d
                        FROM {ServerMetrics.__tablename__}
                        WHERE collected_at >= :window_start AND collected_at <= :end {sid_sm}
                    ) x WHERE d IS NOT NULL GROUP BY collected_at
                ),
                txp_ts AS (
                    SELECT collected_at, SUM(d) AS txp FROM (
                        SELECT collected_at,
                            GREATEST(tx_packets - LAG(tx_packets)
                                     OVER (PARTITION BY server_id, iface_id ORDER BY collected_at), 0) AS d
                        FROM {ServerNetIo.__tablename__}
                        WHERE {_PHYS_IFACE_SQL_FILTER}
                          AND collected_at >= :window_start AND collected_at <= :end {sid_sm}
                    ) y WHERE d IS NOT NULL GROUP BY collected_at
                ),
                per_ts AS (
                    SELECT r.collected_at, r.retrans::float / NULLIF(t.txp, 0) * 100 AS v
                    FROM retrans_ts r JOIN txp_ts t USING (collected_at)
                    WHERE r.collected_at >= :start
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
            """)
            params["window_start"] = start - bucket_td
        elif metric_type in ("cpu.psi", "mem.psi", "disk.psi"):
            # PSI %정체 추이 = Σ(Δstall_time_s) / Σ(Δwall-time) * 100 (scope=some, resource 매핑).
            # server_pressure (server, resource)별 stall_time_s counter. reset 은 GREATEST(Δ,0). 단일선.
            # Linux 4.20+ 만 행 존재 -> 미지원 OS(Windows)는 빈 결과(차트 empty state).
            psi_resource = {"cpu.psi": "cpu", "mem.psi": "memory", "disk.psi": "io"}[metric_type]
            sid_psi = "AND server_id = ANY(:server_ids)" if server_ids else ""
            sql = text(f"""
                WITH l_raw AS (
                    SELECT collected_at, server_id, stall_time_s AS s
                    FROM server_pressure
                    WHERE resource = :psi_resource AND scope = 'some'
                      AND collected_at >= :window_start AND collected_at <= :end {sid_psi}
                ),
                l_delta AS (
                    SELECT collected_at,
                        GREATEST(s - LAG(s) OVER w, 0) AS d_s,
                        EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
                    FROM l_raw WINDOW w AS (PARTITION BY server_id ORDER BY collected_at)
                ),
                per_ts AS (
                    SELECT collected_at, SUM(d_s)::float / NULLIF(SUM(dt), 0) * 100 AS v
                    FROM l_delta WHERE collected_at >= :start AND dt > 0
                    GROUP BY collected_at
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
            """)
            params["window_start"] = start - bucket_td
            params["psi_resource"] = psi_resource
        elif metric_type == "fs.usage_percent":
            if collapse:
                sql = text(f"""
                    WITH per_ts AS (
                        SELECT collected_at,
                            SUM(used_bytes)::float / NULLIF(SUM(used_bytes + free_bytes), 0) * 100 AS v
                        FROM {ServerFilesystem.__tablename__}
                        WHERE collected_at >= :start AND collected_at <= :end {sid}
                          AND {_DATA_VOLUME_SQL_FILTER}
                          AND used_bytes IS NOT NULL AND free_bytes IS NOT NULL AND (used_bytes + free_bytes) > 0
                        GROUP BY collected_at
                    )
                    SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
                    FROM per_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
                """)
            else:
                sql = text(f"""
                    WITH per_ts AS (
                        SELECT collected_at, mountpoint AS dim,
                            SUM(used_bytes)::float / NULLIF(SUM(used_bytes + free_bytes), 0) * 100 AS v
                        FROM {ServerFilesystem.__tablename__}
                        WHERE collected_at >= :start AND collected_at <= :end {sid}
                          AND {_DATA_VOLUME_SQL_FILTER}
                          AND used_bytes IS NOT NULL AND free_bytes IS NOT NULL AND (used_bytes + free_bytes) > 0
                          AND (CAST(:dim_filter AS text) IS NULL OR mountpoint = :dim_filter)
                        GROUP BY collected_at, mountpoint
                    )
                    SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, dim AS dimension, NULL::text AS kind
                    FROM per_ts WHERE v IS NOT NULL GROUP BY ts, dim ORDER BY ts, dim
                """)
                params["dim_filter"] = dimension
        elif metric_type in _RATE_PER_DIM_DEFS:
            table, dim_col, value_col = _RATE_PER_DIM[metric_type]
            if collapse:
                dev_filter = _PHYS_DISK_SQL_FILTER if dim_col == "device_id" else _PHYS_IFACE_SQL_FILTER
                dim_sel, dim_grp, out_dim = "", "", "NULL::text"
            else:
                dev_filter = f"(CAST(:dim_filter AS text) IS NULL OR {dim_col} = :dim_filter)"
                dim_sel, dim_grp, out_dim = ", dim", ", dim", "dim"
                params["dim_filter"] = dimension
            sql = text(f"""
                WITH raw AS (
                    SELECT collected_at, server_id, {dim_col} AS dim, {value_col} AS cnt
                    FROM {table}
                    WHERE collected_at >= :window_start AND collected_at <= :end {sid} AND {dev_filter}
                ),
                deltas AS (
                    SELECT collected_at, dim,
                        GREATEST(cnt - LAG(cnt) OVER w, 0) AS d_val,
                        EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
                    FROM raw WINDOW w AS (PARTITION BY server_id, dim ORDER BY collected_at)
                ),
                rates AS (
                    SELECT collected_at, dim,
                        CASE WHEN dt IS NULL OR dt <= 0 OR d_val IS NULL THEN NULL ELSE d_val / dt END AS v
                    FROM deltas WHERE collected_at >= :start
                ),
                per_ts AS (
                    SELECT collected_at{dim_sel}, SUM(v) AS v
                    FROM rates WHERE v IS NOT NULL GROUP BY collected_at{dim_grp}
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, {out_dim} AS dimension, NULL::text AS kind
                FROM per_ts GROUP BY ts{dim_grp} ORDER BY ts{dim_grp}
            """)
            params["window_start"] = start - bucket_td
        else:
            raise AssertionError(f"unsupported metric_type {metric_type!r}")

        if server_ids:
            params["server_ids"] = server_ids
        result = await self.session.execute(sql, params)
        return [
            MetricSeries(collected_at=row.ts, value=row.value, dimension=row.dimension, kind=row.kind)
            for row in result.all()
        ]

    async def reboot_events(
        self,
        server_id: int,
        start: datetime,
        end: datetime,
    ) -> list[RebootEvent]:
        """server_inventory_history에서 boot_time / agent_started_at 변경 시점 추출.

        history는 변경 trigger 시 행이 추가되므로 행 자체가 변경 이벤트. LAG 비교로 boot_time /
        agent_started_at 변경만 필터. range 시작 직전 1행도 LAG 베이스로 포함. NULL-safe 는 IS DISTINCT FROM.
        """
        sql = text("""
            WITH base AS (
                SELECT collected_at, boot_time, agent_started_at,
                    LAG(boot_time)        OVER (ORDER BY collected_at) AS prev_boot,
                    LAG(agent_started_at) OVER (ORDER BY collected_at) AS prev_agent
                FROM server_inventory_history
                WHERE server_id = :sid
                  AND collected_at <= :end
                  AND collected_at >= :buffer_start
            )
            SELECT collected_at, boot_time, agent_started_at,
                CASE
                    WHEN prev_boot IS NULL                            THEN 'reboot'
                    WHEN ABS(EXTRACT(EPOCH FROM (boot_time - prev_boot))) > :jitter_sec THEN 'reboot'
                    WHEN agent_started_at IS DISTINCT FROM prev_agent THEN 'restart'
                    ELSE NULL
                END AS kind
            FROM base
            WHERE collected_at >= :start
              AND (
                  prev_boot IS NULL
                  OR ABS(EXTRACT(EPOCH FROM (boot_time - prev_boot))) > :jitter_sec
                  OR agent_started_at IS DISTINCT FROM prev_agent
              )
            ORDER BY collected_at
        """)
        result = await self.session.execute(
            sql,
            {
                "sid": server_id,
                "start": start,
                "end": end,
                "buffer_start": start - timedelta(days=30),
                "jitter_sec": BOOT_JITTER_SEC,
            },
        )
        return [
            RebootEvent(
                collected_at=row.collected_at,
                boot_time=row.boot_time,
                agent_started_at=row.agent_started_at,
                kind=row.kind,
            )
            for row in result.all()
            if row.kind is not None
        ]
