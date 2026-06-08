"""서버 표시 ViewModel — list / detail / storage / network 페이지 + 인벤토리 단위 dataclass."""

from dataclasses import dataclass, field
from datetime import datetime

from assessment_engine.web.view_models.task import TaskSummaryItem


@dataclass
class DiskItem:
    name: str
    size_gb: float | None


@dataclass
class VolumeItem:
    """파일시스템(논리 볼륨) — inventory.mounts 기준. 물리 디스크(DiskItem)와 별개 축.

    양 OS 일관 표시 (Linux: / ext4 등, Windows: C:\\ ntfs 등). fstype 명시.
    """

    mount: str
    fstype: str | None
    total_gb: float | None


@dataclass
class IpAddr:
    """IP 주소 + IPv4 여부 — detail 화면 IPv4 우선 정렬·강조용 (mapper precompute, P3 회피)."""

    value: str
    is_ipv4: bool


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
    # 같은 카테고리 서비스 개수 (서버목록 뱃지 "db 2" 표시 — 환경요약 role 인스턴스 수와 일관).
    # 런타임 스택(container)은 호스트당 1 (docker+containerd 를 부풀리지 않음). _dedup_known 이 set.
    category_count: int = 1


@dataclass
class ListenPortItem:
    proto: str
    addr: str
    port: int
    uid: int | None  # Windows agent null 호환 (POSIX uid 미존재)
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
    # 권장 조치 — 7일 USE Method 분류. 색은 도넛 _DONUT_SEGMENT_DEFS와 동기화. mapper 단일 결정 (P2).
    # raws_period 부재 시 빈 문자열 (도넛/분류 데이터 없음 — 페이지 2+ 또는 신규 등록 직후).
    recommendation_label: str = ""
    recommendation_color: str = ""
    # 분류 raw enum — list 필터링 단일 진실 (optimal / over_provisioned / under_provisioned /
    # idle / shutdown / insufficient_data). raws_period 부재 시 빈 문자열.
    provisioning_class: str = ""
    # OS distro(endoflife 카탈로그 product slug) — OS 필터 단일 진실.
    # os_id_to_distro(os_id) 정규화 (rocky->rocky-linux).
    os_distro: str = ""
    # 행별 마지막 task 요약. None 이면 발행 이력 없음 — 템플릿이 "—" 로 표시.
    last_task: TaskSummaryItem | None = None


@dataclass
class ServerDetailResponse:
    id: int
    public_id: str
    composite_id: str
    machine_id: str | None  # raw machine-id 표시 전용
    hostname: str
    agent_version: str | None
    os_family: str | None  # "linux" | "windows" — Windows 미측정 메트릭 N/A 표시 분기 (표시 경계)
    os_id: str | None
    os_version: str | None
    os_codename: str | None
    kernel_version: str | None
    cpu_cores: int | None
    cpu_model: str | None
    mem_total_gb: float | None
    swap_total_gb: float | None
    boot_time: datetime | None
    agent_started_at: datetime | None
    ip_internal: list[IpAddr]
    ip_external: list[IpAddr] | None
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
    # 파일시스템(논리 볼륨) 항목 — inventory.mounts 기준. 물리 디스크(disks)와 별개 축, 양 OS 일관(fstype 명시).
    volumes: list[VolumeItem] = field(default_factory=list)
    volume_total_gb: float | None = None
    volumes_count: int = 0


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


@dataclass
class StorageDetailResponse:
    server_id: int
    public_id: str
    hostname: str
    disks: list[DiskItem]
    mounts: list[MountUsageItem]
    fs_total_gb: float | None  # 파일시스템(마운트) total_gb 합 — 현재 상태 요약
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
    ip_internal: list[IpAddr]
    ip_external: list[IpAddr] | None
    interfaces: list[NetIoSnapshot]
    inventory_at: datetime | None
    snapshot_at: datetime | None
