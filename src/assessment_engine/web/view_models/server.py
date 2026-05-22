"""서버 표시 ViewModel — list / detail / storage / network 페이지 + 인벤토리 단위 dataclass."""

from dataclasses import dataclass, field
from datetime import datetime

from assessment_engine.web.view_models.task import TaskSummaryItem


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
    # 분류 raw enum — list 필터링 단일 진실 (optimal / over_provisioned / under_provisioned /
    # idle / shutdown / insufficient_data). raws_period 부재 시 빈 문자열.
    provisioning_class: str = ""
    # 행별 마지막 task 요약. None 이면 발행 이력 없음 — 템플릿이 "—" 로 표시.
    last_task: TaskSummaryItem | None = None


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
    sorted_services: list[ServiceItem] = field(default_factory=list)  # P3: unit ASC 정렬
    sorted_listen_ports: list[ListenPortItem] = field(default_factory=list)  # P3: port ASC 정렬
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


# NetworkDetailResponse 는 metric.NetIoSnapshot 을 재사용 — interfaces 필드 type.
# import 순환 방지 위해 metric sub-module 에서 NetIoSnapshot 정의를 별도로 보유 후 본 모듈 import.
from assessment_engine.web.view_models.metric import NetIoSnapshot  # noqa: E402


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
