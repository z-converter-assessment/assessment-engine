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


@dataclass
class RiskServerItem:
    """리스크 상위 서버 — list 화면 카드 섹션. 24h USE 통계 + 디스크 latest 기반.

    risk_score: 정렬 기준 (높을수록 위험). 위험 분기 100/95/90/85/60/55 고정값,
                정상 분기는 max(cpu, mem, disk_max). 카드 헤더에 정렬 근거로 표시.
    primary_concern: 한국어 라벨 (카드 상단 배지) — "오프라인" / "스왑 활성" / "MEM p95 92%" / "정상" 등.
    badge_class: recommendation.py BADGE_CLASS와 같은 CSS 패밀리(`rec-{...}`).
    cpu/mem/disk_max_pct: 카드 안 도넛 3개(CPU/MEM/DISK) 채움. None은 회색 도넛 + "—" 표시.
    """
    public_id: str
    hostname: str
    risk_score: float          # 정렬 기준 + 카드 헤더 게이지 채움
    risk_score_color: str      # 게이지 바 색상 — 임계(≥85 빨강 / ≥55 노랑 / 그 외 초록), mapper 단일 결정 (P3)
    primary_concern: str       # "오프라인" | "스왑 활성" | "MEM p95 92%" 등
    badge_class: str           # rec-under_provisioned / rec-right_size 등
    cpu_p95_pct: float | None
    mem_p95_pct: float | None
    disk_max_pct: float | None  # 서버별 mount latest 중 max 사용률
    swap_used: bool
    is_online: bool


@dataclass
class DiskWarningItem:
    """디스크 사용률 임박 mount 1건 — 특정 서버 + 특정 mount."""
    public_id: str
    hostname: str
    mount: str
    used_pct: float       # 0~100
    free_gb: float
    total_gb: float
    last_metric_at: datetime  # 해당 mount의 latest 시점 — 운영자 stale 판단
    badge_class: str      # 90+ → rec-under_provisioned, 85~90 → rec-right_size


@dataclass
class GapWarningItem:
    """metric 발행 갭 — 한때 살아있다 끊긴 서버."""
    public_id: str
    hostname: str
    last_metric_at: datetime
    gap_minutes: int      # 표시 헬퍼 (template에서 계산 안 함 — P3)
    badge_class: str      # 30분+ → rec-under_provisioned, 5~30분 → rec-right_size


@dataclass
class AttentionSignals:
    """list 화면 "주의 필요 신호" 섹션 묶음 — risk_top과 시간 축·도메인 차별.

    disk_warnings: 마이그레이션 전 cleanup 직접 액션.
    gap_warnings: 모니터링 사각지대 (네트워크·VM resume 일시 끊김).
    """
    disk_warnings: list[DiskWarningItem]
    gap_warnings: list[GapWarningItem]

    @property
    def has_any(self) -> bool:
        return bool(self.disk_warnings or self.gap_warnings)


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

    recommendation: str          # enum 값
    recommendation_label: str    # 한국어
    badge_class: str             # CSS 클래스


@dataclass
class ReportSummary:
    """get_report 응답 — 행 list + KPI 집계 (KPI도 service 책임)."""
    rows: list[ReportRowItem]
    period_days: int
    total: int
    online: int
    over: int
    under: int