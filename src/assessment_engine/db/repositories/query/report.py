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
    ) -> dict[int, int]:
        """server_id -> period 안 boot_time 변경(재부팅) 횟수."""
        ...

    async def get_report_agent_restart_stats(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, int]:
        """server_id -> period 안 agent_started_at 변경(에이전트 재시작) 횟수.

        보고서 anchor+window 안 카운트 (#F10) — 호스트 상세 "시스템 안정성" 컬럼 표시.
        """
        ...

    async def get_agent_restart_counts_recent(self, server_ids: list[int], since: datetime) -> dict[int, int]:
        """since 이후 server별 agent 재시작 횟수를 반환한다."""
        ...

    async def get_report_disk_io_baseline(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, DiskIoBaselineRaw]:
        """server_id -> DiskIoBaselineRaw (iops·throughput baseline + p95/peak)."""
        ...

    async def get_report_net_io_baseline(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, NetIoBaselineRaw]:
        """server_id -> NetIoBaselineRaw (rx·tx baseline + p95/peak)."""
        ...

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
    ) -> CpuBreakdownRaw:
        """CPU 분류 윈도우 평균 — user/system/iowait (jiffies LAG delta, counter reset 흡수)."""
        ...

    async def get_report_mount_capacity_batch(
        self,
        server_ids: list[int],
        end: datetime,
    ) -> dict[int, list[MountCapacityRaw]]:
        """N대 마운트별 용량 사이징 입력 — /api/assessment per-mount 디스크 축(worst-mount 로 접지 않음)."""
        ...

    async def get_report_memory_breakdown_batch(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, MemoryBreakdownRaw]:
        """N대 메모리 구성 윈도우 평균 — `get_report_memory_breakdown` 배치(GROUP BY server_id)."""
        ...

    async def get_report_cpu_breakdown_batch(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, CpuBreakdownRaw]:
        """N대 CPU 분류 윈도우 평균 — `get_report_cpu_breakdown` 배치(PARTITION BY server_id, GROUP BY server_id)."""
        ...

    async def get_environment_utilization(
        self,
        period_days: float,
        end: datetime,
        server_ids: list[int] | None = None,
    ) -> EnvironmentUtilizationRaw:
        """환경(또는 선택 N대) capacity-weighted 평균 활용률 — 자원 총량 가중 (sum(used) / sum(total))."""
        ...
