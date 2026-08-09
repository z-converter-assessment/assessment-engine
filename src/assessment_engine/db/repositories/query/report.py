"""Report aggregation 도메인 추상 인터페이스 — USE Method 통계 + 환경 활용률."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from assessment_engine.db.dtos.outbound import (
        CpuBreakdownRaw,
        DiskIoBaselineRaw,
        EnvironmentUtilizationRaw,
        MemoryBreakdownRaw,
        MountCapacityRaw,
        NetIoBaselineRaw,
        ReportRowRaw,
    )


class ReportQueryRepository(Protocol):
    async def get_report_aggregate(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> list[ReportRowRaw]: ...
    async def get_report_uptime_stats(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, int]: ...

    async def get_report_agent_restart_stats(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, int]: ...

    async def get_agent_restart_counts_recent(self, server_ids: list[int], since: datetime) -> dict[int, int]: ...

    async def get_report_disk_io_baseline(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, DiskIoBaselineRaw]: ...

    async def get_report_net_io_baseline(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, NetIoBaselineRaw]: ...

    async def get_report_memory_breakdown(
        self,
        server_id: int,
        period_days: float,
        end: datetime,
    ) -> MemoryBreakdownRaw:
        """메모리 구성 윈도우 평균 — used/available/cached/buffers (전체 메모리 대비 %, 시점값 avg)."""
        ...

    async def get_report_cpu_breakdown(
        self,
        server_id: int,
        period_days: float,
        end: datetime,
    ) -> CpuBreakdownRaw: ...

    async def get_report_mount_capacity_batch(
        self,
        server_ids: list[int],
        end: datetime,
    ) -> dict[int, list[MountCapacityRaw]]: ...

    async def get_report_memory_breakdown_batch(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, MemoryBreakdownRaw]: ...

    async def get_report_cpu_breakdown_batch(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, CpuBreakdownRaw]: ...

    async def get_environment_utilization(
        self,
        period_days: float,
        end: datetime,
        server_ids: list[int] | None = None,
    ) -> EnvironmentUtilizationRaw: ...
