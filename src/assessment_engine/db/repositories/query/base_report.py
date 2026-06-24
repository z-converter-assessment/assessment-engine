"""Report aggregation 도메인 추상 인터페이스 — USE Method 통계 + 환경 활용률."""

from abc import ABC, abstractmethod
from datetime import datetime

from assessment_engine.db.dtos.outbound import (
    CpuBreakdownRaw,
    EnvironmentUtilizationRaw,
    MemoryBreakdownRaw,
    ReportMountUsageRaw,
    ReportRowRaw,
)


class BaseReportQueryRepository(ABC):
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
        """server_id -> (worst_mount, worst_mount_used_pct, worst_mount_days_until_full)."""

    @abstractmethod
    async def report_uptime_stats(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> dict[int, int]:
        """server_id -> period 안 boot_time 변경(재부팅) 횟수."""

    @abstractmethod
    async def report_agent_restart_stats(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> dict[int, int]:
        """server_id -> period 안 agent_started_at 변경(에이전트 재시작) 횟수.

        보고서 anchor+window 안 카운트 (#F10) — 호스트 상세 "시스템 안정성" 컬럼 표시.
        """

    @abstractmethod
    async def agent_restart_counts_recent(self, server_ids: list[int], since: datetime) -> dict[int, int]:
        """since 이후 server별 agent 재시작 횟수 — attention agent_unstable fixed 윈도우 (Redis sliding 대체)."""

    @abstractmethod
    async def report_disk_io_baseline(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> dict[int, tuple[int | None, float | None, float | None, float | None, float | None, float | None]]:
        """server_id -> (iops_baseline, throughput_kbps_baseline, iops_p95, iops_peak, kbps_p95, kbps_peak)."""

    @abstractmethod
    async def report_net_io_baseline(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> dict[int, tuple[float | None, float | None, float | None, float | None, float | None, float | None]]:
        """server_id -> (rx_kbps_baseline, tx_kbps_baseline, rx_p95, rx_peak, tx_p95, tx_peak)."""

    @abstractmethod
    async def report_mount_usage(
        self,
        server_id: int,
        period_days: float,
        end: datetime,
    ) -> list[ReportMountUsageRaw]:
        """마운트별 윈도우 평균 사용률 — 개별 보고서 스토리지 상세 (worst 1개 아닌 전체, 가상 mount 제외)."""

    @abstractmethod
    async def report_memory_breakdown(
        self,
        server_id: int,
        period_days: float,
        end: datetime,
    ) -> MemoryBreakdownRaw:
        """메모리 구성 윈도우 평균 — used/available/cached/buffers (전체 메모리 대비 %, 시점값 avg)."""

    @abstractmethod
    async def report_cpu_breakdown(
        self,
        server_id: int,
        period_days: float,
        end: datetime,
    ) -> CpuBreakdownRaw:
        """CPU 분류 윈도우 평균 — user/system/iowait (jiffies LAG delta, counter reset 흡수)."""

    @abstractmethod
    async def report_mount_usage_batch(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, list[ReportMountUsageRaw]]:
        """N대 마운트별 윈도우 평균 — `report_mount_usage` 배치(server_id IN). child fan-out 1회 조회 (A5)."""

    @abstractmethod
    async def report_memory_breakdown_batch(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, MemoryBreakdownRaw]:
        """N대 메모리 구성 윈도우 평균 — `report_memory_breakdown` 배치(GROUP BY server_id)."""

    @abstractmethod
    async def report_cpu_breakdown_batch(
        self,
        server_ids: list[int],
        period_days: float,
        end: datetime,
    ) -> dict[int, CpuBreakdownRaw]:
        """N대 CPU 분류 윈도우 평균 — `report_cpu_breakdown` 배치(PARTITION BY server_id, GROUP BY server_id)."""

    @abstractmethod
    async def environment_utilization(
        self,
        period_days: float,
        end: datetime,
        server_ids: list[int] | None = None,
    ) -> EnvironmentUtilizationRaw:
        """환경(또는 선택 N대) capacity-weighted 평균 활용률 — 자원 총량 가중 (Σused / Σtotal)."""
        ...
