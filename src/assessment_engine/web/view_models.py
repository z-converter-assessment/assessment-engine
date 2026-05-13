from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DiskItem:
    name: str
    size_gb: float | None
    type: str | None


@dataclass
class MatchedPort:
    """서비스 유닛에 매핑된 listen 포트 1개. service_classifier.matched_ports의 결과 단위."""
    proto: str
    port: int


@dataclass
class ServiceItem:
    unit: str
    sub: str
    category: str
    ports: list[MatchedPort]
    display_name: str = ""


@dataclass
class ListenPortItem:
    proto: str
    addr: str
    port: int
    uid: int
    pid: int | None
    comm: str | None
    is_well_known: bool = False  # port <= 1024. mapper에서 계산 (P2)


# ---------- 서버 목록 ----------

@dataclass
class ServerListItem:
    id: int
    public_id: str
    hostname: str
    os_id: str | None
    os_version: str | None
    cpu_cores: int | None
    mem_total_gb: float | None
    storage_total_gb: float | None
    is_online: bool
    ip_external: list[str] | None
    services: list[ServiceItem] | None
    known_services: list[ServiceItem] = field(default_factory=list)
    show_unknown_badge: bool = False
    os_display: str = ""
    # 권장 조치 — 14일 USE Method 분류. 색은 도넛 _DONUT_SEGMENT_DEFS와 동기화. mapper 단일 결정 (P2).
    # raws_period 부재 시 빈 문자열 (도넛/분류 데이터 없음 — 페이지 2+ 또는 신규 등록 직후).
    recommendation_label: str = ""
    recommendation_color: str = ""


@dataclass
class AttentionRow:
    """주의 신호 카드 안 1행 — 5 카테고리(gap/disk/days_until_full/os_eol/agent_unstable) 통합 표현.

    P2 단일 진실 — 모든 표시 string은 mapper가 결정. template은 attribute access만.

    badge_class: badge CSS class (rec-* — base.html 단일 진실)
    badge_text:  badge 안 텍스트 ("5분", "92%", "EOL", ...)
    link_href:   상세 페이지 경로 ("/servers/{public_id}" 또는 "/servers/{public_id}/storage")
    link_text:   링크 표시 텍스트 (보통 hostname)
    mount_path:  mount path 별도 attribute — 긴 path는 CSS ellipsis + title hover (template에서 처리).
                 None이면 macro는 mount 표시 생략. mount path 없는 카테고리(gap/os_eol/agent_unstable)에 적용.
    meta_text:   meta 영역 정적 텍스트 (mount path 제외한 나머지 — "잔여 12 / 100 GB" 등)
    meta_at:     KST 표시 datetime (있으면 meta_text 뒤에 kst 필터로 표시). KST 변환은 template 필터만 (#F2)
    """
    badge_class: str
    badge_text: str
    link_href: str
    link_text: str
    mount_path: str | None = None
    meta_text: str = ""
    meta_at: datetime | None = None


@dataclass
class CapacityTriggerBadge:
    """자원 부족 trigger 하나 — 본문 한 줄에 3종 모두 표시 (active False는 비활성 시각).

    label: "스왑" / "CPU" / "메모리"
    color: 범례 색과 동기화 hex (mapper _CAPACITY_TRIGGER_COLORS 단일 진실)
    active: True면 본 자원 임계 초과 (색 채워진 배지), False면 비활성 (흐리게)
    bg_color/fg_color: 표시용 hex — mapper가 active/inactive 분기로 미리 결정 (P3 단일 진실).
                       템플릿은 inline 색 분기 안 함.
    """
    label: str
    color: str
    active: bool = True
    bg_color: str = ""
    fg_color: str = ""


@dataclass
class CapacityWarningItem:
    """14일 평균 자원 부족 서버 — 마이그레이션 capacity 산정 시 instance type 상향 검토.

    triggers: 활성화된 부족 자원 list. 한 서버에 여러 trigger 동시 가능 (예: CPU + 메모리).
    분류 조건:
    - swap_used=True → "스왑"
    - cpu_p95 >= CPU_UPSIZE_P95_PCT → "CPU"
    - mem_p95 >= MEM_UPSIZE_P95_PCT → "메모리"
    under_provisioned 분류라 최소 1개 trigger 존재.
    """
    public_id: str
    hostname: str
    cpu_p95_pct: float | None
    mem_p95_pct: float | None
    swap_used: bool
    triggers: list[CapacityTriggerBadge] = field(default_factory=list)


@dataclass
class AttentionCatalogEntry:
    """주의 신호 카드 상단 범례 1개 — 6 카탈로그 중 1개.

    label: 카테고리 이름 (sub-section 헤더와 동일)
    count: 발화 건수 (list 길이)
    active: count > 0 — 시각 강조 분기 (P3 — 템플릿 분기 금지)
    """
    label: str
    count: int
    active: bool


@dataclass
class AttentionSignals:
    """list 화면 통합 신호 카드 — 현재 시점·평가 기간 신호 묶음.

    disk_warnings: 마이그레이션 전 cleanup 직접 액션 (사용률 85%+ mount).
    gap_warnings: 모니터링 사각지대 (5분+ 끊김).
    capacity_warnings: 14일 평균 기준 자원 부족 의심 서버 (보고서·right-sizing 윈도우 동일).
    days_until_full_warnings: 디스크 잔여 30일 안 (fill_rate 추정).
    os_eol_warnings: OS EOL 임박/지남 (정적 매핑).
    agent_unstable: 1h 윈도우 안 재시작 임계 초과 서버.
    catalog: 카드 상단 범례 — 6 카탈로그 label·count·active (발화 0건도 노출).
    has_any: 6 카탈로그 중 1개라도 비어있지 않으면 True.
    """
    disk_warnings: list[AttentionRow]
    gap_warnings: list[AttentionRow]
    capacity_warnings: list[CapacityWarningItem] = field(default_factory=list)
    days_until_full_warnings: list[AttentionRow] = field(default_factory=list)
    os_eol_warnings: list[AttentionRow] = field(default_factory=list)
    agent_unstable: list[AttentionRow] = field(default_factory=list)

    @property
    def catalog(self) -> list[AttentionCatalogEntry]:
        """6 카탈로그 범례 — sub-section 표시 순서와 일치. 발화 0건 카테고리도 포함."""
        return [
            AttentionCatalogEntry("통신 끊김",       len(self.gap_warnings),              bool(self.gap_warnings)),
            AttentionCatalogEntry("디스크 사용률",   len(self.disk_warnings),             bool(self.disk_warnings)),
            AttentionCatalogEntry("언더 프로비저닝", len(self.capacity_warnings),         bool(self.capacity_warnings)),
            AttentionCatalogEntry(
                "디스크 추세",
                len(self.days_until_full_warnings),
                bool(self.days_until_full_warnings),
            ),
            AttentionCatalogEntry("OS 지원종료",     len(self.os_eol_warnings),           bool(self.os_eol_warnings)),
            AttentionCatalogEntry("에이전트 재시작", len(self.agent_unstable),            bool(self.agent_unstable)),
        ]

    @property
    def has_any(self) -> bool:
        return any([
            self.disk_warnings, self.gap_warnings,
            self.capacity_warnings, self.days_until_full_warnings,
            self.os_eol_warnings, self.agent_unstable,
        ])


@dataclass
class UtilizationBar:
    """환경 평균 자원 활용률 도넛 1개 — list 화면 상단.

    pct: None이면 표본 부재 ("—" 표시). bar_color: P3 임계 분기 결과 (mapper 단일 결정).
    dash_length: SVG stroke-dasharray 길이 (도넛 원호). 원주 ≈ 264, pct 0~100 → 0~264.
    템플릿이 산술 못 하므로 mapper가 미리 계산 (P3).
    """
    label: str             # "CPU" / "메모리" / "디스크"
    pct: float | None
    bar_color: str         # 임계별 hex 색 — mapper 결정 (P3 분기 금지)
    dash_length: float     # SVG dasharray — mapper 비례 산술 (P3)


@dataclass
class RiskDonutSegment:
    """위험도 분포 도넛 1 segment — 4개 카테고리(고위험·주의·저사용·정상) 중 하나.

    dash_length·dash_offset: SVG stroke-dasharray + stroke-dashoffset (다중 segment 누적).
    템플릿에서 산술 못 하므로 mapper가 미리 계산 (P3).
    """
    key: str               # "high" / "attention" / "low_usage" / "normal"
    label: str             # "고위험" / "주의" / "저사용" / "정상"
    color: str             # hex
    count: int             # 해당 카테고리 서버 수
    dash_length: float     # 본 segment 원호 길이
    dash_offset: float     # 시계방향 시작 위치 (이전 segments 누적 음수)


@dataclass
class EnvironmentOverview:
    """list 화면 상단 환경 요약 — 총 N대·온라인/오프라인·자원 합계·역할 분포·평균 활용률.

    total_memory_gb: float — 소수 1자리 (작은 환경에서 정수로 묶이면 정보 손실 — 예: 2.5 GB → 2 GB).
    total_disk_gb: int — TB·PB 스케일에서 소수점 의미 적음.
    utilization: 3개 막대(CPU·메모리·디스크) — 환경 평균 활용률. 표본 부재 시 빈 list.
    util_sample_size: 활용률 평균 표본 서버 수 (UI에 "N대 기준" 표시).
    """
    total: int
    online: int
    offline: int
    total_vcpus: int
    total_memory_gb: float
    total_disk_gb: int
    role_distribution: dict[str, int] = field(default_factory=dict)
    utilization: list[UtilizationBar] = field(default_factory=list)
    util_sample_size: int = 0
    risk_donut: list[RiskDonutSegment] = field(default_factory=list)
    risk_donut_total: int = 0          # 도넛 중심 표시용 (분류된 서버 수)
    risk_high_count: int = 0           # 도넛 중심 강조 — "위험 N대"


# ---------- 서버 상세 ----------


@dataclass
class ServerDetailResponse:
    id: int
    public_id: str
    machine_id: str
    hostname: str
    agent_version: str | None
    os_id: str | None
    os_version: str | None
    os_codename: str | None
    kernel_version: str | None
    cpu_cores: int | None
    cpu_model: str | None
    mem_total_gb: float | None
    swap_total_gb: float | None
    boot_time: datetime | None
    ip_internal: list[str]
    ip_external: list[str] | None
    disks: list[DiskItem]
    services: list[ServiceItem] | None
    listen_ports: list[ListenPortItem]
    last_seen_at: datetime | None
    # 이하 mapper(enrich_server_detail)에서 채우는 파생 필드 — default 필수 (dataclass 순서 제약)
    sorted_services: list[ServiceItem] = field(default_factory=list)       # P3: unit ASC 정렬
    sorted_listen_ports: list[ListenPortItem] = field(default_factory=list) # P3: port ASC 정렬
    known_services: list[ServiceItem] = field(default_factory=list)
    show_unknown_badge: bool = False
    key_listen_ports: list[ListenPortItem] = field(default_factory=list)
    os_display: str = ""
    cpu_display: str = ""
    disk_total_gb: float | None = None
    # P3: 템플릿이 `| length` 못 쓰도록 count를 mapper에서 미리 계산
    services_count: int = 0
    listen_ports_count: int = 0
    disks_count: int = 0


# ---------- 스토리지 (인벤토리 + 실시간 사용량) ----------

@dataclass
class MountUsageItem:
    mount: str
    fstype: str | None
    total_gb: float | None
    used_gb: float | None
    avail_gb: float | None
    usage_pct: float | None
    badge_class: str = ""
    bar_color: str = ""
    # mount가 어느 물리 디스크 위에 있는지 (major/minor 조인 결과). 매핑 실패 시 None.
    # mapper(to_storage_detail)에서 inventory.disks와 (major) 매칭으로 채움.
    device_name: str | None = None


@dataclass
class StorageDetailResponse:
    server_id: int
    public_id: str
    hostname: str
    disks: list[DiskItem]
    mounts: list[MountUsageItem]
    snapshot_at: datetime | None
    inventory_at: datetime | None


# ---------- 메트릭 대시보드 스냅샷 (AJAX /metrics/latest) ----------

@dataclass
class CpuSnapshot:
    usage_pct: float | None
    user_pct: float | None
    system_pct: float | None
    iowait_pct: float | None


@dataclass
class MemSnapshot:
    total_kb: int | None
    used_kb: int | None        # total - available
    available_kb: int | None
    cached_kb: int | None
    buffers_kb: int | None
    usage_pct: float | None
    # stacked bar 표시용 비율 (P5: 클라이언트가 다시 계산하지 않음). metrics_calculator에서 산출.
    cached_pct: float | None = None
    buffers_pct: float | None = None


@dataclass
class SwapSnapshot:
    total_kb: int | None
    used_kb: int | None
    usage_pct: float | None


@dataclass
class DiskIoSnapshot:
    device: str
    read_iops: float | None
    write_iops: float | None
    read_kbps: float | None
    write_kbps: float | None


@dataclass
class NetIoSnapshot:
    interface: str
    rx_kbps: float | None
    tx_kbps: float | None
    rx_pps: float | None
    tx_pps: float | None


@dataclass
class MountDashSnapshot:
    mount: str
    total_gb: float | None
    used_gb: float | None
    avail_gb: float | None
    usage_pct: float | None


@dataclass
class MetricDashboard:
    collected_at: datetime | None
    cpu: CpuSnapshot | None
    load_1m: float | None
    load_5m: float | None
    load_15m: float | None
    memory: MemSnapshot | None
    swap: SwapSnapshot | None
    disk_io_phys: list[DiskIoSnapshot]
    disk_io_lvm: list[DiskIoSnapshot]
    disk_io_part: list[DiskIoSnapshot]
    net_io: list[NetIoSnapshot]
    mounts: list[MountDashSnapshot]


# ---------- 네트워크 (IPs + 실시간 인터페이스 현황) ----------
# NetIoSnapshot을 재사용 — 필드 구조가 동일하므로 별도 타입 불필요

@dataclass
class NetworkDetailResponse:
    server_id: int
    public_id: str
    hostname: str
    ip_internal: list[str]
    ip_external: list[str] | None
    interfaces: list[NetIoSnapshot]
    inventory_at: datetime | None
    snapshot_at: datetime | None


# ---------- 수집 상태 ----------

@dataclass
class CollectionStatusItem:
    last_metric_at: datetime | None
    last_inventory_at: datetime | None
    is_online: bool


# ---------- 시계열 ----------

@dataclass
class MetricSeriesItem:
    collected_at: datetime
    value: float | None
    dimension: str | None


# ---------- Assessment 보고서 ----------

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
    cpu_peak_pct: float | None
    mem_p95_pct: float | None
    mem_peak_pct: float | None
    load_15m_max: float | None
    swap_used: bool

    recommendation: str          # USE Method enum 값 (양식 B에 노출)
    recommendation_label: str    # 한국어
    badge_class: str             # CSS 클래스 (USE 분류용)

    # 옵션 B 매핑 — UI 친화 위험도 (양식 A KPI·표 노출)
    risk_level: str              # "high" / "attention" / "normal" / "low_usage"
    risk_label: str              # "고위험" / "주의 필요" / "정상" / "저사용"
    risk_badge_class: str        # rec-under_provisioned / rec-right_size 등 재사용

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

    # 임계값 분류 색 (P3 — 템플릿 산술·분기 금지. mapper에서 미리 계산)
    # 모두 #b91c1c (danger) / #92400e (warn) / #94a3b8 (muted) / #1e293b (default) hex 중 하나.
    saturation_color: str = "#94a3b8"
    cpu_variance_color: str = "#1e293b"
    mem_variance_color: str = "#94a3b8"
    worst_mount_days_color: str = "#64748b"      # days_until_full 표시색 — 30일 이하 시 danger
    reboot_count_color: str = "#94a3b8"          # 3회 이상 시 danger


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
    risk_attention: int          # 주의 필요 — over_provisioned·idle·shutdown 합산
    risk_high: int               # 고위험 — under_provisioned
    totals: ReportTotals = field(default_factory=lambda: ReportTotals(0, 0, 0))
    # 양식 A 정성 요약 — mapper에서 자동 생성 (P2). 컨설턴트가 고객 보고서 첨부 시 활용.
    summary_bullets: list[str] = field(default_factory=list)
    # 양식 A 상단 역할 분포 — {"web": 8, "db": 5, "cache": 3, ...}. service_classifier 카테고리 집계.
    role_distribution: dict[str, int] = field(default_factory=dict)