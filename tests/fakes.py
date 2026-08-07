"""테스트 대역 — 실제 구현 대신 Protocol 을 만족하는 최소 객체.

`AsyncMock` 은 어떤 속성 접근도 통과시켜 pyright strict 에서 0 errors 를 내므로 계약 검사가 되지 않는다.
여기 대역은 Protocol 을 실제로 만족하고, 모듈 끝의 정적 단언이 그것을 컴파일 시점에 강제한다.

`InMemoryQueryRepository` 는 메서드 이름을 키로 하는 seed 를 받아 그대로 돌려준다. seed 에 없는
메서드는 반환 타입에 맞는 빈 값을 준다 — 화면이 "데이터 없음" 경로를 타는 상태다.

`InMemoryCollectRepository` 는 반대로 호출을 기록한다. consumer 가 검사하는 것은 반환값보다
"무엇을 몇 번 썼는가" 라서 기록이 곧 단언 대상이다.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Self, cast

from assessment_engine.db.dtos.outbound import (
    CpuBreakdownRaw,
    EnvironmentUtilizationRaw,
    ErrorFleetRaw,
    FleetErrorRaw,
    MemoryBreakdownRaw,
)
from assessment_engine.db.repositories.collect import MetricInsertResult

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from datetime import datetime

    from assessment_engine.db.dtos.inbound import (
        ServerInventoryCreate,
        ServerMetricCreate,
        TaskCreate,
        TaskResultUpdate,
    )
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
    from assessment_engine.db.repositories.collect import CollectRepository
    from assessment_engine.db.repositories.query import QueryRepository
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

    async def get_agent_restart_counts_recent(self, server_ids: list[int], since: datetime) -> dict[int, int]:
        return self._value("get_agent_restart_counts_recent", {})

    async def get_environment_utilization(
        self,
        period_days: float,
        end: datetime,
        server_ids: list[int] | None = None,
    ) -> EnvironmentUtilizationRaw:
        return self._value(
            "get_environment_utilization",
            EnvironmentUtilizationRaw(cpu_avg_pct=None, mem_avg_pct=None, disk_avg_pct=None, sample_size=0),
        )

    async def get_fleet_error_hosts(self, server_ids: list[int], since: datetime) -> set[int]:
        return self._value("get_fleet_error_hosts", set())

    async def get_fleet_error_summary(self, server_ids: list[int], since: datetime) -> FleetErrorRaw:
        return self._value("get_fleet_error_summary", FleetErrorRaw())

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

    async def get_latest_dashboard(self, server_id: int) -> DashboardRaw | None:
        return self._value("get_latest_dashboard", None)

    async def get_latest_errors(self, server_id: int, since: datetime) -> ErrorFleetRaw:
        return self._value("get_latest_errors", ErrorFleetRaw())

    async def get_latest_link_speed(self, server_ids: list[int], since: datetime) -> dict[int, dict[str, int]]:
        return self._value("get_latest_link_speed", {})

    async def get_latest_metric_at(self) -> datetime | None:
        return self._value("get_latest_metric_at", None)

    async def get_latest_saturation(self, server_ids: list[int], since: datetime) -> dict[int, SaturationRaw]:
        return self._value("get_latest_saturation", {})

    async def get_latest_tasks_by_servers(
        self,
        server_ids: list[int],
    ) -> dict[int, TaskRow]:
        return self._value("get_latest_tasks_by_servers", {})

    async def list_server_public_ids(self) -> list[str]:
        return self._value("list_server_public_ids", [])

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

    async def get_metric_chart(
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
        return self._value("get_metric_chart", [])

    async def get_metric_gap_warnings(
        self,
        gap_minutes: int,
        recent_hours: int,
        limit: int | None,
    ) -> list[MetricGapWarningRaw]:
        return self._value("get_metric_gap_warnings", [])

    async def get_metric_snapshots(
        self,
        server_id: int,
        cursor: datetime | None,
        limit: int,
    ) -> list[MetricSeries]:
        return self._value("get_metric_snapshots", [])

    async def get_metric_trend(
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
        return self._value("get_metric_trend", [])

    async def get_reboot_events(
        self,
        server_id: int,
        start: datetime,
        end: datetime,
    ) -> list[RebootEvent]:
        return self._value("get_reboot_events", [])

    async def get_report_agent_restart_stats(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, int]:
        return self._value("get_report_agent_restart_stats", {})

    async def get_report_aggregate(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> list[ReportRowRaw]:
        return self._value("get_report_aggregate", [])

    async def get_report_cpu_breakdown(
        self,
        server_id: int,
        period_days: float,
        end: datetime,
    ) -> CpuBreakdownRaw:
        return self._value("get_report_cpu_breakdown", CpuBreakdownRaw(user_pct=None, system_pct=None, iowait_pct=None))

    async def get_report_cpu_breakdown_batch(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, CpuBreakdownRaw]:
        return self._value("get_report_cpu_breakdown_batch", {})

    async def get_report_disk_io_baseline(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, DiskIoBaselineRaw]:
        return self._value("get_report_disk_io_baseline", {})

    async def get_report_memory_breakdown(
        self,
        server_id: int,
        period_days: float,
        end: datetime,
    ) -> MemoryBreakdownRaw:
        return self._value(
            "get_report_memory_breakdown",
            MemoryBreakdownRaw(used_pct=None, available_pct=None, cached_pct=None, buffers_pct=None),
        )

    async def get_report_memory_breakdown_batch(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, MemoryBreakdownRaw]:
        return self._value("get_report_memory_breakdown_batch", {})

    async def get_report_mount_capacity_batch(
        self,
        server_ids: list[int],
        end: datetime,
    ) -> dict[int, list[MountCapacityRaw]]:
        return self._value("get_report_mount_capacity_batch", {})

    async def get_report_net_io_baseline(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, NetIoBaselineRaw]:
        return self._value("get_report_net_io_baseline", {})

    async def get_report_uptime_stats(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, int]:
        return self._value("get_report_uptime_stats", {})

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

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)


class FakePipeline:
    """`safe_incr_with_ttl` 이 여는 MULTI/EXEC — 명령을 모았다가 execute 에서 한 번에 적용한다."""

    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._queued: list[tuple[str, str]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def incr(self, key: str) -> None:
        self._queued.append(("incr", key))

    def expire(self, key: str, ttl: int) -> None:
        self._queued.append(("expire", key))

    async def execute(self) -> list[int]:
        results: list[int] = []
        for op, key in self._queued:
            if op == "incr":
                results.append(await self._redis.incr(key))
            else:
                results.append(1)
        self._queued.clear()
        return results


class InMemoryCollectRepository:
    """`CollectRepository` protocol 대역. 호출을 `calls` 에 순서대로 남긴다.

    `known_agents` 에 있는 agent_id 는 등록된 서버로, 없으면 `ensure_server_id` 가 auto-register
    경로를 탄다. `complete_task_ok=False` 는 매칭 실패(운영자가 지운 task)를 재현한다.
    """

    def __init__(
        self,
        *,
        known_agents: dict[str, int] | None = None,
        next_server_id: int = 100,
        complete_task_ok: bool = True,
        raises: BaseException | None = None,
    ) -> None:
        self.known_agents: dict[str, int] = known_agents or {}
        self.next_server_id = next_server_id
        self.complete_task_ok = complete_task_ok
        self.raises = raises
        self.calls: list[tuple[str, Any]] = []

    def _record(self, name: str, arg: Any = None) -> None:
        self.calls.append((name, arg))
        if self.raises is not None:
            raise self.raises

    def call_names(self) -> list[str]:
        return [name for name, _ in self.calls]

    async def find_server_id(self, agent_id: str) -> int | None:
        self._record("find_server_id", agent_id)
        return self.known_agents.get(agent_id)

    async def upsert_server(self, data: ServerInventoryCreate) -> int:
        self._record("upsert_server", data)
        server_id = self.known_agents.setdefault(data.agent_id, self.next_server_id)
        if server_id == self.next_server_id:
            self.next_server_id += 1
        return server_id

    async def ensure_server_id(self, agent_id: str, fallback: ServerInventoryCreate) -> tuple[int, bool]:
        self._record("ensure_server_id", agent_id)
        known = self.known_agents.get(agent_id)
        if known is not None:
            return known, False
        return await self.upsert_server(fallback), True

    async def create_task(self, data: TaskCreate) -> str:
        self._record("create_task", data)
        return "00000000-0000-4000-8000-0000000000ff"

    async def complete_task(self, data: TaskResultUpdate) -> bool:
        self._record("complete_task", data)
        return self.complete_task_ok

    async def expire_overdue_tasks(self, server_ids: list[int]) -> int:
        self._record("expire_overdue_tasks", server_ids)
        return 0

    async def find_pending_deadline_servers(self, server_ids: list[int]) -> list[int]:
        self._record("find_pending_deadline_servers", server_ids)
        return []

    async def expire_all_overdue_tasks(self) -> int:
        self._record("expire_all_overdue_tasks")
        return 0

    async def record_metrics(self, server_id: int, data: ServerMetricCreate) -> MetricInsertResult:
        self._record("record_metrics", (server_id, data))
        return MetricInsertResult(metrics=1, disk_io=1, net_io=1, filesystem=1)


class FakeSession:
    """`async with session_factory() as session` 한 사이클 — consumer 가 쓰는 것은 commit 뿐이다."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FakeSessionFactory:
    """`_db_retry` 가 여는 세션 컨텍스트. 만들어진 세션을 `sessions` 에 모은다."""

    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []

    @asynccontextmanager
    async def _open(self) -> AsyncGenerator[FakeSession]:
        session = FakeSession()
        self.sessions.append(session)
        yield session

    def __call__(self) -> Any:
        return self._open()


class FakeMessage:
    """`AbstractIncomingMessage` 대역 — 핸들러가 만지는 5 표면만 갖는다.

    `process()` 는 실제와 같이 async 컨텍스트다. 컨텍스트 밖에서 await 하면 ack/nack 이 둘 다
    안 되는 것이 #F11 의 금지 항목이라, 진입·이탈 여부를 기록해 그 위반을 테스트가 볼 수 있게 한다.
    """

    def __init__(self, body: bytes, *, routing_key: str = "server.metrics", delivery_tag: int = 1) -> None:
        self.body = body
        self.routing_key = routing_key
        self.message_id = "fake-message-id"
        self.delivery_tag = delivery_tag
        self.requeue: bool | None = None
        self.entered = False
        self.exited = False
        self.raised: BaseException | None = None

    @asynccontextmanager
    async def process(self, *, requeue: bool = False) -> AsyncGenerator[None]:
        self.entered = True
        self.requeue = requeue
        try:
            yield
        except BaseException as e:
            self.raised = e
            raise
        finally:
            self.exited = True


class FakeQueue:
    """`queue.cancel(tag)` 만 갖는 대역 — `_drain` 이 배달을 끊는 표면이다."""

    def __init__(self, *, error: BaseException | None = None, hang: bool = False) -> None:
        self.error = error
        self.hang = hang
        self.cancelled: list[str] = []

    async def cancel(self, consumer_tag: str) -> None:
        self.cancelled.append(consumer_tag)
        if self.error is not None:
            raise self.error
        if self.hang:
            await asyncio.Event().wait()


# 대역이 protocol 을 실제로 만족하는지 컴파일 시점에 못박는다 — 메서드가 늘거나 시그니처가 바뀌면 여기서 깨진다.
_query_repo_conformance: QueryRepository = InMemoryQueryRepository()
_collect_repo_conformance: CollectRepository = InMemoryCollectRepository()


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
