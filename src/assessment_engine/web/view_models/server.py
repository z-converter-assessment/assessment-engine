"""서버 표시 ViewModel — list / detail / storage / network 페이지 + 인벤토리 단위 dataclass."""

from dataclasses import dataclass, field
from datetime import datetime

# MatchedPort 는 분류 도메인 개념 — service_classifier(domain)에 정의, 본 모듈은 ServiceItem.ports 로 소비.
from assessment_engine.service_classifier import MatchedPort

# NetIoSnapshot 은 NetworkDetailResponse.interfaces 필드 타입으로 재사용 (metric sub-module 정의).
from assessment_engine.web.view_models.metric import NetIoSnapshot
from assessment_engine.web.view_models.task import TaskSummaryItem


@dataclass
class DiskItem:
    name: str
    size_gb: float | None


@dataclass
class VolumeItem:
    """파일시스템(논리 볼륨) — block_devices 중 마운트된 데이터 볼륨 노드 기준. 물리 디스크(DiskItem)와 별개 축.

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
class ServiceBadgeRef:
    """참고자료 — 서비스 뱃지 카탈로그 1행. SERVICE_CATALOG(E7) 파생, 표시 전용.

    category 는 UI 뱃지에 노출되는 키 텍스트(web·db 등)와 동일 — 사용자가 화면 뱃지를 본 페이지에서 조회.
    """

    category: str
    label_ko: str
    desc_ko: str
    badge_class: str
    # 대상 서비스 표시 — 포트를 서비스명에 인라인 ("nginx(80/443)·..."). 포트 없으면 desc_ko. mapper precompute.
    services_label: str = ""


@dataclass
class ServiceItem:
    unit: str
    sub: str
    category: str
    ports: list[MatchedPort]
    display_name: str = ""
    # 같은 카테고리 서비스 개수 (서버목록 뱃지 "db 2" — 환경요약 role 인스턴스 수와 일관).
    # 런타임 스택(container)은 호스트당 1 (docker+containerd 부풀림 방지).
    category_count: int = 1


@dataclass
class ListenPortItem:
    proto: str
    addr: str
    port: int
    uid: int | None  # Windows agent null 호환 (POSIX uid 미존재)
    pid: int | None
    comm: str | None
    is_significant: bool = False  # port < 49152 (비동적 = 의도된 서비스 리스너). mapper 계산 (P2)


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
    # 정적 사양 한 줄 — "2코어 · 16.4GB · 30GB" (CPU 코어·메모리·디스크). mapper precompute(P2), 값 부재는 "—".
    spec_display: str = ""
    # OS 지원 종료(EOL) — 카탈로그 매칭 시 eol date iso("2024-06-30", 경과·미래 무관), 미매칭 시 빈 문자열.
    os_eol: str = ""
    # 지원 단계 — "ended"(패치 없음) / "paid_only"(무상 종료·유상 연장만) / "security_only"(보안 패치만) /
    # "full"(기능+보안) / "unknown"(카탈로그 미수록·미매칭 = 판정 불가). 미매칭을 "지원 중"으로
    # 단정하지 않기 위한 분리 (lookup_os_eol 매칭 여부 + status 기반).
    os_eol_status: str = ""
    # 표시 파생 — mappers.shared.os_eol_display 단일 진실 (P2). 템플릿은 분기 없이 꺼내 쓴다.
    os_eol_label: str = ""
    os_eol_css: str = ""
    os_eol_title: str = ""
    os_eol_sort: int = 0
    # 권장 조치 — USE Method 분류 한국어 라벨(recommendation.LABEL_KO 단일 진실). mapper 단일 결정 (P2).
    # 목록 색은 provisioning_class 기반 under-only 강조(#E, _server_rows.html) — 분류 다색은 상세/보고서 전용.
    # raws_period 부재 시 빈 문자열 (도넛/분류 데이터 없음 — 페이지 2+ 또는 신규 등록 직후).
    recommendation_label: str = ""
    # 분류 raw enum — list 필터링 단일 진실 (optimal / over_provisioned / under_provisioned /
    # idle / insufficient_data). raws_period 부재 시 빈 문자열.
    provisioning_class: str = ""
    # 운영 이벤트 — 전체 기간 에러 발생 유무(OOM kill·MCE·메모리 손상·net/disk 에러 5축 중 1+). 서비스가
    # fleet_error_hosts(전기간) 집합으로 세팅 (환경 개요 운영 이벤트 카드와 동일 창 — 목록에서 그 호스트 찾기).
    has_operational_event: bool = False
    # OS distro(endoflife 카탈로그 product slug) — OS 필터 단일 진실.
    # os_id_to_distro(os_id) 정규화 (rocky->rocky-linux).
    os_distro: str = ""
    # 행별 마지막 task 요약. None 이면 발행 이력 없음 — 템플릿이 "—" 로 표시.
    last_task: TaskSummaryItem | None = None


@dataclass
class ServerDetailResponse:
    id: int
    public_id: str
    agent_id: str  # 식별 단일 키(UUID) — 매칭·라우팅·upsert. 표시용 실질 식별자
    composite_id: str | None  # 감사·표시용 (식별은 agent_id, URL 은 public_id)
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
    cpu_arch: str | None  # ISA — x86_64|aarch64 등 (server_inventory.arch pass-through)
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
    # Windows only pass-through — product_name 은 os_display 짧은 라벨 파싱 소스, edition 은 상세 조합 표시(SKU).
    product_name: str | None = None
    edition: str | None = None
    sorted_services: list[ServiceItem] = field(default_factory=list[ServiceItem])  # P3: unit ASC 정렬
    sorted_listen_ports: list[ListenPortItem] = field(default_factory=list[ListenPortItem])  # P3: port ASC 정렬
    known_services: list[ServiceItem] = field(default_factory=list[ServiceItem])
    show_unknown_badge: bool = False
    key_listen_ports: list[ListenPortItem] = field(default_factory=list[ListenPortItem])
    os_display: str = ""
    cpu_display: str = ""
    disk_total_gb: float | None = None  # 배정 블록 — disk_total_bytes 단일 산식(물리 우선·fs fallback, #C)
    disk_unallocated_gb: float | None = None  # 미할당 = 배정 - 파일시스템 (확장 여력 추론)
    # P3: 템플릿이 `| length` 못 쓰도록 count를 mapper에서 미리 계산
    services_count: int = 0
    listen_ports_count: int = 0
    disks_count: int = 0
    # 파일시스템(논리 볼륨) — block_devices 중 마운트된 데이터 볼륨 노드. 물리 디스크와 별개 축, fstype 명시.
    volumes: list[VolumeItem] = field(default_factory=list[VolumeItem])
    volume_total_gb: float | None = None
    volumes_count: int = 0


@dataclass
class ServerStabilitySignals:
    """서버 세부 운영 신호 — 전구간(전체 수집 기간) 재부팅·에이전트 재시작 카운트 + OS 지원종료 라벨.

    selection 엔지니어 보고서는 anchor+7일 window 카운트지만, 서버 세부는 전체 수집 기간(전구간) 기준.
    window 집계라 ServerDetailResponse 캐시에 넣지 않고 라우터가 매 요청 query_service 로 조회해 context 전달.
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

    device_filters 측정 원칙 계층 귀속: 디스크=특성(SSD/HDD·partition_table)·배정 용량 / 파티션·LV=fstype·mount·
    소속 VG·segtype / fs(마운트 노드)=사용량 2축(bytes+inode) / VG=free_bytes(확장 여력) / RAID·crypt=표식.
    kind = block_device type("disk"/"part"/"lvm"/"raid"/"crypt"/"swap"/"volume"/"mpath"/"dynamic") 또는
    파생("unallocated" 미파티션 갭 · "vg_free" VG 미할당). 라벨·메타·배지·사용량 전부 mapper precompute (P2).
    다중 부모(RAID span·striped VG)는 순수 트리 불가(DAG) — 디스크별 그룹으로 노출(같은 노드가 여러 디스크에 반복).
    """

    name: str
    kind: str
    kind_label: str  # "디스크"/"파티션"/"LV"/"RAID"/"암호화"/"스왑"/"볼륨"/"미할당"/"VG 여유"
    size_gb: float | None
    meta: str = ""  # 계층 속성 한 줄 ("SSD · GPT" / "ext4 · /boot" / "linear · VG rhel")
    badges: list[str] = field(default_factory=list[str])  # ["LUKS"], ["RAID5"] 표식
    # 파일시스템 사용량 2축 — 마운트된 데이터 볼륨 노드만 (아니면 usage_pct None). inode 는 Windows/미측정 시 None.
    mount: str = ""
    usage_pct: float | None = None
    usage_label: str = ""  # "6.2 / 28.8 GB"
    usage_class: str = ""  # _usage_badge_class severity (ok/warn/danger)
    inode_pct: float | None = None
    inode_label: str = ""  # "1%"
    inode_class: str = ""
    children: list[StorageNode] = field(default_factory=list["StorageNode"])
    # 게이지(usage_pct) 있는 행에만 설정 — 트리 depth 들여쓰기를 상쇄해 모든 게이지 시작 x 를 통일하는
    # .stree-info 폭(px). None = 게이지 없음(폭 고정 불요, 자연 크기). mapper precompute(P3 계산 회피).
    gauge_info_width_px: int | None = None


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
    # 스토리지 3계층(storage_layers_gb 단일 산식) — 배정 블록 / 미할당(확장 여력). fs_total_gb 가 파일시스템 층.
    disk_total_gb: float | None = None
    disk_unallocated_gb: float | None = None
    # 레이아웃 트리 — 물리 디스크 루트(+ 디스크 미도달 논리 볼륨 그룹). 계층 조립·속성 precompute(P2).
    tree: list[StorageNode] = field(default_factory=list[StorageNode])
    os_family: str | None = None  # OS 분기 표시(Windows I/O PSI N/A 등, #E6 data-os-family)


@dataclass
class NetIfaceAddress:
    """인터페이스 주소 1개 — CIDR 표시값 + IPv4 여부 + 할당 방식(dhcp/static, 미상 시 빈 문자열)."""

    value: str  # "10.50.1.42/24"
    is_ipv4: bool
    origin: str = ""  # "dhcp" / "static" / "" (미상)


@dataclass
class NetworkInterfaceInfo:
    """네트워크 인터페이스 정적 속성 — net_interfaces(agent) 원본 노드 1개당 표시 단위(P2 precompute).

    실 활동(RX/TX 처리량·pps)은 NetIoSnapshot 별개 축(실시간 카드) — 본 dataclass 는 구성 정보만
    (MAC·MTU·속도·게이트웨이·DNS·주소). 물리(kind=physical/bond_master)만 노출 — 가상은 제외
    (device_filters.is_virtual_interface 단일 진실, loopback·bridge·veth·vlan 등).
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
    # 인터페이스 정적 정보(MAC·MTU·속도·게이트웨이·DNS·주소) — 레이아웃 카드 "네트워크 정보"에서 소비.
    interfaces_info: list[NetworkInterfaceInfo] = field(default_factory=list[NetworkInterfaceInfo])
    os_family: str | None = None  # OS 분기 표시(conntrack N/A 등, #E6 data-os-family)
