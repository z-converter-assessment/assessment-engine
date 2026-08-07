"""Assessment 보고서 ViewModel — server scope 보고서 row + KPI 합계 + 요약."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ReportWorkloadGroup:
    """워크로드 카테고리별 제품명 묶음 — names_label 이 비면 listen 소켓만으로 탐지돼 제품명 미상(T15)."""

    category: str
    names_label: str = ""
    ports: list[str] = field(default_factory=list[str])  # "80/tcp" 형식


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
    os_family: str | None  # 실행 큐·페이징·await 은 양 OS 실측, 신호 이름만 OS 별로 갈린다
    os_display: str
    kernel_version: str | None
    internal_ip: str | None

    cpu_p95_pct: float | None
    cpu_avg_pct: float | None
    cpu_peak_pct: float | None
    mem_p95_pct: float | None
    mem_avg_pct: float | None
    mem_peak_pct: float | None

    recommendation: str  # USE Method enum 값
    recommendation_label: str
    badge_class: str

    risk_level: str  # "high" / "attention" / "normal" / "low_usage"
    risk_label: str  # "고위험" / "주의 필요" / "정상" / "저사용"
    risk_badge_class: str  # rec-* CSS 재사용

    # 정적 인벤토리 — 신규 등록 직후 등 미수집이면 None.
    cpu_cores: int | None = None
    mem_total_gb: float | None = None
    disk_total_gb: float | None = None

    # 코어당 정규화 전 raw (Linux procs_running / Windows Processor Queue Length).
    cpu_run_queue_p95: float | None = None

    iowait_p95_pct: float | None = None
    iowait_peak_pct: float | None = None

    worst_mount_used_pct: float | None = None  # most-full 마운트
    # assess_disk_capacity 가 분류에 쓴 것과 같은 마운트·runway.
    disk_capacity_driving_mount: str | None = None
    disk_capacity_runway_days: int | None = None

    uptime_days: int | None = None
    # 카운트는 보고서 창 안에서 발생한 것만 (전기간 아님).
    reboot_count: int = 0
    agent_restart_count: int = 0

    # peak/p95 비율 — 1.5 이상이면 변동 큼.
    cpu_variance_ratio: float | None = None
    mem_variance_ratio: float | None = None

    # baseline 은 창 평균 (disk·net 공통).
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

    # 우선순위·임계는 mapper._build_diagnosis 단일 진실.
    diagnosis: str = ""

    # 분류별 권장 조치 — mapper._build_recommendation_action 단일 진실(environment·single_report 공유).
    recommendation_action: str = ""

    # rollup_host 인과 종합 — CapacityWarningItem.root_cause_label 과 같은 단일 진실(화면 간 정합).
    root_cause_label: str = ""

    # 사이징 분류와 별개인 네트워크 품질 판정. 템플릿의 혼잡 분기는 net_congested 로만 —
    # 로컬라이즈 문자열("혼잡")을 비교하면 라벨을 바꿀 때 조용히 fall-through 한다.
    net_status_label: str = ""
    net_congested: bool = False

    # 포화 신호 = paging_major refault sustained (실제 압박 신호).
    mem_swap_paging: bool = False

    # 해당 OS 의 perflib 미발행 축만 미관측 — Windows 도 run queue/paging/await 를 실측하고
    # 카운터를 못 읽은 축만 coverage_gap 이다.
    is_partial: bool = False

    # 분류는 가진 데이터로 완결하고, 신뢰도 저하 요인만 이 채널로 분리 노출한다.
    # 문구는 assessment_display.build_host_confidence_notes 단일 진실.
    confidence_notes: list[str] = field(default_factory=list[str])

    # 구동 서비스 — 개별 서버 보고서에서만 렌더. N대 표·환경 보고서는 미사용.
    workload_groups: list[ReportWorkloadGroup] = field(default_factory=list[ReportWorkloadGroup])
    listen_ports_detail: list[ReportListenItem] = field(default_factory=list[ReportListenItem])
    # baseline OS 서비스 제외 — 환경 개요 서비스 뱃지와 같은 소스라 카운트가 맞는다.
    workload_categories: list[str] = field(default_factory=list[str])
    # 위를 SIGNATURE_CATEGORIES 로 좁힌 부분집합 — 세부 서버 목록 "구동 서비스" 열 전용.
    signature_workload_categories: list[str] = field(default_factory=list[str])
    # 카테고리별 특징 서비스명 (baseline·unknown 제외).
    workload_services: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    # 판정 기준 시각은 보고서 발행 시점 — live "오늘"이 아니다(스냅샷 불변).
    # os_eol = 매칭된 EOL 날짜라 경과·미래 무관하게 채워진다(운영신호 카드와 달리 EOL 경과 한정 아님).
    # os_eol_status: "ended"/"paid_only"/"security_only"/"full"/"unknown"(카탈로그 미수록·미매칭).
    os_eol: str = ""
    os_eol_status: str = ""
    # 표시 파생 — mappers.os_eol.os_eol_display 단일 진실.
    os_eol_label: str = ""
    os_eol_css: str = ""
    os_eol_title: str = ""
    os_eol_sort: int = 0
    # ServerListItem.has_operational_event(전기간)과 달리 보고서 창에 한정.
    # 세부 서버 목록만 채운다 — 환경 전체 보고서는 N+1 회피로 미채움.
    has_operational_event: bool = False


@dataclass
class ReportTotals:
    """양식 A 묶음 자원 총량 — 마이그레이션 capacity 산정 입력."""

    total_vcpus: int
    # 메모리만 float — int 로 접으면 0.5 GB 같은 값이 표시에서 왜곡된다.
    total_memory_gb: float
    total_disk_gb: int


@dataclass
class ReportSummary:
    """get_report 응답 — 행 list + KPI 집계 (KPI도 service 책임)."""

    rows: list[ReportRowItem]
    period_days: float
    total: int
    online: int
    risk_attention: int  # 주의 필요 = over_provisioned + idle
    risk_high: int  # 고위험 = under_provisioned
    # None = 전 서버가 평가 불가 (표시 단계에서 "—").
    avg_cpu_p95_pct: float | None = None
    avg_mem_p95_pct: float | None = None
    totals: ReportTotals = field(default_factory=lambda: ReportTotals(0, 0, 0))
    summary_bullets: list[str] = field(default_factory=list[str])
    # {"web": 8, "db": 5, ...} — service_classifier 카테고리 집계.
    role_distribution: dict[str, int] = field(default_factory=dict[str, int])
    # N대 선택 맥락 한 줄 요약 — "Linux 2 / Windows 1" · "web 2, db 1". 환경 보고서는 막대를 써 미사용.
    os_family_summary: str = ""
    workload_summary: str = ""
    # generated_at = 응답 합성 시각, anchor_at = 평가 윈도우 끝.
    generated_at: datetime | None = None
    anchor_at: datetime | None = None
