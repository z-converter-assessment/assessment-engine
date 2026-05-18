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
    # 행별 마지막 task 요약. None 이면 발행 이력 없음 — 템플릿이 "—" 로 표시.
    last_task: "TaskSummaryItem | None" = None


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

    triggers: USE Method classify 입력 5 trigger 와 1:1 정합 (스왑/CPU/메모리/Load/디스크).
    - swap_used=True → "스왑"
    - cpu_p95 >= CPU_UPSIZE_P95_PCT → "CPU"
    - mem_p95 >= MEM_UPSIZE_P95_PCT → "메모리"
    - load_15m / cpu_cores >= CPU_SATURATION_LOAD_RATIO → "Load"
    - disk_used >= DISK_CAPACITY_UPSIZE_PCT 또는 iowait_p95 >= IOWAIT_UPSIZE_PCT → "디스크"
    under_provisioned 분류라 최소 1개 trigger 존재. 표시는 뱃지만 (구체값 메타 표시 안 함).
    """
    public_id: str
    hostname: str
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
    description: str = ""  # 임계 근거 한국어 보조 ("≥ 85%" 등)


@dataclass
class AttentionSignals:
    """list 화면 운영 신호 카드 — 모니터링·시스템 운영 이상 3 카테고리 (USE Method 와 완전 분리).

    USE Method(자원 평가)에서 다루지 못하는 인프라 이상만 표시.
    디스크(capacity·IO)는 USE Method classify 에 통합 — 본 catalog 에서 제외 (중복 회피).

    gap_warnings: 모니터링 사각지대 (5분+ 끊김).
    os_eol_warnings: OS EOL 임박/지남 (정적 매핑).
    agent_unstable: 1h 윈도우 안 재시작 임계 초과 서버.
    catalog: 카드 상단 범례 — 3 카탈로그 label·count·active (발화 0건도 노출).
    has_any: 3 카탈로그 중 1개라도 비어있지 않으면 True.
    """
    gap_warnings: list[AttentionRow]
    os_eol_warnings: list[AttentionRow] = field(default_factory=list)
    agent_unstable: list[AttentionRow] = field(default_factory=list)

    @property
    def catalog(self) -> list[AttentionCatalogEntry]:
        """3 카탈로그 범례 — USE Method 외 시스템 운영 이상. 발화 0건 카테고리도 포함."""
        return [
            AttentionCatalogEntry("통신 끊김", len(self.gap_warnings), bool(self.gap_warnings)),
            AttentionCatalogEntry("OS 지원종료", len(self.os_eol_warnings), bool(self.os_eol_warnings)),
            AttentionCatalogEntry("에이전트 재시작", len(self.agent_unstable), bool(self.agent_unstable)),
        ]

    @property
    def has_any(self) -> bool:
        return any([self.gap_warnings, self.os_eol_warnings, self.agent_unstable])


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
    """USE Method 분포 도넛 1 segment — recommend enum 6 분류 1:1.

    dash_length·dash_offset: SVG stroke-dasharray + stroke-dashoffset (다중 segment 누적).
    템플릿에서 산술 못 하므로 mapper가 미리 계산 (P3).
    description: 범례 옆 간단 보조 설명 (mapper 단일 진실).
    """
    key: str               # recommend enum (영어)
    label: str             # 표시 라벨 (영어 enum 그대로)
    color: str             # hex
    count: int             # 해당 카테고리 서버 수
    dash_length: float     # 본 segment 원호 길이
    dash_offset: float     # 시계방향 시작 위치 (이전 segments 누적 음수)
    description: str = ""  # 한국어 보조 설명 ("자원 부족 (사양 상향)" 등)


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
    # USE Method 분포 도넛 아래 표시 — 자원 부족(under_provisioned) 호스트 trigger·메타 상세.
    under_provisioned_hosts: list[CapacityWarningItem] = field(default_factory=list)
    under_provisioned_hosts_count: int = 0  # 템플릿 P3 회피 — mapper precompute (#E1 P3)


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
    cpu_avg_pct: float | None
    cpu_peak_pct: float | None
    mem_p95_pct: float | None
    mem_avg_pct: float | None
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

    # under_provisioned 분류 시 어떤 trigger 가 hit 됐는지 한국어 권고 (양식 A "권고" 컬럼).
    # USE Method 5 trigger 중 active 만 결합 (예: "메모리 증설 (스왑 발생) / CPU 증설").
    # 비-under 분류 시 빈 문자열 — template 에서 분류별 폴백 메시지.
    under_provisioned_reason: str = ""

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
    # 환경 활용률 평균 KPI — 고객 보고서가 "환경 전체 활용도"를 한눈에 보여주기 위함.
    # None은 표시 단계에서 "—"로 fallback (모든 서버가 평가 불가일 때).
    avg_cpu_p95_pct: float | None = None
    avg_mem_p95_pct: float | None = None
    totals: ReportTotals = field(default_factory=lambda: ReportTotals(0, 0, 0))
    # 양식 A 정성 요약 — mapper에서 자동 생성 (P2). 컨설턴트가 고객 보고서 첨부 시 활용.
    summary_bullets: list[str] = field(default_factory=list)
    # 양식 A 상단 역할 분포 — {"web": 8, "db": 5, "cache": 3, ...}. service_classifier 카테고리 집계.
    role_distribution: dict[str, int] = field(default_factory=dict)


@dataclass
class ClassificationCount:
    """USE Method 분포 1 segment — 환경 보고서 전용 (양식 A/B 공통).

    label 은 recommend enum 영어 그대로 (대시보드 도넛과 단일 진실).
    description: 보고서 분포 막대 옆 간단 보조 설명.
    pct: 본 segment 가 전체 classification_dist 중 차지하는 % (mapper precompute, P3 회피).
    """
    key: str               # recommend enum
    label: str             # 영어 enum 그대로
    count: int
    color: str             # hex 색
    description: str = ""  # 한국어 보조 설명
    pct: float = 0.0       # 0~100 — mapper 가 _count_classifications 직후 채움


@dataclass
class OsCount:
    """OS distro·version 그룹별 카운트 — 환경 보고서 OS 분포 섹션."""
    os_display: str   # mapper `_os_display` 결과
    count: int


@dataclass
class AttentionHostItem:
    """운영 신호 발화 호스트 — 통신 끊김 / OS EOL / 에이전트 재시작 빈번 중 1개 이상 hit.

    AttentionSignals 3 카테고리 (gap_warnings / os_eol_warnings / agent_unstable) 를
    호스트 기준 unified 합성 — 동일 호스트가 여러 신호 발화 시 한 row 안에 표시.
    engineer 보고서 즉시 점검 list (Right-sizing 분류와 독립).
    """
    public_id: str
    hostname: str
    os_display: str
    # 카테고리별 발화 메타 — None 이면 비활성. mapper 결정 (P2).
    gap_label: str | None        # "5분" — gap_warnings badge_text
    os_eol_label: str | None     # "centos 7 · EOL 2024-06-30" — os_eol_warnings meta_text
    restart_label: str | None    # "12회" — agent_unstable badge_text
    active_count: int            # 활성 신호 카운트 (1~3)


@dataclass
class CapacityImminentItem:
    """디스크 capacity 임박 호스트 — worst_mount_days_until_full <= 30 (engineer 보고서).

    linear projection 기반 — 30일 안 디스크 full 위험. 운영 계획 입력.
    """
    public_id: str
    hostname: str
    worst_mount: str
    days_until_full: int
    used_pct: float | None


@dataclass
class InsufficientHostItem:
    """평가 표본 부족 호스트 — 엔지니어 보고서 별도 list (운영 액션: 에이전트 점검/메트릭 수집 확인).

    reason: 표본 부족 원인 ("CPU 메트릭 없음" / "메모리 메트릭 없음" / "둘 다 없음").
    """
    public_id: str
    hostname: str
    os_display: str
    reason: str


@dataclass
class EnvironmentReportSummary:
    """환경 단위 보고서 (전체 등록 서버 대상) — server scope ReportSummary 와 별도 양식.

    server scope 보고서: row 단위 검토 중심 (선택 N대 상세).
    environment scope 보고서: high-level overview·분류 분포·top risk·OS 분포 중심.
    view ('customer'|'engineer') 분기 — summary_bullets_env 가 view 별 다른 텍스트.
    time_range/anchor_at: AI 진단과 동일 윈도우 매트릭스 (15m~30d) + 운영자 명시 anchor.
    """
    view: str
    time_range: str                # "15m"/"1h"/"6h"/"24h"/"7d"/"14d"/"30d"
    time_range_label: str          # "15분"/"1시간"/...  한국어 표시 단일 진실 (mapper)
    anchor_at: datetime            # 분석 기준 시각 (보고서 본문 끝점)
    generated_at: datetime         # 응답 합성 시각 (DB 저장·UI 표시용)
    overview: EnvironmentOverview          # 기존 list 페이지와 동일 source (utilization 3 bar 포함)
    attention: AttentionSignals            # 기존 6 카탈로그 (disk/gap/capacity/days/os_eol/agent_unstable)
    base: ReportSummary                    # 전체 서버 raw aggregation 결과 (KPI·totals·rows 전부)
    classification_dist: list[ClassificationCount]
    os_distribution: list[OsCount]
    top_risks: list[ReportRowItem]         # base.rows 위험도 정렬 Top N (기본 5)
    summary_bullets_env: list[str]         # 환경 단위 view 별 정성 요약
    under_provisioned_hosts: list[CapacityWarningItem] = field(default_factory=list)
    # 엔지니어 보고서 전용 — 운영 신호 발화 호스트 통합 list (gap / os_eol / agent_unstable).
    attention_hosts: list[AttentionHostItem] = field(default_factory=list)
    # 엔지니어 보고서 전용 — 디스크 capacity 임박 (30일 안 full 위험, linear projection).
    capacity_imminent: list[CapacityImminentItem] = field(default_factory=list)
    # 엔지니어 보고서 전용 — 평가 표본 부족 호스트 (에이전트 점검 대상).
    insufficient_hosts: list[InsufficientHostItem] = field(default_factory=list)
    # 템플릿 P3 회피 precompute count — mapper 가 list len 단일 합성 (#E1 P3).
    top_risks_count: int = 0
    attention_hosts_count: int = 0
    capacity_imminent_count: int = 0
    insufficient_hosts_count: int = 0
    under_provisioned_hosts_count: int = 0


# ---------- Task 표시 ----------

@dataclass
class TaskSummaryItem:
    """list / detail row 단위 task 요약 — 한 줄 표시.

    상태 표현은 mapper 단일 결정 (P2):
    - badge_class: rec-{key} CSS class. success / failure / pending / unknown
    - badge_label: 한글 표시 ("성공" / "실패" / "진행 중" / "—")
    - failure_label: failure_reason 한글 (실패 시만)
    """
    task_id: str
    task_type: str
    status: str
    badge_class: str
    badge_label: str
    failure_label: str | None
    created_at: datetime
    completed_at: datetime | None
    duration_ms: int | None


@dataclass
class TaskDetailItem:
    """단일 task 상세 — modal / API 응답. 디버깅 데이터(tail) 포함."""
    task_id: str
    target_public_id: str | None
    target_hostname:  str | None
    task_type: str
    status: str
    badge_class: str
    badge_label: str
    failure_reason: str | None
    failure_label:  str | None
    exit_code: int | None
    duration_ms: int | None
    created_at: datetime
    completed_at: datetime | None
    stdout_tail: str | None
    stderr_tail: str | None
    params: dict | None = None  # install task 발행 파라미터 ({zdm_ip, zdm_user} 등) — modal 노출
