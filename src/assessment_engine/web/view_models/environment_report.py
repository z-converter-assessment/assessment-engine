"""환경 보고서 ViewModel — environment scope 보고서 (전체 등록 서버 대상)."""

from dataclasses import dataclass, field
from datetime import datetime

from assessment_engine.json_types import JsonObject
from assessment_engine.web.view_models.attention import (
    ActionTargets,
    AttentionSignals,
    EnvironmentOverview,
)
from assessment_engine.web.view_models.metric import PeriodAssessment
from assessment_engine.web.view_models.report import ReportRowItem, ReportSummary
from assessment_engine.web.view_models.server import IpAddr, NetworkInterfaceInfo, StorageNode
from assessment_engine.web.view_models.topology import NetworkTopology


@dataclass
class ClassificationCount:
    """USE Method 분포 1 segment.

    label 은 `right_sizing.RECOMMENDATION_LABEL_KO` 단일 진실 — 보고서 전역 동일 어휘.
    pct 는 classification_dist 합 대비 %.
    """

    key: str
    label: str
    count: int
    color: str
    description: str = ""
    pct: float = 0.0


@dataclass
class OsCount:
    """OS 계층 그룹별 카운트 — family/distro/version 3단.

    distro = os_id, version = os_version, 미상은 "—".
    """

    family: str  # "Linux" | "Windows" | "기타"
    distro: str
    version: str
    count: int

    kernel_versions: str = "—"


@dataclass
class DistributionBar:
    """구성 분포 1 segment — OS family / 워크로드 카테고리 공용.

    pct 는 분포 내 최대 count 대비 막대 너비 % (mapper precompute).
    """

    label: str
    count: int
    pct: float = 0.0


@dataclass
class ServerInventorySnapshot:
    """개별 서버 보고서 인벤토리 — ServerDetail 충실 표시.

    IP 는 IPv4/IPv6 전체를 싣는다 (임의 1개 선택 금지). 식별자류는 customer 뷰에서 template 이 가린다.
    """

    hostname: str
    os_display: str
    os_codename: str | None
    kernel_version: str | None
    cpu_model: str | None
    cpu_cores: int | None
    mem_total_gb: float | None
    swap_total_gb: float | None
    disk_total_gb: int | None
    ip_internal: list[IpAddr]
    ip_external: list[IpAddr]
    boot_time: datetime | None
    agent_started_at: datetime | None
    last_seen_at: datetime | None
    agent_version: str | None
    composite_id: str | None
    machine_id: str | None
    is_online: bool
    public_id: str | None = None
    agent_id: str | None = None
    cpu_arch: str | None = None
    cpu_bits: int | None = None
    boot_firmware: str | None = None
    secure_boot: bool | None = None
    os_edition: str | None = None
    timezone: str | None = None


@dataclass
class MemoryBreakdown:
    """개별 보고서 메모리 구성 — 전체 대비 %, 평가 윈도우 평균."""

    used_pct: float | None
    available_pct: float | None
    cached_pct: float | None
    buffers_pct: float | None


@dataclass
class CpuBreakdown:
    """개별 보고서 CPU 분류 — cpu 시간 초 delta 기반 %, 평가 윈도우 평균."""

    user_pct: float | None
    system_pct: float | None
    iowait_pct: float | None


@dataclass
class ServiceHost:
    """서비스 구동 호스트 1개 — 서버 상세 링크용(`/servers/{public_id}`)."""

    hostname: str
    public_id: str


@dataclass
class ServiceNameCount:
    """서비스 구성 1행 — 서비스명·등장 서버 수·구동 호스트 (hosts 는 engineer 뷰만 노출)."""

    name: str
    count: int
    hosts: list[ServiceHost] = field(default_factory=list[ServiceHost])


@dataclass
class ServiceCatalogGroup:
    """서비스 구성 카드 — 워크로드 카테고리 1개 + 그 안 서비스명·개수.

    count 0 카테고리도 비우지 않고 싣는다 (#E9). total_count 는 services count 합.
    """

    category: str
    total_count: int = 0
    services: list[ServiceNameCount] = field(default_factory=list[ServiceNameCount])


@dataclass
class AttentionHostItem:
    """운영 신호 발화 호스트 — AttentionSignals 3 카테고리를 호스트 단위로 합성 (한 호스트가 여러 신호 발화 가능).

    right-sizing 분류와 독립된 축이다.
    """

    public_id: str
    hostname: str
    os_display: str
    gap_label: str | None
    os_eol_label: str | None
    restart_label: str | None
    active_count: int


@dataclass
class CapacityImminentItem:
    """바이트 또는 inode 소진이 임박한 마운트."""

    public_id: str
    hostname: str
    worst_mount: str
    days_until_full: int
    constraint_label: str = ""
    used_pct: float | None = None


@dataclass
class EnvironmentReportSummary:
    """환경 단위 보고서 — 단일·selection·환경 3 스코프 공용 양식.

    스코프 전용 필드는 나머지 스코프에서 None / 빈 list 로 남는다. view 는 'customer' | 'engineer'.
    """

    view: str
    time_range: str
    time_range_label: str
    anchor_at: datetime
    generated_at: datetime
    overview: EnvironmentOverview
    attention: AttentionSignals
    base: ReportSummary
    classification_dist: list[ClassificationCount]
    os_distribution: list[OsCount]
    top_risks: list[ReportRowItem]
    summary_bullets_env: list[str]
    os_family_dist: list[DistributionBar] = field(default_factory=list[DistributionBar])

    env_metrics: list[JsonObject] = field(default_factory=list[JsonObject])
    os_eol_count: int = 0
    os_eol_breakdown_label: str = ""
    agent_versions_label: str = ""
    topology: NetworkTopology | None = None

    trend: list[JsonObject] = field(default_factory=list[JsonObject])

    sat_trend: list[JsonObject] = field(default_factory=list[JsonObject])

    action: ActionTargets = field(default_factory=ActionTargets)
    service_catalog: list[ServiceCatalogGroup] = field(default_factory=list[ServiceCatalogGroup])
    server_inventory: ServerInventorySnapshot | None = None

    memory_breakdown: MemoryBreakdown | None = None
    cpu_breakdown: CpuBreakdown | None = None

    period_assessment: PeriodAssessment | None = None

    storage_tree: list[StorageNode] = field(default_factory=list[StorageNode])
    network_interfaces: list[NetworkInterfaceInfo] = field(default_factory=list[NetworkInterfaceInfo])

    attention_hosts: list[AttentionHostItem] = field(default_factory=list[AttentionHostItem])
    capacity_imminent: list[CapacityImminentItem] = field(default_factory=list[CapacityImminentItem])

    top_risks_count: int = 0
    attention_hosts_count: int = 0
    capacity_imminent_count: int = 0
