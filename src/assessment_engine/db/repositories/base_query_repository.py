from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Literal

from assessment_engine.db.repositories.outbound import (
    CollectionStatus,
    DashboardRaw,
    DiskUsageWarningRaw,
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
TimeRange  = Literal["15m", "1h", "6h", "24h", "7d", "30d"]
BucketSize = Literal["1m", "5m", "15m", "30m", "1h", "3h", "12h", "1d"]
AggFunc    = Literal["avg", "max", "p95"]

# TimeRange → timedelta. metric_chart·reboot_events 양쪽 사용 (service·repo 중복 방지).
TIME_RANGE_TD: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h":  timedelta(hours=1),
    "6h":  timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d":  timedelta(days=7),
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
    async def list_server_ids(self, limit: int = 1000) -> list[int]:
        """등록 서버 정수 PK 모음 — ID만 필요한 batch 호출용 (risk_top 등). disks JSONB 같은 큰 컬럼 미포함 (T8)."""
        ...

    @abstractmethod
    async def latest_disk_max_pct(self, server_ids: list[int]) -> dict[int, float]:
        """서버별 mount latest 중 max 사용률 % — 위험 카드 표시용. 7d partition pruning.

        반환: {server_id: max_used_pct}. metric 없는 server_id는 dict에서 누락.
        """
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