"""Assessment 보고서 ViewModel — server scope 보고서 row + KPI 합계 + 요약."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ReportWorkloadGroup:
    """워크로드 카테고리별 제품명 묶음 — names_label 이 비면 listen 소켓만으로 탐지돼 제품명 미상(T15)."""

    category: str
    names_label: str = ""
    ports: list[str] = field(default_factory=list[str])


@dataclass
class ReportListenItem:
    """listen 소켓 1행 (engineer view) — raw listen_ports 표시본."""

    port: int
    proto: str
    comm: str = ""
    addr: str = ""
    uid: int | None = None
    pid: int | None = None


@dataclass
class ReportRowItem:
    """ReportRowRaw + 표시 파생. 모든 표시 결정(role/recommendation/badge)은 mapper에서 채움 (P2)."""

    server_id: int
    public_id: str
    hostname: str
    role: str
    is_online: bool
    os_family: str | None
    os_display: str
    kernel_version: str | None
    internal_ip: str | None

    cpu_p95_pct: float | None
    cpu_avg_pct: float | None
    cpu_peak_pct: float | None
    mem_p95_pct: float | None
    mem_avg_pct: float | None
    mem_peak_pct: float | None

    recommendation: str
    recommendation_label: str
    badge_class: str

    risk_level: str
    risk_label: str
    risk_badge_class: str

    cpu_cores: int | None = None
    mem_total_gb: float | None = None
    disk_total_gb: float | None = None

    # 코어당 정규화 전 raw (Linux procs_running / Windows Processor Queue Length).
    cpu_run_queue_p95: float | None = None

    iowait_p95_pct: float | None = None
    iowait_peak_pct: float | None = None

    worst_mount_used_pct: float | None = None

    disk_capacity_driving_mount: str | None = None
    disk_capacity_runway_days: int | None = None

    uptime_days: int | None = None

    reboot_count: int = 0
    agent_restart_count: int = 0

    cpu_variance_ratio: float | None = None
    mem_variance_ratio: float | None = None

    disk_iops_baseline: int | None = None
    disk_iops_p95: float | None = None
    disk_iops_peak: float | None = None
    disk_throughput_kbps: float | None = None
    disk_throughput_kbps_p95: float | None = None
    disk_throughput_kbps_peak: float | None = None

    net_rx_kbps: float | None = None
    net_rx_kbps_p95: float | None = None
    net_rx_kbps_peak: float | None = None
    net_tx_kbps: float | None = None
    net_tx_kbps_p95: float | None = None
    net_tx_kbps_peak: float | None = None

    diagnosis: str = ""

    recommendation_action: str = ""

    root_cause_label: str = ""

    net_status_label: str = ""
    net_congested: bool = False

    mem_swap_paging: bool = False

    # 해당 OS 의 perflib 미발행 축만 미관측 — Windows 도 run queue/paging/await 를 실측하고

    is_partial: bool = False

    # 분류는 가진 데이터로 완결하고, 신뢰도 저하 요인만 이 채널로 분리 노출한다.

    confidence_notes: list[str] = field(default_factory=list[str])

    workload_groups: list[ReportWorkloadGroup] = field(default_factory=list[ReportWorkloadGroup])
    listen_ports_detail: list[ReportListenItem] = field(default_factory=list[ReportListenItem])

    workload_categories: list[str] = field(default_factory=list[str])

    signature_workload_categories: list[str] = field(default_factory=list[str])

    workload_services: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    os_eol: str = ""
    os_eol_status: str = ""

    os_eol_label: str = ""
    os_eol_css: str = ""
    os_eol_title: str = ""
    os_eol_sort: int = 0

    has_operational_event: bool = False


@dataclass
class ReportTotals:
    """양식 A 묶음 자원 총량 — 마이그레이션 capacity 산정 입력."""

    total_vcpus: int

    total_memory_gb: float
    total_disk_gb: int


@dataclass
class ReportSummary:
    """get_report 응답 — 행 list + KPI 집계 (KPI도 service 책임)."""

    rows: list[ReportRowItem]
    period_days: float
    total: int
    online: int
    risk_attention: int
    risk_high: int

    avg_cpu_p95_pct: float | None = None
    avg_mem_p95_pct: float | None = None
    totals: ReportTotals = field(default_factory=lambda: ReportTotals(0, 0, 0))
    summary_bullets: list[str] = field(default_factory=list[str])

    role_distribution: dict[str, int] = field(default_factory=dict[str, int])
    # N대 선택 맥락 한 줄 요약 — "Linux 2 / Windows 1" · "web 2, db 1". 환경 보고서는 막대를 써 미사용.
    os_family_summary: str = ""
    workload_summary: str = ""

    generated_at: datetime | None = None
    anchor_at: datetime | None = None
