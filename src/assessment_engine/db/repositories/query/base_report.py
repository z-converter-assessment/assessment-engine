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
    async def report_agent_restart_stats(
        self,
        server_ids: list[int],
        period_days: int,
        end: datetime,
    ) -> dict[int, int]:
        """server_id -> period 안 agent_started_at 변경(에이전트 재시작) 횟수.

        report_uptime_stats(재부팅)와 동일 산식 — agent_started_at DISTINCT count - 1.
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
