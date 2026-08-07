"""서버 표시 ViewModel — list / detail / storage / network 페이지 + 인벤토리 단위 dataclass."""

from dataclasses import dataclass, field
from datetime import datetime

from assessment_engine.domain.service_classifier import MatchedPort
from assessment_engine.web.view_models.metric import NetIoSnapshot
from assessment_engine.web.view_models.task import TaskSummaryItem


@dataclass
class DiskItem:
    name: str
    size_gb: float | None


@dataclass
class VolumeItem:
    """파일시스템(논리 볼륨) — block_devices 중 마운트된 데이터 볼륨 노드. 물리 디스크(DiskItem)와 별개 축."""

    mount: str
    fstype: str | None
    total_gb: float | None


@dataclass
class IpAddr:
    """detail 화면 IPv4 우선 정렬·강조를 위해 mapper 가 미리 채우는 주소 단위."""

    value: str
    is_ipv4: bool


@dataclass
class ServiceBadgeRef:
    """서비스 뱃지 카탈로그 1행 — SERVICE_CATALOG 파생. category 는 화면 뱃지에 뜨는 키 텍스트와 같은 값."""

    category: str
    label_ko: str
    desc_ko: str
    badge_class: str
    # 포트를 서비스명에 인라인한 표시 문자열("nginx(80/443)·...") — 포트가 없으면 desc_ko.
    services_label: str = ""


@dataclass
class ServiceItem:
    unit: str
    sub: str
    category: str
    ports: list[MatchedPort]
    display_name: str = ""
    # 런타임 스택(container)만 호스트당 1 로 센다 — docker+containerd 가 2 로 부풀지 않게.
    category_count: int = 1


@dataclass
class ListenPortItem:
    proto: str
    addr: str
    port: int
    uid: int | None  # Windows agent null 호환 (POSIX uid 미존재)
    pid: int | None
    comm: str | None
    is_significant: bool = False  # port < 49152 — 동적 포트가 아니면 의도된 리스너로 본다


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
    known_services: list[ServiceItem] = field(default_factory=list[ServiceItem])
    show_unknown_badge: bool = False
    os_display: str = ""
    # "2코어 · 16.4GB · 30GB" 한 줄 — 값이 없는 항목은 "—".
    spec_display: str = ""
    # 카탈로그 매칭 시 eol date iso("2024-06-30", 경과·미래 무관), 미매칭이면 빈 문자열.
    os_eol: str = ""
    # "ended"(패치 없음) / "paid_only"(무상 종료·유상 연장만) / "security_only" / "full" / "unknown"(미매칭).
    # unknown 을 "지원 중"으로 접지 않는다 — 카탈로그 미매칭은 판정 불가일 뿐 지원 확인이 아니다.
    os_eol_status: str = ""
    # 표시 파생 — mappers.os_eol.os_eol_display 단일 진실.
    os_eol_label: str = ""
    os_eol_css: str = ""
    os_eol_title: str = ""
    os_eol_sort: int = 0
    # right_sizing.RECOMMENDATION_LABEL_KO 단일 진실. raws_period 부재 시 빈 문자열(분류 데이터 없음).
    recommendation_label: str = ""
    # 분류 raw enum — optimal / over_provisioned / under_provisioned / idle / insufficient_data.
    # 목록 필터 단일 진실. raws_period 부재 시 빈 문자열.
    provisioning_class: str = ""
    # 전체 기간 에러 발생 유무(OOM kill·MCE·메모리 손상·net/disk 에러 중 1+) — 환경 개요 운영 이벤트 카드와 같은 창.
    has_operational_event: bool = False
    # endoflife 카탈로그 product slug — os_id_to_distro 정규화(rocky -> rocky-linux). OS 필터 단일 진실.
    os_distro: str = ""
    last_task: TaskSummaryItem | None = None  # None = 발행 이력 없음


@dataclass
class ServerDetailResponse:
    id: int
    public_id: str
    agent_id: str  # 식별 단일 키(UUID) — 매칭·라우팅·upsert
    composite_id: str | None  # 감사·표시용 (식별은 agent_id, URL 은 public_id)
    machine_id: str | None  # raw machine-id 표시 전용
    hostname: str
    agent_version: str | None
    os_family: str | None  # "linux" | "windows" — Windows 미측정 메트릭 N/A 분기
    os_id: str | None
    os_version: str | None
    os_codename: str | None
    kernel_version: str | None
    cpu_cores: int | None
    cpu_model: str | None
    cpu_arch: str | None  # x86_64|aarch64 등 (server_inventory.arch pass-through)
    cpu_bits: int | None  # 32|64 (server_inventory.bits pass-through)
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
    # 이하 mapper(enrich_server_detail) 파생 필드 — default 필수 (dataclass 순서 제약)
    # Windows only pass-through — product_name 은 os_display 짧은 라벨 파싱 소스, edition 은 SKU 조합 표시.
    product_name: str | None = None
    edition: str | None = None
    sorted_services: list[ServiceItem] = field(default_factory=list[ServiceItem])  # unit ASC
    sorted_listen_ports: list[ListenPortItem] = field(default_factory=list[ListenPortItem])  # port ASC
    known_services: list[ServiceItem] = field(default_factory=list[ServiceItem])
    show_unknown_badge: bool = False
    key_listen_ports: list[ListenPortItem] = field(default_factory=list[ListenPortItem])
    os_display: str = ""
    cpu_display: str = ""
    disk_total_gb: float | None = None  # 배정 블록 — disk_total_bytes 단일 산식
    disk_unallocated_gb: float | None = None  # 미할당 = 배정 - 파일시스템 (확장 여력 추론)
    # 템플릿이 `| length` 를 쓰지 않도록 mapper 가 미리 센다.
    services_count: int = 0
    listen_ports_count: int = 0
    disks_count: int = 0
    volumes: list[VolumeItem] = field(default_factory=list[VolumeItem])
    volume_total_gb: float | None = None
    volumes_count: int = 0


@dataclass
class ServerStabilitySignals:
    """서버 세부 운영 신호 — 전체 수집 기간 기준 재부팅·에이전트 재시작 카운트 + OS 지원종료 라벨.

    엔지니어 보고서의 anchor+7일 창과 다른 창이다. 기간 집계라 ServerDetailResponse 캐시에 넣지 않고
    라우터가 매 요청 조회한다.
    """

    reboot_count: int
    agent_restart_count: int
    os_eol_label: str | None  # EOL 경과 시 "{제품} · EOL {date}", 아니면 None(지원 중)


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
class StorageNode:
    """스토리지 레이아웃 트리 노드 — block_device 또는 파생(미할당 갭·VG 여유). 계층별 자기 속성만 노출.

    계층별 귀속: 디스크=특성(SSD/HDD·partition_table)·배정 용량 / 파티션·LV=fstype·mount·소속 VG·segtype /
    fs(마운트 노드)=사용량 2축(bytes+inode) / VG=free_bytes(확장 여력) / RAID·crypt=표식.
    kind = block_device type 또는 파생("unallocated" 미파티션 갭 · "vg_free" VG 미할당).
    다중 부모(RAID span·striped VG)는 순수 트리가 안 돼(DAG) 디스크별 그룹으로 반복 노출한다.
    """

    name: str
    kind: str
    kind_label: str
    size_gb: float | None
    meta: str = ""  # 계층 속성 한 줄 ("SSD · GPT" / "ext4 · /boot" / "linear · VG rhel")
    badges: list[str] = field(default_factory=list[str])  # ["LUKS"], ["RAID5"] 표식
    # 사용량 2축은 마운트된 데이터 볼륨 노드만 채운다(아니면 usage_pct None). inode 는 Windows·미측정 시 None.
    mount: str = ""
    usage_pct: float | None = None
    usage_label: str = ""  # "6.2 / 28.8 GB"
    usage_class: str = ""  # _usage_badge_class severity (ok/warn/danger)
    inode_pct: float | None = None
    inode_label: str = ""  # "1%"
    inode_class: str = ""
    children: list[StorageNode] = field(default_factory=list["StorageNode"])
    # 트리 depth 들여쓰기를 상쇄해 모든 게이지 시작 x 를 맞추는 .stree-info 폭(px).
    # None = 게이지 없는 행(폭 고정 불요).
    gauge_info_width_px: int | None = None


@dataclass
class StorageDetailResponse:
    server_id: int
    public_id: str
    hostname: str
    disks: list[DiskItem]
    mounts: list[MountUsageItem]
    fs_total_gb: float | None  # 마운트 total_gb 합 — 스토리지 3계층 중 파일시스템 층
    snapshot_at: datetime | None
    inventory_at: datetime | None
    # 나머지 두 계층 — 배정 블록 / 미할당(확장 여력). storage_layers_gb 단일 산식.
    disk_total_gb: float | None = None
    disk_unallocated_gb: float | None = None
    # 물리 디스크 루트 + 디스크에 닿지 않는 논리 볼륨 그룹.
    tree: list[StorageNode] = field(default_factory=list[StorageNode])
    os_family: str | None = None  # OS 분기 표시(Windows I/O PSI N/A 등) — 템플릿 data-os-family


@dataclass
class NetIfaceAddress:
    value: str  # "10.50.1.42/24"
    is_ipv4: bool
    origin: str = ""  # "dhcp" / "static" / "" (미상)


@dataclass
class NetworkInterfaceInfo:
    """네트워크 인터페이스 정적 속성 — 구성 정보만, 실 활동(RX/TX·pps)은 NetIoSnapshot 별개 축.

    물리(kind=physical/bond_master)만 담는다 — 가상 제외 판정은 device_filters.is_virtual_interface 단일 진실.
    """

    name: str
    mac: str = ""
    mtu: int | None = None
    speed_mbps: int | None = None
    gateway: str = ""
    dns: list[str] = field(default_factory=list[str])
    addresses: list[NetIfaceAddress] = field(default_factory=list[NetIfaceAddress])


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
    interfaces_info: list[NetworkInterfaceInfo] = field(default_factory=list[NetworkInterfaceInfo])
    os_family: str | None = None  # OS 분기 표시(conntrack N/A 등) — 템플릿 data-os-family
