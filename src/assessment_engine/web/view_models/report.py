"""Assessment 보고서 ViewModel — server scope 보고서 row + KPI 합계 + 요약."""

from dataclasses import dataclass, field


@dataclass
class ReportRowItem:
    """ReportRowRaw + 표시 파생. 모든 표시 결정(role/recommendation/badge)은 mapper에서 채움 (P2)."""

    server_id: int
    public_id: str
    hostname: str
    role: str
    is_online: bool
    os_display: str
    kernel_version: str | None
    internal_ip: str | None

    cpu_p95_pct: float | None
    cpu_avg_pct: float | None
    cpu_peak_pct: float | None
    mem_p95_pct: float | None
    mem_avg_pct: float | None
    mem_peak_pct: float | None
    load_15m_max: float | None
    swap_used: bool

    recommendation: str  # USE Method enum 값 (양식 B에 노출)
    recommendation_label: str  # 한국어
    badge_class: str  # CSS 클래스 (USE 분류용)

    # 옵션 B 매핑 — UI 친화 위험도 (양식 A KPI·표 노출)
    risk_level: str  # "high" / "attention" / "normal" / "low_usage"
    risk_label: str  # "고위험" / "주의 필요" / "정상" / "저사용"
    risk_badge_class: str  # rec-under_provisioned / rec-right_size 등 재사용

    # I/O wait — 디스크 병목 신호 (양식 B 컬럼)
    iowait_p95_pct: float | None = None
    iowait_peak_pct: float | None = None

    # Mount 최악 — 서버 안에서 가장 채워진 마운트 1건 (양식 B 컬럼)
    worst_mount: str | None = None
    worst_mount_used_pct: float | None = None
    worst_mount_days_until_full: int | None = None

    # Uptime + 재부팅 (양식 B 컬럼)
    uptime_days: int | None = None
    reboot_count: int = 0

    # Saturation — load_15m_max / cpu_cores. 1 이상이면 saturated. mapper에서 계산.
    saturation_ratio: float | None = None

    # 이상치 변동성 — peak/p95 비율. 1.5 이상이면 변동 큼.
    cpu_variance_ratio: float | None = None
    mem_variance_ratio: float | None = None

    # Disk I/O — baseline(평균) + p95 + peak (양식 B 컬럼 + inventory-export)
    disk_iops_baseline: int | None = None
    disk_iops_p95: float | None = None
    disk_iops_peak: float | None = None
    disk_throughput_kbps: float | None = None
    disk_throughput_kbps_p95: float | None = None
    disk_throughput_kbps_peak: float | None = None

    # Net I/O — baseline(평균) + p95 + peak (양식 B 컬럼 + inventory-export)
    net_rx_kbps: float | None = None
    net_rx_kbps_p95: float | None = None
    net_rx_kbps_peak: float | None = None
    net_tx_kbps: float | None = None
    net_tx_kbps_p95: float | None = None
    net_tx_kbps_peak: float | None = None

    # 진단 텍스트 — saturation·variance·iowait·disk·swap 종합 자동 진단 (양식 B "판단" 컬럼)
    # mapper.build_diagnosis 결정. 우선순위: 메모리 압박 → 디스크 병목 → CPU saturation → 변동성 → 적정
    diagnosis: str = ""

    # under_provisioned 분류 시 어떤 trigger 가 hit 됐는지 한국어 권고 (양식 A "권고" 컬럼).
    # USE Method 5 trigger 중 active 만 결합 (예: "메모리 증설 (스왑 발생) / CPU 증설").
    # 비-under 분류 시 빈 문자열 — template 에서 분류별 폴백 메시지.
    under_provisioned_reason: str = ""

    # 임계값 분류 색 (P3 — 템플릿 산술·분기 금지. mapper에서 미리 계산)
    # 모두 #b91c1c (danger) / #92400e (warn) / #94a3b8 (muted) / #1e293b (default) hex 중 하나.
    saturation_color: str = "#94a3b8"
    cpu_variance_color: str = "#1e293b"
    mem_variance_color: str = "#94a3b8"
    worst_mount_days_color: str = "#64748b"  # days_until_full 표시색 — 30일 이하 시 danger
    reboot_count_color: str = "#94a3b8"  # 3회 이상 시 danger


@dataclass
class ReportTotals:
    """양식 A 묶음 자원 총량 — 마이그레이션 capacity 산정 입력."""

    total_vcpus: int
    total_memory_gb: int
    total_disk_gb: int


@dataclass
class ReportSummary:
    """get_report 응답 — 행 list + KPI 집계 (KPI도 service 책임)."""

    rows: list[ReportRowItem]
    period_days: int
    total: int
    online: int
    risk_attention: int  # 주의 필요 — over_provisioned·idle·shutdown 합산
    risk_high: int  # 고위험 — under_provisioned
    # 환경 활용률 평균 KPI — 고객 보고서가 "환경 전체 활용도"를 한눈에 보여주기 위함.
    # None은 표시 단계에서 "—"로 fallback (모든 서버가 평가 불가일 때).
    avg_cpu_p95_pct: float | None = None
    avg_mem_p95_pct: float | None = None
    totals: ReportTotals = field(default_factory=lambda: ReportTotals(0, 0, 0))
    # 양식 A 정성 요약 — mapper에서 자동 생성 (P2). 컨설턴트가 고객 보고서 첨부 시 활용.
    summary_bullets: list[str] = field(default_factory=list)
    # 양식 A 상단 역할 분포 — {"web": 8, "db": 5, "cache": 3, ...}. service_classifier 카테고리 집계.
    role_distribution: dict[str, int] = field(default_factory=dict)
