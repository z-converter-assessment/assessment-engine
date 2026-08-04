"""Metric chart 도메인 concrete — dashboard snapshot · 시계열 cursor · 차트 dispatch · reboot marker.

v2: 단위 s/By, device_id/iface_id/mountpoint 안정키. child 시계열(disk_io/net_io)은 boot_time 미보유 ->
rate/CPU reset 은 GREATEST(delta,0)/d_total>0 로 흡수(boot gate 폐기). 물리/가상 필터는 types 상수(현재 no-op).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from assessment_engine import recommendation  # 순수 도메인 커널 — right-sizing 정책 상수(순환 없음)
from assessment_engine.db.dtos.outbound import (
    CpuCoreRaw,
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
from assessment_engine.db.models.server_cpu_core import ServerCpuCore
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
    EnvironmentMetricType,
    MetricType,
    TimeRange,
)
from assessment_engine.json_types import JsonObject

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
            select(
                ServerInventory.os_family,
                ServerInventory.kernel_version,
                ServerInventory.block_devices,
                ServerInventory.net_interfaces,
            ).where(ServerInventory.id == server_id)
        )
        inv_row = inv.first()
        if inv_row is None:
            return None
        os_family = inv_row[0]  # os-aware 스냅샷 포화 판정 입력 (nullable)
        kernel_version = inv_row[1]  # PSI 지원(Linux 4.20+) 판정 입력 (nullable)
        block_devices = inv_row[2]  # 물리 디스크 필터 입력 (I/O 활동 축, nullable)
        net_interfaces = inv_row[3]  # 물리 인터페이스 필터 입력 (nullable)

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
                cpu_blocked=m.cpu_blocked,
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

        c_rows = await self._latest_per_dimension(ServerCpuCore.__tablename__, "core_id", server_id, n=2)
        cpu_cores = [
            CpuCoreRaw(
                core_id=row.core_id,
                collected_at=row.collected_at,
                cpu_user_s=row.cpu_user_s,
                cpu_nice_s=row.cpu_nice_s,
                cpu_system_s=row.cpu_system_s,
                cpu_idle_s=row.cpu_idle_s,
                cpu_iowait_s=row.cpu_iowait_s,
                cpu_irq_s=row.cpu_irq_s,
                cpu_softirq_s=row.cpu_softirq_s,
                cpu_steal_s=row.cpu_steal_s,
            )
            for row in c_rows
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
            block_devices=block_devices, net_interfaces=net_interfaces,
            cpu_cores=cpu_cores,
        )

    async def latest_saturation(self, server_ids: list[int], since: datetime) -> dict[int, SaturationRaw]:
        """서버별 실시간 포화 원자료 (v2, os 통일):
        - run_queue: 최신 cpu_run_queue gauge (Linux procs_running / Windows Processor Queue).
        - await_ms: server_disk_io op_time delta / ops delta (양 OS, ms, 물리 disk only — `_PHYS_DISK_SQL_FILTER`
          fail-closed, chart/report_aggregate 와 동일). pending_ops 는 큐 폴백.
        - disk_io_util_pct: 물리 disk worst device io_time delta / wall-time delta * 100 (USE Method Utilization
          축, 0% 도 실측). 합성/숨김 pseudo-device(예: Windows aggregate:system) 제외 — 실측치인 척 대체하면
          진짜 물리 device 카운터 이상(phantom busy 등)이 은폐된다.
        - paging_major_rate: 하드폴트 rate, os-aware 컬럼 선택 — Linux paging_major(refault) / Windows paging_in
          (Pages Input/sec. Windows 는 paging.operations 를 direction=in 만 발행, type=major 포인트 없음 —
          paging_major 컬럼은 Windows 에서 항상 NULL, report_aggregate pages_input_rate 산식과 동일 소스 통일).
        - retrans_pct / drop_pct / conntrack_ratio: 네트워크 품질·로컬 포화.
        - psi_cpu / psi_mem / psi_io: PSI %정체(stall_time delta / wall-time delta, server_pressure some, Linux 4.20+ / null).

        since 이후 최신 2행(delta) per server/device. reset(값-감소)은 delta<0 -> None 가드. now+2m skew 상한.
        """
        if not server_ids:
            return {}
        sql = text(f"""
            WITH m2 AS (
                SELECT sm.server_id, sm.cpu_run_queue,
                       CASE WHEN si.os_family = 'windows' THEN sm.paging_in ELSE sm.paging_major END AS paging_val,
                       sm.net_tcp_retransmits, sm.collected_at,
                       sm.net_conntrack_usage, sm.net_conntrack_limit,
                       row_number() OVER (PARTITION BY sm.server_id ORDER BY sm.collected_at DESC) AS rn
                FROM server_metrics sm
                JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
                WHERE sm.server_id = ANY(:sids) AND sm.collected_at >= :since
                      AND sm.collected_at <= now() + interval '2 minutes'
            ),
            m AS (
                SELECT server_id,
                    max(CASE WHEN rn = 1 THEN cpu_run_queue END) AS run_queue,
                    max(CASE WHEN rn = 1 THEN net_tcp_retransmits END)
                        - max(CASE WHEN rn = 2 THEN net_tcp_retransmits END) AS retrans_delta,
                    CASE WHEN max(CASE WHEN rn = 1 THEN net_conntrack_limit END) > 0
                         THEN max(CASE WHEN rn = 1 THEN net_conntrack_usage END)::float
                              / max(CASE WHEN rn = 1 THEN net_conntrack_limit END) END AS conntrack_ratio,
                    CASE WHEN max(CASE WHEN rn = 1 THEN paging_val END) >= max(CASE WHEN rn = 2 THEN paging_val END)
                              AND max(CASE WHEN rn = 1 THEN collected_at END) > max(CASE WHEN rn = 2 THEN collected_at END)
                         THEN (max(CASE WHEN rn = 1 THEN paging_val END) - max(CASE WHEN rn = 2 THEN paging_val END))::float
                              / EXTRACT(EPOCH FROM (max(CASE WHEN rn = 1 THEN collected_at END)
                                                    - max(CASE WHEN rn = 2 THEN collected_at END)))
                    END AS paging_major_rate
                FROM m2 WHERE rn <= 2 GROUP BY server_id
            ),
            d2 AS (
                -- 물리 disk 만(합성/숨김 pseudo-device 제외, chart/report_aggregate 와 동일 fail-closed 필터) —
                -- 미필터면 Windows aggregate:system 같은 pseudo-device 가 실측 0%/await 로 위장해 진짜 물리
                -- 디바이스(PhysicalDrive0 등) 카운터 이상 시 그걸로 대체돼 버림(오탐 은폐, N/A 여야 할 게 값으로 보임).
                SELECT server_id, device_id,
                       (COALESCE(op_read_time_s,0) + COALESCE(op_write_time_s,0)) AS t,
                       (COALESCE(ops_read,0) + COALESCE(ops_write,0)) AS ops,
                       COALESCE(io_time_s,0) AS iot, pending_ops, collected_at,
                       row_number() OVER (PARTITION BY server_id, device_id ORDER BY collected_at DESC) AS rn
                FROM server_disk_io
                WHERE server_id = ANY(:sids) AND collected_at >= :since AND collected_at <= now() + interval '2 minutes'
                      AND {_PHYS_DISK_SQL_FILTER}
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
                -- disk_io_util_pct(Δio_time/Δwall*100) 는 worst(MAX) device 채택 — 유휴(0%)도 실측값이라 await 같은
                -- util 하한 게이트는 없음. 단 ops_delta > 0 요구(await 와 동일 원칙) — 연산 0건인데 io_time 만 증가하는
                -- 건 물리적으로 모순(구세대 virtio 드라이버 phantom busy 카운터 실측, 완료 연산 없이 바쁨 시간만 누적) —
                -- 그대로 보이면 오탐이라 그 device 는 미측정 처리. d2 가 이미 물리 disk only(_PHYS_DISK_SQL_FILTER)라
                -- 합성 pseudo-device 로 위장 대체될 일은 없음 — 유일한 물리 device 가 phantom busy 면 host 전체 None.
                -- iot_delta < 0(카운터 리셋/역행) 또는 iot_delta > wall(busy 시간이 경과시간을 초과 — agent-data.md
                -- disk.io_time 계약상 %util busy 분율은 [0, wall] 이어야 정상, 초과는 카운터 이상)도 None 가드 —
                -- await 와 동일 원칙(reset·overflow 흡수).
                -- pending_ops(await 폴백, Windows 전용)도 동일 신뢰 조건 요구 — io_time 카운터가 깨진 device 는
                -- io_time 만 못 믿는 게 아니라 그 device 자체를 못 믿는다(보수적 원칙). 게이트 없이 max(pending_ops)
                -- 그대로 쓰면 phantom busy/overflow device 의 다른 카운터(대기열 깊이)까지 진짜인 척 새어나가
                -- disk_io_saturation_index 폴백을 오염시킨다.
                SELECT server_id,
                    max(CASE WHEN ops_delta > 0 AND t_delta >= 0 AND wall > 0
                                  AND iot_delta / wall >= :diskio_util_min AND iot_delta <= wall
                             THEN t_delta::float / ops_delta * 1000 END) AS await_ms,
                    max(CASE WHEN ops_delta > 0 AND wall > 0 AND iot_delta >= 0 AND iot_delta <= wall
                             THEN iot_delta / wall * 100 END) AS disk_io_util_pct,
                    max(CASE WHEN ops_delta > 0 AND wall > 0 AND iot_delta >= 0 AND iot_delta <= wall
                             THEN pending_ops END) AS pending_ops
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
                   da.await_ms AS await_ms, da.disk_io_util_pct AS disk_io_util_pct,
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
                disk_io_util_pct=_f(r.disk_io_util_pct),
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
                        SELECT error_kind || CASE WHEN error_class = '' THEN '' ELSE '/' || error_class END AS kc,
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

    async def fleet_error_hosts(self, server_ids: list[int], since: datetime) -> set[int]:
        """에러 발생 server_id 집합 (서버 목록 "운영 이벤트" 칼럼). 5축(mce·oom·corrupted·net·disk) 중
        하나라도 창내 counter delta > 0(또는 corrupted 현재>0)이면 포함 — fleet_error_summary 와 동일 소스·delta.

        #C5 예외: since=epoch 전체기간 스캔이나 에러 delta 는 저비용(fleet_error_summary 와 동일 예외).
        3 소스 각각 SELECT 후 Python 합집합.
        """
        if not server_ids:
            return set()
        hosts: set[int] = set()
        m_rows = (
            await self.session.execute(
                text("""
                    SELECT server_id FROM (
                        SELECT server_id,
                            COALESCE(MAX(cpu_mce) - MIN(cpu_mce), 0) AS mce_d,
                            COALESCE(MAX(mem_oom_kill) - MIN(mem_oom_kill), 0) AS oom_d,
                            COALESCE(MAX(mem_hardware_corrupted_bytes), 0) AS corrupted
                        FROM server_metrics
                        WHERE server_id = ANY(:sids) AND collected_at >= :since
                          AND collected_at <= now() + interval '2 minutes'
                        GROUP BY server_id
                    ) x
                    WHERE mce_d > 0 OR oom_d > 0 OR corrupted > 0
                """),
                {"sids": server_ids, "since": since},
            )
        ).scalars()
        hosts.update(m_rows)
        net_rows = (
            await self.session.execute(
                text("""
                    SELECT server_id FROM (
                        SELECT server_id, SUM(d) AS net_d FROM (
                            SELECT server_id, MAX(COALESCE(rx_errors,0)+COALESCE(tx_errors,0))
                                            - MIN(COALESCE(rx_errors,0)+COALESCE(tx_errors,0)) AS d
                            FROM server_net_io
                            WHERE server_id = ANY(:sids) AND collected_at >= :since
                              AND collected_at <= now() + interval '2 minutes'
                            GROUP BY server_id, iface_id
                        ) y GROUP BY server_id
                    ) z WHERE net_d > 0
                """),
                {"sids": server_ids, "since": since},
            )
        ).scalars()
        hosts.update(net_rows)
        disk_rows = (
            await self.session.execute(
                text("""
                    SELECT DISTINCT server_id FROM (
                        SELECT server_id, MAX(count) - MIN(count) AS d
                        FROM server_disk_error
                        WHERE server_id = ANY(:sids) AND collected_at >= :since
                          AND collected_at <= now() + interval '2 minutes'
                        GROUP BY server_id, device_id, error_kind, error_class, member
                    ) w WHERE d > 0
                """),
                {"sids": server_ids, "since": since},
            )
        ).scalars()
        hosts.update(disk_rows)
        return {int(h) for h in hosts}

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
        collapse: bool = False,
    ) -> list[MetricSeries]:
        # 서버 상세 차트 = metric_trend(server_ids=[1대]) 위임. collapse=True — 물리 디바이스/마운트 수와
        # 무관하게 1대 서버 내에서 dimension 합산 1선(스토리지 IOPS·처리량 추이 — 디바이스 많으면 멀티라인
        # 지저분해지는 문제, 환경 합산과 동일 SQL 재사용). collapse=False(기본) 는 기존 dimension 별 멀티라인.
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
            collapse=collapse,
        )

    async def metric_trend(
        self,
        metric_type: MetricType | EnvironmentMetricType,
        start: datetime,
        end: datetime,
        bucket: BucketSize,
        server_ids: list[int] | None = None,
        agg: AggFunc = "avg",
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
        params: JsonObject = {"start": start, "end": end}

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
        elif metric_type == "cpu.saturation_hosts":
            # 실행 큐 포화 서버 수 — cpu_saturation_index(값/os별 임계, >=1.0 포화)와 동일 판정이지만 환경 집계는
            # 메모리 압박 서버 수와 일관되게 "판정 crossing 서버 수"(count)로 통일 — 연속 지수(강도)보다 카운트가
            # 도메인 지식(임계 의미) 없이 바로 읽히고 "몇 대 봐야 하는지"를 직접 답해 운영상 더 실행 가능함.
            # Linux(procs_running, 임계1.0)·Windows(Processor Queue, 임계2.0) — "윈도우 정규화 보정".
            # 버킷 먼저 묶고 그 안에서 server 별 "한 번이라도 넘었는지"(bool_or) 후 distinct server 수 — raw
            # collected_at 별로 먼저 세고 avg 내면 서버들이 비동기 보고라 매 시점 사실상 1대만 잡혀(다른 서버는
            # 그 시점에 값이 없음) 3/7 같은 소수 카운트가 나온다(오류). 버킷 우선이라 항상 정수.
            sid_sm = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
            params["procs_running_threshold"] = recommendation.PROCS_RUNNING_PER_CORE_SATURATION
            params["cpu_run_queue_threshold"] = recommendation.CPU_RUN_QUEUE_PER_CORE_SATURATION
            sql = text(f"""
                WITH flags AS (
                    SELECT sm.collected_at, sm.server_id,
                        (sm.cpu_run_queue::float / si.cpu_cores)
                        / CASE WHEN si.os_family = 'windows' THEN (:cpu_run_queue_threshold)::float
                               ELSE (:procs_running_threshold)::float END >= 1.0 AS crossed
                    FROM {ServerMetrics.__tablename__} sm
                    JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
                    WHERE sm.collected_at >= :start AND sm.collected_at <= :end {sid_sm}
                      AND sm.cpu_run_queue IS NOT NULL AND si.cpu_cores > 0
                ),
                per_bucket AS (
                    SELECT time_bucket(interval '{bi}', collected_at) AS ts, server_id, bool_or(crossed) AS ever
                    FROM flags GROUP BY ts, server_id
                )
                SELECT ts, COUNT(*) FILTER (WHERE ever) AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_bucket GROUP BY ts ORDER BY ts
            """)
        elif metric_type == "cpu.saturation":
            # CPU 실행 큐 포화 여부(서버 상세, 이진 0/1) — cpu.saturation_hosts(환경, crossing 서버 수)와 동일
            # 원자료·임계, 서버 1대 단일 시계열로 축소: recommendation.cpu_saturated 동일 판정 — Linux
            # (procs_running/core, 임계 1.0)·Windows(Processor Queue/core, 임계 2.0, "윈도우 정규화 보정").
            sid_sm = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
            params["procs_running_threshold"] = recommendation.PROCS_RUNNING_PER_CORE_SATURATION
            params["cpu_run_queue_threshold"] = recommendation.CPU_RUN_QUEUE_PER_CORE_SATURATION
            sql = text(f"""
                WITH flags AS (
                    SELECT sm.collected_at,
                        (sm.cpu_run_queue::float / si.cpu_cores)
                        / CASE WHEN si.os_family = 'windows' THEN (:cpu_run_queue_threshold)::float
                               ELSE (:procs_running_threshold)::float END >= 1.0 AS crossed
                    FROM {ServerMetrics.__tablename__} sm
                    JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
                    WHERE sm.collected_at >= :start AND sm.collected_at <= :end {sid_sm}
                      AND sm.cpu_run_queue IS NOT NULL AND si.cpu_cores > 0
                ),
                per_bucket AS (
                    SELECT time_bucket(interval '{bi}', collected_at) AS ts, bool_or(crossed) AS ever
                    FROM flags GROUP BY ts
                )
                SELECT ts, (CASE WHEN ever THEN 1.0 ELSE 0.0 END) AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_bucket ORDER BY ts
            """)
        elif metric_type == "cpu.blocked":
            # D-state 블록(IO 대기 근본원인) gauge — Linux 전용(cpu_blocked null 인 Windows 행은 자연 제외).
            # 실행 큐와 달리 코어 정규화 없음(원자값 그대로, 실시간 스냅샷과 동일 단위).
            sid_sm = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
            sql = text(f"""
                WITH per_ts AS (
                    SELECT sm.collected_at, si.os_family AS dim, AVG(sm.cpu_blocked) AS v
                    FROM {ServerMetrics.__tablename__} sm
                    JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
                    WHERE sm.collected_at >= :start AND sm.collected_at <= :end {sid_sm}
                      AND sm.cpu_blocked IS NOT NULL
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
        elif metric_type == "disk.saturation_hosts":
            # 디스크 I/O 포화 서버 수 — disk.io_saturation(worst-device await, 환경 단일 MAX선)과 동일 판정
            # 임계(RS_DISKIO_AWAIT_MS)를 서버별로 적용해 "판정 crossing 서버 수"(count)로 집계 — CPU 실행 큐·
            # 메모리 페이징과 동형(도메인 지식 없이 바로 읽히고, MAX 단일선보다 문제의 확산 범위가 드러남).
            # 물리 disk only(_PHYS_DISK_SQL_FILTER) + 카운터 신뢰 조건(ops_delta>0, iot_delta 가 [0, wall]
            # 범위 — phantom busy/reset/overflow 가드, 실시간현황 latest_saturation 과 동일 원칙) 적용.
            # 버킷 우선 bool_or count(cpu.saturation_hosts·mem.paging_pressure_hosts 와 동형, 항상 정수) —
            # raw per_ts 먼저 세면 서버 비동기 보고라 소수 카운트 오류(오늘 발견·수정한 버그와 동일 원인).
            sid_dio = "AND server_id = ANY(:server_ids)" if server_ids else ""
            params["window_start"] = start - bucket_td
            params["diskio_util_min"] = recommendation.RS_DISKIO_UTIL_MIN
            params["diskio_await_ms"] = recommendation.RS_DISKIO_AWAIT_MS
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
                    SELECT collected_at, server_id,
                        t   - LAG(t)   OVER w AS d_t,
                        ops - LAG(ops) OVER w AS d_ops,
                        iot - LAG(iot) OVER w AS d_iot,
                        EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS d_wall
                    FROM l_raw WINDOW w AS (PARTITION BY server_id, device_id ORDER BY collected_at)
                ),
                per_dev AS (
                    SELECT collected_at, server_id,
                        CASE WHEN d_ops > 0 AND d_wall > 0 AND d_iot >= 0 AND d_iot <= d_wall
                                  AND d_iot / d_wall >= :diskio_util_min
                             THEN d_t::float / d_ops * 1000 END AS await_ms
                    FROM l_delta WHERE collected_at >= :start
                ),
                flags AS (
                    SELECT collected_at, server_id,
                        bool_or(await_ms > (:diskio_await_ms)::float) AS crossed
                    FROM per_dev GROUP BY collected_at, server_id
                ),
                per_bucket AS (
                    SELECT time_bucket(interval '{bi}', collected_at) AS ts, server_id, bool_or(crossed) AS ever
                    FROM flags GROUP BY ts, server_id
                )
                SELECT ts, COUNT(*) FILTER (WHERE ever) AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_bucket GROUP BY ts ORDER BY ts
            """)
        elif metric_type == "disk.saturation":
            # 디스크 I/O 포화 여부(서버 상세, 이진 0/1) — disk.saturation_hosts(환경, crossing 서버 수)와 동일
            # 원자료·임계(RS_DISKIO_AWAIT_MS), 서버 1대 단일 시계열로 축소. 물리 disk only(_PHYS_DISK_SQL_FILTER)
            # + 카운터 신뢰 조건(ops_delta>0, iot_delta 가 [0, wall] 범위 — phantom busy/reset/overflow 가드,
            # disk.io_saturation·실시간현황 latest_saturation 과 동일 원칙). MAX(await_ms)>임계 는 device 별
            # bool_or(await_ms>임계) 와 동치(비교가 단조라 — worst device 만 넘으면 전체 넘음).
            sid_dio = "AND server_id = ANY(:server_ids)" if server_ids else ""
            params["window_start"] = start - bucket_td
            params["diskio_util_min"] = recommendation.RS_DISKIO_UTIL_MIN
            params["diskio_await_ms"] = recommendation.RS_DISKIO_AWAIT_MS
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
                        t   - LAG(t)   OVER w AS d_t,
                        ops - LAG(ops) OVER w AS d_ops,
                        iot - LAG(iot) OVER w AS d_iot,
                        EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS d_wall
                    FROM l_raw WINDOW w AS (PARTITION BY server_id, device_id ORDER BY collected_at)
                ),
                per_dev AS (
                    SELECT collected_at,
                        CASE WHEN d_ops > 0 AND d_wall > 0 AND d_iot >= 0 AND d_iot <= d_wall
                                  AND d_iot / d_wall >= :diskio_util_min
                             THEN d_t::float / d_ops * 1000 END AS await_ms
                    FROM l_delta WHERE collected_at >= :start
                ),
                flags AS (
                    SELECT collected_at, bool_or(await_ms > (:diskio_await_ms)::float) AS crossed
                    FROM per_dev GROUP BY collected_at
                ),
                per_bucket AS (
                    SELECT time_bucket(interval '{bi}', collected_at) AS ts, bool_or(crossed) AS ever
                    FROM flags GROUP BY ts
                )
                SELECT ts, (CASE WHEN ever THEN 1.0 ELSE 0.0 END) AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_bucket ORDER BY ts
            """)
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
        elif metric_type == "net.drop_percent":
            # 패킷 드롭율 % = Σ(Δrx_dropped+Δtx_dropped) / Σ(Δrx_packets+Δtx_packets) * 100 — report.py
            # net_drop_pct 와 동일 산식(분모 rx+tx 전체 — retrans% 는 tx 만이라 다름). reset 은 GREATEST(Δ,0).
            sid_nd = "AND server_id = ANY(:server_ids)" if server_ids else ""
            sql = text(f"""
                WITH raw AS (
                    SELECT collected_at, server_id, iface_id, rx_dropped, tx_dropped, rx_packets, tx_packets
                    FROM {ServerNetIo.__tablename__}
                    WHERE {_PHYS_IFACE_SQL_FILTER}
                      AND collected_at >= :window_start AND collected_at <= :end {sid_nd}
                ),
                deltas AS (
                    SELECT collected_at,
                        GREATEST(rx_dropped - LAG(rx_dropped) OVER w, 0) AS d_rxd,
                        GREATEST(tx_dropped - LAG(tx_dropped) OVER w, 0) AS d_txd,
                        GREATEST(rx_packets - LAG(rx_packets) OVER w, 0) AS d_rxp,
                        GREATEST(tx_packets - LAG(tx_packets) OVER w, 0) AS d_txp
                    FROM raw WINDOW w AS (PARTITION BY server_id, iface_id ORDER BY collected_at)
                ),
                per_ts AS (
                    SELECT collected_at, SUM(d_rxd) + SUM(d_txd) AS drop_sum, SUM(d_rxp) + SUM(d_txp) AS pkt_sum
                    FROM deltas WHERE collected_at >= :start GROUP BY collected_at
                ),
                rate_ts AS (
                    SELECT collected_at, drop_sum::float / NULLIF(pkt_sum, 0) * 100 AS v FROM per_ts
                )
                SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value, NULL::text AS dimension, NULL::text AS kind
                FROM rate_ts WHERE v IS NOT NULL GROUP BY ts ORDER BY ts
            """)
            params["window_start"] = start - bucket_td
        elif metric_type == "net.congested":
            # 네트워크 이상 여부(서버 상세, 이진 0/1) — net.congested_hosts(환경, 판정 crossing 서버 수)와
            # 동일 원자료·임계·OR 판정, 서버 1대 단일 시계열로 축소(mem.paging_pressure 와 동일 원칙).
            sid_nc = "AND server_id = ANY(:server_ids)" if server_ids else ""
            params["window_start"] = start - bucket_td
            params["min_traffic_kbps"] = recommendation.RS_NET_MIN_TRAFFIC_KBPS
            params["retrans_threshold"] = recommendation.RS_NET_RETRANS_PCT
            params["drop_threshold"] = recommendation.RS_NET_DROP_PCT
            params["conntrack_threshold"] = recommendation.RS_CONNTRACK_SATURATION_RATIO
            sql = text(f"""
                WITH tcp_raw AS (
                    SELECT collected_at, server_id, net_tcp_retransmits AS retrans,
                        net_conntrack_usage, net_conntrack_limit
                    FROM {ServerMetrics.__tablename__}
                    WHERE collected_at >= :window_start AND collected_at <= :end {sid_nc}
                ),
                tcp_deltas AS (
                    SELECT collected_at, server_id,
                        GREATEST(retrans - LAG(retrans) OVER w, 0) AS d_retrans,
                        CASE WHEN net_conntrack_limit > 0
                             THEN net_conntrack_usage::float / net_conntrack_limit END AS conntrack_ratio
                    FROM tcp_raw WINDOW w AS (PARTITION BY server_id ORDER BY collected_at)
                ),
                net_raw AS (
                    SELECT collected_at, server_id, iface_id,
                        tx_packets, rx_packets, rx_dropped, tx_dropped, rx_bytes, tx_bytes
                    FROM {ServerNetIo.__tablename__}
                    WHERE {_PHYS_IFACE_SQL_FILTER}
                      AND collected_at >= :window_start AND collected_at <= :end {sid_nc}
                ),
                iface_deltas AS (
                    SELECT collected_at, server_id,
                        GREATEST(tx_packets - LAG(tx_packets) OVER w, 0) AS d_txp,
                        GREATEST(rx_packets - LAG(rx_packets) OVER w, 0) AS d_rxp,
                        GREATEST(rx_dropped - LAG(rx_dropped) OVER w, 0) AS d_rxd,
                        GREATEST(tx_dropped - LAG(tx_dropped) OVER w, 0) AS d_txd,
                        GREATEST(rx_bytes - LAG(rx_bytes) OVER w, 0) AS d_rxb,
                        GREATEST(tx_bytes - LAG(tx_bytes) OVER w, 0) AS d_txb,
                        EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
                    FROM net_raw WINDOW w AS (PARTITION BY server_id, iface_id ORDER BY collected_at)
                ),
                net_deltas AS (
                    SELECT collected_at, server_id,
                        SUM(d_txp) AS d_txp,
                        SUM(d_rxd) + SUM(d_txd) AS d_drop,
                        SUM(d_rxp) + SUM(d_txp) AS d_pkt,
                        (SUM(d_rxb) + SUM(d_txb)) / 1024.0 / NULLIF(MAX(dt), 0) AS kbytes_per_s
                    FROM iface_deltas GROUP BY collected_at, server_id
                ),
                joined AS (
                    SELECT n.collected_at, n.d_txp, n.d_drop, n.d_pkt, n.kbytes_per_s,
                        t.d_retrans, t.conntrack_ratio
                    FROM net_deltas n
                    LEFT JOIN tcp_deltas t USING (collected_at, server_id)
                    WHERE n.collected_at >= :start
                ),
                flags AS (
                    SELECT collected_at,
                        (kbytes_per_s IS NULL OR kbytes_per_s >= (:min_traffic_kbps)::float) AS has_traffic,
                        d_retrans::float / NULLIF(d_txp, 0) * 100 AS retrans_pct,
                        d_drop::float / NULLIF(d_pkt, 0) * 100 AS drop_pct,
                        conntrack_ratio
                    FROM joined
                ),
                crossed AS (
                    SELECT collected_at,
                        (has_traffic AND (
                            (retrans_pct IS NOT NULL AND retrans_pct > (:retrans_threshold)::float)
                            OR (drop_pct IS NOT NULL AND drop_pct > (:drop_threshold)::float)
                        ))
                        OR (conntrack_ratio IS NOT NULL AND conntrack_ratio >= (:conntrack_threshold)::float) AS congested
                    FROM flags
                ),
                per_bucket AS (
                    SELECT time_bucket(interval '{bi}', collected_at) AS ts, bool_or(congested) AS ever
                    FROM crossed GROUP BY ts
                )
                SELECT ts, (CASE WHEN ever THEN 1.0 ELSE 0.0 END) AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_bucket ORDER BY ts
            """)
        elif metric_type == "net.congested_hosts":
            # 네트워크 이상 서버 수 — recommendation.assess_network 의 실제 판정(HostAssessment.
            # network_congested)과 동일 원자료·임계 3종을 SQL 이식: 재전송율(net_retrans_pct, 임계
            # RS_NET_RETRANS_PCT)·드롭율(net_drop_pct, 임계 RS_NET_DROP_PCT)은 저트래픽 게이트(RS_NET_
            # MIN_TRAFFIC_KBPS 미만이면 부팅기 소수 이벤트가 비율을 지배해 억제) 적용, conntrack 고갈
            # (net_conntrack_usage/limit, 임계 RS_CONNTRACK_SATURATION_RATIO)은 트래픽량과 무관한 절대
            # 신호라 게이트 제외 — assess_network 와 동일 OR 판정. TCP 재전송율·패킷 드롭율 2개 % 라인이
            # 시각적으로 거의 겹쳐 구분이 안 되는 문제도 판정 crossing 서버 수(count)로 흡수해 해결.
            # server_metrics(재전송·conntrack)와 server_net_io(드롭·물리 iface 트래픽)는 같은 agent
            # 보고 주기라 (collected_at, server_id) 로 조인. reset(카운터 감소)은 GREATEST(Δ,0) 흡수 —
            # iface 별로 delta 를 먼저 구한 뒤 server 로 SUM(net.retrans_percent·net.drop_percent 와 동일
            # per-iface-then-sum 순서, server SUM 을 먼저 하면 한 iface 의 reset 이 다른 iface 정상 증가분에
            # 묻혀 GREATEST 클램프가 안 먹는다).
            # 버킷 먼저 묶고 서버별 bool_or 후 count — cpu.saturation_hosts 와 동일 staggered-report 대응.
            sid_nc = "AND server_id = ANY(:server_ids)" if server_ids else ""
            params["window_start"] = start - bucket_td
            params["min_traffic_kbps"] = recommendation.RS_NET_MIN_TRAFFIC_KBPS
            params["retrans_threshold"] = recommendation.RS_NET_RETRANS_PCT
            params["drop_threshold"] = recommendation.RS_NET_DROP_PCT
            params["conntrack_threshold"] = recommendation.RS_CONNTRACK_SATURATION_RATIO
            sql = text(f"""
                WITH tcp_raw AS (
                    SELECT collected_at, server_id, net_tcp_retransmits AS retrans,
                        net_conntrack_usage, net_conntrack_limit
                    FROM {ServerMetrics.__tablename__}
                    WHERE collected_at >= :window_start AND collected_at <= :end {sid_nc}
                ),
                tcp_deltas AS (
                    SELECT collected_at, server_id,
                        GREATEST(retrans - LAG(retrans) OVER w, 0) AS d_retrans,
                        CASE WHEN net_conntrack_limit > 0
                             THEN net_conntrack_usage::float / net_conntrack_limit END AS conntrack_ratio
                    FROM tcp_raw WINDOW w AS (PARTITION BY server_id ORDER BY collected_at)
                ),
                net_raw AS (
                    SELECT collected_at, server_id, iface_id,
                        tx_packets, rx_packets, rx_dropped, tx_dropped, rx_bytes, tx_bytes
                    FROM {ServerNetIo.__tablename__}
                    WHERE {_PHYS_IFACE_SQL_FILTER}
                      AND collected_at >= :window_start AND collected_at <= :end {sid_nc}
                ),
                iface_deltas AS (
                    -- iface 별로 delta 를 먼저 구해야 한 iface 의 counter reset 이 GREATEST(Δ,0) 에 걸려도
                    -- 다른 iface 의 정상 증가분과 섞이지 않는다(net.retrans_percent·net.drop_percent 와 동일
                    -- per-iface-then-sum 순서 — server 레벨 SUM 을 먼저 하면 reset 이 합계 안에 묻혀버림).
                    SELECT collected_at, server_id,
                        GREATEST(tx_packets - LAG(tx_packets) OVER w, 0) AS d_txp,
                        GREATEST(rx_packets - LAG(rx_packets) OVER w, 0) AS d_rxp,
                        GREATEST(rx_dropped - LAG(rx_dropped) OVER w, 0) AS d_rxd,
                        GREATEST(tx_dropped - LAG(tx_dropped) OVER w, 0) AS d_txd,
                        GREATEST(rx_bytes - LAG(rx_bytes) OVER w, 0) AS d_rxb,
                        GREATEST(tx_bytes - LAG(tx_bytes) OVER w, 0) AS d_txb,
                        EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
                    FROM net_raw WINDOW w AS (PARTITION BY server_id, iface_id ORDER BY collected_at)
                ),
                net_deltas AS (
                    SELECT collected_at, server_id,
                        SUM(d_txp) AS d_txp,
                        SUM(d_rxd) + SUM(d_txd) AS d_drop,
                        SUM(d_rxp) + SUM(d_txp) AS d_pkt,
                        (SUM(d_rxb) + SUM(d_txb)) / 1024.0 / NULLIF(MAX(dt), 0) AS kbytes_per_s
                    FROM iface_deltas GROUP BY collected_at, server_id
                ),
                joined AS (
                    SELECT n.collected_at, n.server_id, n.d_txp, n.d_drop, n.d_pkt, n.kbytes_per_s,
                        t.d_retrans, t.conntrack_ratio
                    FROM net_deltas n
                    LEFT JOIN tcp_deltas t USING (collected_at, server_id)
                    WHERE n.collected_at >= :start
                ),
                flags AS (
                    SELECT collected_at, server_id,
                        (kbytes_per_s IS NULL OR kbytes_per_s >= (:min_traffic_kbps)::float) AS has_traffic,
                        d_retrans::float / NULLIF(d_txp, 0) * 100 AS retrans_pct,
                        d_drop::float / NULLIF(d_pkt, 0) * 100 AS drop_pct,
                        conntrack_ratio
                    FROM joined
                ),
                crossed AS (
                    SELECT collected_at, server_id,
                        (has_traffic AND (
                            (retrans_pct IS NOT NULL AND retrans_pct > (:retrans_threshold)::float)
                            OR (drop_pct IS NOT NULL AND drop_pct > (:drop_threshold)::float)
                        ))
                        OR (conntrack_ratio IS NOT NULL AND conntrack_ratio >= (:conntrack_threshold)::float) AS congested
                    FROM flags
                ),
                per_bucket AS (
                    SELECT time_bucket(interval '{bi}', collected_at) AS ts, server_id, bool_or(congested) AS ever
                    FROM crossed GROUP BY ts, server_id
                )
                SELECT ts, COUNT(*) FILTER (WHERE ever) AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_bucket GROUP BY ts ORDER BY ts
            """)
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
        elif metric_type == "mem.paging_pressure":
            # 메모리 압박 여부(서버 상세, 이진 0/1) — mem.paging_pressure_hosts(환경, 판정 crossing 서버 수)와
            # 동일 원자료·임계, 서버 1대 단일 시계열로 축소: recommendation.mem_pressure_active 동일 판정
            # (Linux refault 임계 "> 0"·Windows Pages Input/sec 임계 WIN_PAGES_INPUT_SATURATION=20/s). Linux
            # 페이징은 magnitude 아닌 존재 판정이라(하드폴트 절대 rate 는 디스크 속도 의존이라 보편 임계 불가)
            # raw rate 를 그대로 선으로 그리면 OS 간 척도가 달라 비교 불가 — 버킷 안에서 한 번이라도 넘었는지
            # (bool_or)를 1.0/0.0 스텝으로 표시해야 Linux·Windows 를 같은 잣대(판정 결과)로 비교 가능.
            # reset(카운터 감소)은 GREATEST(Δ,0) 흡수. 하드폴트 원자료 컬럼은 os-aware(Windows 는 paging.operations
            # 를 direction=in 만 발행해 paging_major 가 항상 NULL — Linux=paging_major(refault) / Windows=paging_in
            # (Pages Input), report_aggregate pages_input_rate 산식과 동일 소스 통일).
            sid_mp = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
            params["window_start"] = start - bucket_td
            params["win_paging_threshold"] = recommendation.WIN_PAGES_INPUT_SATURATION
            sql = text(f"""
                WITH raw AS (
                    SELECT sm.collected_at, sm.server_id, si.os_family,
                           CASE WHEN si.os_family = 'windows' THEN sm.paging_in ELSE sm.paging_major END AS p
                    FROM {ServerMetrics.__tablename__} sm
                    JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
                    WHERE sm.collected_at >= :window_start AND sm.collected_at <= :end {sid_mp}
                      AND (CASE WHEN si.os_family = 'windows' THEN sm.paging_in ELSE sm.paging_major END) IS NOT NULL
                ),
                deltas AS (
                    SELECT collected_at, os_family,
                        GREATEST(p - LAG(p) OVER w, 0) AS d_p,
                        EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
                    FROM raw WINDOW w AS (PARTITION BY server_id ORDER BY collected_at)
                ),
                rates AS (
                    SELECT collected_at, os_family, d_p::float / dt AS rate
                    FROM deltas WHERE collected_at >= :start AND dt > 0
                ),
                flags AS (
                    SELECT collected_at,
                        (os_family = 'windows' AND rate >= (:win_paging_threshold)::float)
                        OR (os_family != 'windows' AND rate > 0) AS crossed
                    FROM rates
                ),
                per_bucket AS (
                    SELECT time_bucket(interval '{bi}', collected_at) AS ts, bool_or(crossed) AS ever
                    FROM flags GROUP BY ts
                )
                SELECT ts, (CASE WHEN ever THEN 1.0 ELSE 0.0 END) AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_bucket ORDER BY ts
            """)
        elif metric_type == "mem.paging_pressure_hosts":
            # 메모리 압박 서버 수 — recommendation.mem_pressure_active(mem_saturated dual-gate 의 실제 페이징
            # 신호원) 동일 원자료·임계를 SQL 로 이식: paging_major(하드폴트 카운터) rate 가 os별 임계를 넘는
            # 서버 수. Linux(refault, 임계 "> 0")·Windows(Pages Input/sec, 임계 WIN_PAGES_INPUT_SATURATION=20/s).
            # CPU 실행 큐와 달리 Linux 쪽이 magnitude 아닌 존재 판정이라(하드폴트 절대 rate 는 디스크 속도
            # 의존이라 보편 임계 불가, 의식적으로 존재 판정으로 후퇴시킨 설계) 정규화 지수 대신 "판정 crossing
            # 서버 수"(count)로 집계 — 분모(온라인 대수) 변동에 왜곡 없는 절대치, 실제 판정에 쓰는 신호라
            # mem.psi(장식적 참고치, 판정 비관여)보다 정합. reset(카운터 감소)은 GREATEST(Δ,0) 흡수 — PSI 와 동일.
            # 버킷 먼저 묶고 그 안에서 server 별 "한 번이라도 넘었는지"(bool_or) 후 distinct server 수 — raw
            # collected_at 별로 먼저 세고 avg 내면 서버들이 비동기 보고라 매 시점 사실상 1대만 잡혀 소수 카운트가
            # 나온다(오류, cpu.saturation_hosts 와 동일 수정). 버킷 우선이라 항상 정수. 하드폴트 원자료 컬럼은
            # os-aware(mem.paging_pressure 와 동일 사유 — Windows paging_major 는 항상 NULL, Linux=paging_major
            # (refault) / Windows=paging_in(Pages Input)).
            sid_sm = "AND sm.server_id = ANY(:server_ids)" if server_ids else ""
            params["window_start"] = start - bucket_td
            params["win_paging_threshold"] = recommendation.WIN_PAGES_INPUT_SATURATION
            sql = text(f"""
                WITH raw AS (
                    SELECT sm.collected_at, sm.server_id, si.os_family,
                           CASE WHEN si.os_family = 'windows' THEN sm.paging_in ELSE sm.paging_major END AS p
                    FROM {ServerMetrics.__tablename__} sm
                    JOIN {ServerInventory.__tablename__} si ON si.id = sm.server_id
                    WHERE sm.collected_at >= :window_start AND sm.collected_at <= :end {sid_sm}
                      AND (CASE WHEN si.os_family = 'windows' THEN sm.paging_in ELSE sm.paging_major END) IS NOT NULL
                ),
                deltas AS (
                    SELECT collected_at, server_id, os_family,
                        GREATEST(p - LAG(p) OVER w, 0) AS d_p,
                        EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
                    FROM raw WINDOW w AS (PARTITION BY server_id ORDER BY collected_at)
                ),
                rates AS (
                    SELECT collected_at, server_id, os_family, d_p::float / dt AS rate
                    FROM deltas WHERE collected_at >= :start AND dt > 0
                ),
                flags AS (
                    SELECT collected_at, server_id,
                        (os_family = 'windows' AND rate >= (:win_paging_threshold)::float)
                        OR (os_family != 'windows' AND rate > 0) AS crossed
                    FROM rates
                ),
                per_bucket AS (
                    SELECT time_bucket(interval '{bi}', collected_at) AS ts, server_id, bool_or(crossed) AS ever
                    FROM flags GROUP BY ts, server_id
                )
                SELECT ts, COUNT(*) FILTER (WHERE ever) AS value, NULL::text AS dimension, NULL::text AS kind
                FROM per_bucket GROUP BY ts ORDER BY ts
            """)
        elif metric_type == "fs.usage_percent":
            if collapse:
                # 환경 사용률 — per-instant GROUP BY collected_at 는 staggered 수집(server 별 보고 시각이
                # 제각각)에서 그 시점 보고 안 한 server 가 빠져 fs.used_bytes 와 동일 왜곡(창 첫/끝 bucket 이
                # 부분 서버만 반영해 튐, F5 로 재확인 후 수정). LATERAL 로 각 bucket·server+mount 조합마다
                # "그 bucket 끝 시점까지의 마지막 값"(LOCF)을 끌어와 Σused/Σ(used+free) — lookback
                # (:window_start = start - 1bucket)으로 첫 bucket 도 직전 값을 확보.
                sid_fs = "AND server_id = ANY(:server_ids)" if server_ids else ""
                sid_fs_sf = "AND sf.server_id = ANY(:server_ids)" if server_ids else ""
                params["window_start"] = start - bucket_td
                sql = text(f"""
                    WITH targets AS (
                        SELECT DISTINCT server_id, mountpoint
                        FROM {ServerFilesystem.__tablename__}
                        WHERE collected_at >= :window_start AND collected_at <= :end {sid_fs}
                          AND {_DATA_VOLUME_SQL_FILTER}
                    ),
                    buckets AS (
                        SELECT generate_series(
                            time_bucket(interval '{bi}', (:start)::timestamptz),
                            time_bucket(interval '{bi}', (:end)::timestamptz),
                            interval '{bi}'
                        ) AS ts
                    ),
                    per_bucket AS (
                        SELECT b.ts, t.server_id, t.mountpoint, lv.used AS used, lv.free AS free
                        FROM buckets b
                        CROSS JOIN targets t
                        LEFT JOIN LATERAL (
                            SELECT sf.used_bytes AS used, sf.free_bytes AS free
                            FROM {ServerFilesystem.__tablename__} sf
                            WHERE sf.server_id = t.server_id AND sf.mountpoint = t.mountpoint
                              AND sf.collected_at >= :window_start
                              AND sf.collected_at < b.ts + interval '{bi}' {sid_fs_sf}
                              AND sf.used_bytes IS NOT NULL AND sf.free_bytes IS NOT NULL
                            ORDER BY sf.collected_at DESC
                            LIMIT 1
                        ) lv ON true
                    )
                    SELECT ts,
                        SUM(used)::float / NULLIF(SUM(used + free), 0) * 100 AS value,
                        NULL::text AS dimension, NULL::text AS kind
                    FROM per_bucket GROUP BY ts HAVING SUM(used + free) > 0 ORDER BY ts
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
            # 물리 device/iface 필터 — collapse 여부 무관 항상 적용(물리 디스크 위 LVM/RAID/crypt LV, 물리 NIC
            # 위 bridge/virtual 관통 이중집계 방지, device_filters 단일 정책).
            phys_filter = _PHYS_DISK_SQL_FILTER if dim_col == "device_id" else _PHYS_IFACE_SQL_FILTER
            if collapse:
                # 환경 합산 — 수집이 staggered(collected_at 당 소수 서버)라 per-instant SUM 은 undercount
                # (합산이 아니라 서버당 평균 ~ 총량/N 로 나옴). server+device 별 버킷 평균 rate 로 정렬 후
                # SUM(전 함대) — 시점 정렬 무관 정확한 함대 합산. agg(avg/max/p95) 무의미(합산) -> ae 미적용.
                dev_filter = phys_filter
                tail = f"""
                    per_sd AS (
                        SELECT time_bucket(interval '{bi}', collected_at) AS ts, server_id, dim, avg(v) AS sv
                        FROM rates WHERE v IS NOT NULL GROUP BY 1, server_id, dim
                    )
                    SELECT ts, SUM(sv) AS value, NULL::text AS dimension, NULL::text AS kind
                    FROM per_sd GROUP BY ts ORDER BY ts
                """
            else:
                # 서버 상세 — device/iface 별 멀티라인 보존(물리만). 단일 서버라 per-instant 합산 이슈 없음.
                dev_filter = f"{phys_filter} AND (CAST(:dim_filter AS text) IS NULL OR {dim_col} = :dim_filter)"
                params["dim_filter"] = dimension
                # 범례 표시명 — raw id_type:id(예: "mac:fa:16:..") 대신 inventory 의 사람이 읽는 name(예:
                # "enp3s0"/"PhysicalDrive0")으로 치환. Linux 는 id_type=mac 인터페이스가 흔해 MAC 그대로 노출되면
                # 가독성이 떨어진다는 지적 반영. 매칭 안 되면(신규 미동기화 등) raw dim 폴백(COALESCE).
                if dim_col == "device_id":
                    name_join = """
                        LEFT JOIN LATERAL (
                            SELECT elem->>'name' AS name
                            FROM server_inventory si_dn, jsonb_array_elements(si_dn.block_devices) elem
                            WHERE si_dn.id = per_ts.server_id
                              AND ((elem->>'id_type') || ':' || (elem->>'id') = per_ts.dim
                                   OR 'name:' || (elem->>'name') = per_ts.dim)
                              AND (elem->>'type') = 'disk'
                            LIMIT 1
                        ) dn ON true
                    """
                else:
                    name_join = """
                        LEFT JOIN LATERAL (
                            SELECT elem->>'name' AS name
                            FROM server_inventory si_dn, jsonb_array_elements(si_dn.net_interfaces) elem
                            WHERE si_dn.id = per_ts.server_id
                              AND (elem->>'id_type') || ':' || (elem->>'id') = per_ts.dim
                              AND (elem->>'kind') IN ('physical', 'bond_master')
                            LIMIT 1
                        ) dn ON true
                    """
                tail = f"""
                    per_ts AS (
                        SELECT collected_at, server_id, dim, SUM(v) AS v
                        FROM rates WHERE v IS NOT NULL GROUP BY collected_at, server_id, dim
                    )
                    SELECT time_bucket(interval '{bi}', collected_at) AS ts, {ae} AS value,
                        COALESCE(dn.name, dim) AS dimension, NULL::text AS kind
                    FROM per_ts
                    {name_join}
                    GROUP BY ts, dim, dn.name ORDER BY ts, dim
                """
            sql = text(f"""
                WITH raw AS (
                    SELECT collected_at, server_id, {dim_col} AS dim, {value_col} AS cnt
                    FROM {table}
                    WHERE collected_at >= :window_start AND collected_at <= :end {sid} AND {dev_filter}
                ),
                deltas AS (
                    SELECT collected_at, server_id, dim,
                        GREATEST(cnt - LAG(cnt) OVER w, 0) AS d_val,
                        EXTRACT(EPOCH FROM (collected_at - LAG(collected_at) OVER w)) AS dt
                    FROM raw WINDOW w AS (PARTITION BY server_id, dim ORDER BY collected_at)
                ),
                rates AS (
                    SELECT collected_at, server_id, dim,
                        CASE WHEN dt IS NULL OR dt <= 0 OR d_val IS NULL THEN NULL ELSE d_val / dt END AS v
                    FROM deltas WHERE collected_at >= :start
                ),
                {tail}
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
