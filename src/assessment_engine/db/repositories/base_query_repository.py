from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Literal

from assessment_engine.db.repositories.outbound import (
    CollectionStatus,
    DashboardRaw,
    DiskUsageWarningRaw,
    EnvironmentUtilizationRaw,
    MetricGapWarningRaw,
    MetricSeries,
    NetworkWithIo,
    RebootEvent,
    ReportRowRaw,
    ServerDetail,
    ServerSummary,
    StorageWithUsage,
)

MetricType = Literal[
    "cpu.usage_percent",
    "cpu.user_percent",
    "cpu.system_percent",
    "cpu.iowait_percent",
    "load.1m",
    "load.5m",
    "load.15m",
    "mem.usage_percent",
    "mem.available_percent",
    "mem.cached_percent",
    "mem.buffers_percent",
    "swap.usage_percent",
    "disk.read_iops",
    "disk.write_iops",
    "fs.usage_percent",
    "net.rx_bytes_per_sec",
    "net.tx_bytes_per_sec",
]
TimeRange  = Literal["15m", "1h", "6h", "24h", "7d", "14d", "30d"]
BucketSize = Literal["1m", "5m", "15m", "30m", "1h", "3h", "6h", "12h", "1d"]
AggFunc    = Literal["avg", "max", "p95"]

# TimeRange → timedelta. metric_chart·reboot_events 양쪽 사용 (service·repo 중복 방지).
# 14d는 right-sizing 윈도우(recommendation.WINDOW_DAYS)와 동일 — 보고서·대시보드·차트 일관.
TIME_RANGE_TD: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h":  timedelta(hours=1),
    "6h":  timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d":  timedelta(days=7),
    "14d": timedelta(days=14),
    "30d": timedelta(days=30),
}


class BaseQueryRepository(ABC):

    @abstractmethod
    async def resolve_server_id(self, public_id: str) -> int | None: ...

    @abstractmethod
    async def resolve_server_ids(self, public_ids: list[str]) -> dict[str, int]:
        """N개 public_id → {public_id: server_id} 단일 SQL. 미존재 public_id는 dict에서 누락."""
        ...

    @abstractmethod
    async def get_servers(self, server_ids: list[int]) -> list[ServerDetail]:
        """N개 server_id → ServerDetail 리스트 단일 SQL. 순서는 DB 임의 — caller 정렬 책임."""
        ...

    @abstractmethod
    async def list_servers(
        self,
        page: int,
        limit: int,
        search: str | None,
    ) -> list[ServerSummary]: ...

    @abstractmethod
    async def get_server(self, server_id: int) -> ServerDetail | None: ...

    @abstractmethod
    async def get_storage(self, server_id: int) -> StorageWithUsage | None: ...

    @abstractmethod
    async def get_network(self, server_id: int) -> NetworkWithIo | None: ...

    @abstractmethod
    async def get_collection_status(self, server_id: int) -> CollectionStatus | None: ...

    @abstractmethod
    async def latest_dashboard(self, server_id: int) -> DashboardRaw | None: ...

    @abstractmethod
    async def metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeries]: ...

    @abstractmethod
    async def metric_chart(
        self,
        server_id: int,
        metric_type: MetricType,
        dimension: str | None,
        time_range: TimeRange,
        bucket: BucketSize,
        agg: AggFunc,
        end: datetime | None = None,
    ) -> list[MetricSeries]: ...

    @abstractmethod
    async def reboot_events(
        self,
        server_id: int,
        start: datetime,
        end: datetime,
    ) -> list[RebootEvent]: ...

    @abstractmethod
    async def report_aggregate(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> list[ReportRowRaw]: ...

    @abstractmethod
    async def report_mount_worst(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> dict[int, tuple[str | None, float | None, int | None]]:
        """server_id -> (worst_mount, worst_mount_used_pct, worst_mount_days_until_full).

        used_pct는 period 안 최대값. days_until_full은 (avail_start - avail_end)/period_days로
        fill_rate 추정, 양수일 때만. 음수·0이면 None (채워지지 않거나 비워지는 추세).
        """

    @abstractmethod
    async def report_uptime_stats(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> dict[int, int]:
        """server_id -> period 안 boot_time 변경(재부팅) 횟수. uptime_days는 호출자가
        ReportRowRaw.boot_time으로 직접 계산 (현재 시각 - boot_time).
        """

    @abstractmethod
    async def report_disk_io_baseline(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> dict[int, tuple[int | None, float | None, float | None, float | None, float | None, float | None]]:
        """server_id -> (iops_baseline, throughput_kbps_baseline,
                          iops_p95, iops_peak, throughput_kbps_p95, throughput_kbps_peak).

        baseline = SUM(delta) / SUM(dt) (모든 device 합산 평균).
        p95/peak = 시점별(서버, collected_at) device 합산 rate에서 percentile_cont(0.95) + MAX.
        boot_time reset 행은 LAG NULL 처리로 자연 제외.
        """

    @abstractmethod
    async def report_net_io_baseline(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> dict[int, tuple[float | None, float | None, float | None, float | None, float | None, float | None]]:
        """server_id -> (rx_kbps_baseline, tx_kbps_baseline,
                          rx_kbps_p95, rx_kbps_peak, tx_kbps_p95, tx_kbps_peak).

        baseline = SUM(delta_bytes) / SUM(dt) / 1024 (모든 interface 합산 평균).
        p95/peak = 시점별 interface 합산 rate에서 percentile_cont(0.95) + MAX.
        """

    @abstractmethod
    async def list_server_ids(self, limit: int = 1000) -> list[int]:
        """등록 서버 정수 PK 모음 — ID만 필요한 batch 호출용 (risk_top 등). disks JSONB 같은 큰 컬럼 미포함 (T8)."""
        ...


    @abstractmethod
    async def disk_usage_warnings(
        self,
        threshold_pct: float,
        limit: int,
    ) -> list[DiskUsageWarningRaw]:
        """전체 mount 중 사용률 임계 초과만 단일 SQL. mount당 latest 1건 → ORDER BY DESC LIMIT N."""
        ...

    @abstractmethod
    async def metric_gap_warnings(
        self,
        gap_minutes: int,
        recent_hours: int,
        limit: int,
    ) -> list[MetricGapWarningRaw]:
        """metric 발행 갭이 gap_minutes 초과 + 최근 recent_hours 안 발행 있던 서버 — '한때 살아있다 끊김'."""
        ...

    @abstractmethod
    async def environment_utilization(
        self,
        period_days: int = 1,
    ) -> EnvironmentUtilizationRaw:
        """환경 전체 서버 N일 평균 활용률 — list 화면 도넛.

        - CPU: 기간 내 모든 인접 시점 jiffies delta 평균 (LAG 윈도우)
        - MEM: 기간 내 모든 시점 (1 - available/total) 평균
        - DISK: 기간 내 mount별 평균 사용률 → 서버별 max → 서버 간 평균
        기간 내 메트릭 없는 서버 자동 제외. partition pruning 의무 (C5).
        period_days는 보고서 분류 SQL과 동일 기간으로 호출해 일관성 유지.
        """
        ...