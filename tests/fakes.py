"""테스트 대역 — 실제 구현 대신 Protocol 을 만족하는 최소 객체.

`AsyncMock` 은 어떤 속성 접근도 통과시켜 pyright strict 에서 0 errors 를 내므로 계약 검사가 되지 않는다.
여기 대역은 Protocol 을 실제로 만족하고, 모듈 끝의 정적 단언이 그것을 컴파일 시점에 강제한다.

`InMemoryQueryRepository` 는 메서드 이름을 키로 하는 seed 를 받아 그대로 돌려준다. seed 에 없는
메서드는 반환 타입에 맞는 빈 값을 준다 — 화면이 "데이터 없음" 경로를 타는 상태다.
"""

from typing import TYPE_CHECKING, Any, cast

from assessment_engine.db.dtos.outbound import (
    CpuBreakdownRaw,
    EnvironmentUtilizationRaw,
    ErrorFleetRaw,
    FleetErrorRaw,
    MemoryBreakdownRaw,
)

if TYPE_CHECKING:
    from datetime import datetime

    from assessment_engine.db.dtos.outbound import (
        CollectionStatus,
        DashboardRaw,
        DiskIoBaselineRaw,
        MetricGapWarningRaw,
        MetricSeries,
        MountCapacityRaw,
        NetIoBaselineRaw,
        NetworkWithIo,
        RebootEvent,
        ReportRowRaw,
        SaturationRaw,
        ServerDetail,
        ServerSummary,
        StorageWithUsage,
        TaskRow,
    )
    from assessment_engine.db.repositories.query.repository import QueryRepository
    from assessment_engine.db.repositories.query.types import (
        AggFunc,
        BucketSize,
        EnvironmentMetricType,
        MetricType,
        TimeRange,
    )


class InMemoryQueryRepository:
    """`QueryRepository` protocol 대역. seed 키는 메서드 이름 그대로다."""

    def __init__(self, seed: dict[str, Any] | None = None) -> None:
        self._seed: dict[str, Any] = seed or {}

    def _value[T](self, name: str, default: T) -> T:
        if name in self._seed:
            return cast("T", self._seed[name])
        return default

    async def agent_restart_counts_recent(self, server_ids: list[int], since: datetime) -> dict[int, int]:
        return self._value("agent_restart_counts_recent", {})

    async def environment_utilization(
        self,
        period_days: float,
        end: datetime,
        server_ids: list[int] | None = None,
    ) -> EnvironmentUtilizationRaw:
        return self._value(
            "environment_utilization",
            EnvironmentUtilizationRaw(cpu_avg_pct=None, mem_avg_pct=None, disk_avg_pct=None, sample_size=0),
        )

    async def fleet_error_hosts(self, server_ids: list[int], since: datetime) -> set[int]:
        return self._value("fleet_error_hosts", set())

    async def fleet_error_summary(self, server_ids: list[int], since: datetime) -> FleetErrorRaw:
        return self._value("fleet_error_summary", FleetErrorRaw())

    async def get_collection_status(self, server_id: int) -> CollectionStatus | None:
        return self._value("get_collection_status", None)

    async def get_network(self, server_id: int) -> NetworkWithIo | None:
        return self._value("get_network", None)

    async def get_server(self, server_id: int) -> ServerDetail | None:
        return self._value("get_server", None)

    async def get_servers(self, server_ids: list[int]) -> list[ServerDetail]:
        return self._value("get_servers", [])

    async def get_storage(self, server_id: int) -> StorageWithUsage | None:
        return self._value("get_storage", None)

    async def get_task_by_public_id(self, public_id: str) -> TaskRow | None:
        return self._value("get_task_by_public_id", None)

    async def latest_dashboard(self, server_id: int) -> DashboardRaw | None:
        return self._value("latest_dashboard", None)

    async def latest_errors(self, server_id: int, since: datetime) -> ErrorFleetRaw:
        return self._value("latest_errors", ErrorFleetRaw())

    async def latest_link_speed(self, server_ids: list[int], since: datetime) -> dict[int, dict[str, int]]:
        return self._value("latest_link_speed", {})

    async def latest_metric_at(self) -> datetime | None:
        return self._value("latest_metric_at", None)

    async def latest_saturation(self, server_ids: list[int], since: datetime) -> dict[int, SaturationRaw]:
        return self._value("latest_saturation", {})

    async def latest_tasks_by_servers(
        self,
        server_ids: list[int],
    ) -> dict[int, TaskRow]:
        return self._value("latest_tasks_by_servers", {})

    async def list_all_server_public_ids(self) -> list[str]:
        return self._value("list_all_server_public_ids", [])

    async def list_recent_tasks(
        self,
        target_server_id: int,
        limit: int,
        cursor: datetime | None = None,
    ) -> list[TaskRow]:
        return self._value("list_recent_tasks", [])

    async def list_server_ids(self, limit: int | None = 1000) -> list[int]:
        return self._value("list_server_ids", [])

    async def list_servers(
        self,
        page: int,
        limit: int,
        search: str | None,
    ) -> list[ServerSummary]:
        return self._value("list_servers", [])

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
        return self._value("metric_chart", [])

    async def metric_gap_warnings(
        self,
        gap_minutes: int,
        recent_hours: int,
        limit: int | None,
    ) -> list[MetricGapWarningRaw]:
        return self._value("metric_gap_warnings", [])

    async def metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeries]:
        return self._value("metric_snapshots", [])

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
        return self._value("metric_trend", [])

    async def reboot_events(
        self,
        server_id: int,
        start: datetime,
        end: datetime,
    ) -> list[RebootEvent]:
        return self._value("reboot_events", [])

    async def report_agent_restart_stats(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, int]:
        return self._value("report_agent_restart_stats", {})

    async def report_aggregate(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> list[ReportRowRaw]:
        return self._value("report_aggregate", [])

    async def report_cpu_breakdown(
        self,
        server_id: int,
        period_days: float,
        end: datetime,
    ) -> CpuBreakdownRaw:
        return self._value("report_cpu_breakdown", CpuBreakdownRaw(user_pct=None, system_pct=None, iowait_pct=None))

    async def report_cpu_breakdown_batch(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, CpuBreakdownRaw]:
        return self._value("report_cpu_breakdown_batch", {})

    async def report_disk_io_baseline(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, DiskIoBaselineRaw]:
        return self._value("report_disk_io_baseline", {})

    async def report_memory_breakdown(
        self,
        server_id: int,
        period_days: float,
        end: datetime,
    ) -> MemoryBreakdownRaw:
        return self._value(
            "report_memory_breakdown",
            MemoryBreakdownRaw(used_pct=None, available_pct=None, cached_pct=None, buffers_pct=None),
        )

    async def report_memory_breakdown_batch(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, MemoryBreakdownRaw]:
        return self._value("report_memory_breakdown_batch", {})

    async def report_mount_capacity_batch(
        self,
        server_ids: list[int],
        end: datetime,
    ) -> dict[int, list[MountCapacityRaw]]:
        return self._value("report_mount_capacity_batch", {})

    async def report_net_io_baseline(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, NetIoBaselineRaw]:
        return self._value("report_net_io_baseline", {})

    async def report_uptime_stats(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, int]:
        return self._value("report_uptime_stats", {})

    async def resolve_server_id(self, public_id: str) -> int | None:
        """유일하게 인자를 보는 메서드 — 미존재 식별자의 404 분기가 여기서 갈린다.

        나머지는 인자와 무관하게 seed 를 돌려준다. 이 하나만 예외인 이유는 "없는 서버" 경로가
        화면 계약의 일부라 대역이 항상 찾아주면 그 분기를 영영 못 캡처하기 때문이다.
        """
        mapping: dict[str, int] = self._seed.get("resolve_server_ids", {})
        return mapping.get(public_id)

    async def resolve_server_ids(self, public_ids: list[str]) -> dict[str, int]:
        return self._value("resolve_server_ids", {})


class FakeRedis:
    """`cache/redis.py` 의 `safe_*` helper 가 부르는 표면만 갖는 대역."""

    def __init__(self, store: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = store or {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.store.get(k) for k in keys]

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    async def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> bool:
        return key in self.store


# 대역이 protocol 을 실제로 만족하는지 컴파일 시점에 못박는다 — 메서드가 늘거나 시그니처가 바뀌면 여기서 깨진다.
_query_repo_conformance: QueryRepository = InMemoryQueryRepository()


class InMemoryDiagnosticService:
    """`DiagnosticService` 대역 — 라우터가 부르는 표면만 갖는다.

    발행 경로(`enqueue_report`)는 캡처 대상이 아니므로 조회 표면만 채운다.
    """

    async def list_reports(self, *args: Any, **kwargs: Any) -> tuple[list[Any], int]:
        return ([], 0)

    async def get_report_snapshot(self, *args: Any, **kwargs: Any) -> Any:
        return None

    async def get_job(self, *args: Any, **kwargs: Any) -> Any:
        return None
