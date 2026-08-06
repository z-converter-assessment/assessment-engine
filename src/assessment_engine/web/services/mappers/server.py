"""서버 표시 mapper — ServerSummary/ServerDetail/StorageWithUsage/NetworkWithIo → ViewModel (P2).

본 sub-module 책임: 인벤토리·서비스·디스크·마운트·네트워크 표시 변환 + role 추론.
다른 sub-module 이 import 하는 항목: `infer_role`, `DYNAMIC_PORT_MIN`, `enrich_server_detail`.
"""

from collections import Counter
from typing import TYPE_CHECKING, Literal

from assessment_engine import recommendation
from assessment_engine.json_types import JsonObject, json_list, json_str_list
from assessment_engine.service_classifier import (
    SIGNATURE_CATEGORIES,
    SINGLE_INSTANCE_CATEGORIES,
    MatchedPort,
    classify,
    detect_listen_categories,
    is_baseline_service,
    is_baseline_socket,
    matched_ports,
)
from assessment_engine.web.services.device_filters import (
    disk_total_bytes,
    is_data_volume,
    is_physical_disk,
    is_swap,
    is_virtual_interface,
    swap_total_bytes,
)
from assessment_engine.web.services.mappers.metrics_calculator import compute_net_io
from assessment_engine.web.services.mappers.os_eol import (
    lookup_os_eol,
    os_eol_display,
    os_id_to_distro,
    windows_legacy_version_from_build,
    windows_short_label_from_product_name,
)
from assessment_engine.web.services.mappers.resource_stats import build_resource_stats
from assessment_engine.web.services.mappers.shared import (
    _USAGE_DANGER_PCT,
    _USAGE_WARN_PCT,
    spec_display_line,
)
from assessment_engine.web.services.unit_converter import bytes_to_gb, bytes_to_gib, usage_pct
from assessment_engine.web.templating.filters import storagesize  # 사이즈 라벨 단일 규약 (트리·페이지 통일)
from assessment_engine.web.view_models.environment_report import (
    CpuBreakdown,
    MemoryBreakdown,
    ServerInventorySnapshot,
)
from assessment_engine.web.view_models.server import (
    DiskItem,
    IpAddr,
    ListenPortItem,
    MountUsageItem,
    NetIfaceAddress,
    NetworkDetailResponse,
    NetworkInterfaceInfo,
    ServerDetailResponse,
    ServerListItem,
    ServiceItem,
    StorageDetailResponse,
    StorageNode,
    VolumeItem,
)

if TYPE_CHECKING:
    from datetime import date

    from assessment_engine.db.dtos.outbound import (
        CpuBreakdownRaw,
        MemoryBreakdownRaw,
        MountUsageRaw,
        NetworkWithIo,
        ReportRowRaw,
        ServerDetail,
        ServerSummary,
        StorageWithUsage,
    )

# IANA 동적/private 포트 하한 (49152~65535). 이 이상은 RPC 동적 할당·ephemeral 이라 "주요 포트" 표시에서 제외.
# 그 미만(0~49151)은 system·registered·user 포트 = 의도된 서비스 리스너로 간주해 표시 (RDP 3389·Redis 6379 등 고포트 포함).
# cache_serializer 가 본 상수를 import 해 역직렬화 후 enrich 재호출 시 동일 분기 적용.
DYNAMIC_PORT_MIN = 49152

type _Severity = Literal["ok", "warn", "danger"]

_BADGE_CLASS_BY_SEVERITY: dict[_Severity, str] = {
    "ok": "badge-ok",
    "warn": "badge-warn",
    "danger": "badge-danger",
}
# 파일시스템 사용량 게이지 막대 = 테마 주색 단색 (사용자 결정 — 임계별 색 분기 없이 통일).
# 사용률 위험/주의 신호는 badge_class(_usage_badge_class)가 담당. 게이지 막대는 단색.
_MOUNT_BAR_COLOR = "var(--color-title)"  # 테마색1 (base.html :root) — 마운트 usage 막대 CSS background


def _usage_severity(pct: float | None) -> _Severity:
    if pct is None or pct < _USAGE_WARN_PCT:
        return "ok"
    if pct < _USAGE_DANGER_PCT:
        return "warn"
    return "danger"


def _usage_badge_class(pct: float | None) -> str:
    return _BADGE_CLASS_BY_SEVERITY[_usage_severity(pct)] if pct is not None else ""


# --- raw dict → typed ViewModel 단일 변환 진입점 --------------------------


def _to_ip_addrs(net_interfaces: list[JsonObject]) -> list[IpAddr]:
    """net_interface 노드 목록 → IpAddr(value=CIDR, is_ipv4). IPv4 우선 정렬(안정), loopback 제외.

    노드의 kind 는 인터페이스 레벨, 주소는 nested addresses[]({address,prefix,family}) — 다중 IP 호스트는
    전 주소 표출. IPv4 는 실제 접속·식별 주력이라 상단·진하게 표시, IPv6(ULA/link-local)는 보조(연하게).
    """
    items: list[IpAddr] = []
    for iface in net_interfaces or []:
        if iface.get("kind") == "loopback":
            continue  # 표시 무의미
        for a in json_list(iface, "addresses"):
            addr = a.get("address", "")
            prefix = a.get("prefix")
            value = f"{addr}/{prefix}" if prefix is not None else addr
            items.append(IpAddr(value=value, is_ipv4=a.get("family") == "ipv4"))
    return sorted(items, key=lambda x: not x.is_ipv4)


def _ext_ip_addrs(ips: list[str] | None) -> list[IpAddr]:
    """외부 IP(list[str] 평문) → IpAddr. prefix/family 정보 없음 — is_ipv4 는 ':' 유무로 추정, CIDR 없이 표시."""
    items = [IpAddr(value=s, is_ipv4=":" not in s) for s in ips or []]
    return sorted(items, key=lambda x: not x.is_ipv4)


def build_network_interfaces(
    net_interfaces: list[JsonObject], link_speed_by_iface: dict[str, int] | None = None
) -> list[NetworkInterfaceInfo]:
    """네트워크 인터페이스 정적 정보(MAC·MTU·속도·게이트웨이·DNS·주소) — "네트워크 정보" 카드 소비(P2).

    물리(kind=physical/bond_master)만 — 가상(loopback·bridge·veth·vlan 등)은 device_filters.
    is_virtual_interface 단일 술어로 제외(다른 소비처와 동일 필터, ad-hoc 재필터 금지).
    speed_mbps 는 인벤토리 원본 우선, null(virtio·Windows NT5.2 흔함)이면 link_speed_by_iface(metrics
    network.link.speed 최신 gauge, bit/s) 로 폴백 — environment.py 의 동일 목적 폴백과 단일 산식.
    """
    link_speed_by_iface = link_speed_by_iface or {}
    items: list[NetworkInterfaceInfo] = []
    for iface in net_interfaces or []:
        kind = iface.get("kind")
        if is_virtual_interface(kind):
            continue
        speed_mbps = iface.get("speed_mbps")
        if speed_mbps is None:
            iface_id = iface.get("id")
            bps = link_speed_by_iface.get(iface_id) if iface_id else None
            if bps is not None:
                speed_mbps = round(bps / 1_000_000)
        addresses = [
            NetIfaceAddress(
                value=(
                    f"{a.get('address', '')}/{a['prefix']}" if a.get("prefix") is not None else a.get("address", "")
                ),
                is_ipv4=a.get("family") == "ipv4",
                origin=a.get("origin") or "",
            )
            for a in json_list(iface, "addresses")
        ]
        items.append(
            NetworkInterfaceInfo(
                name=iface.get("name", ""),
                # .get(key, "") 는 키가 있고 값이 None 이면 None 을 돌려준다 — id 는 nullable 이라 or 로 받는다.
                mac=(iface.get("id") or "") if iface.get("id_type") == "mac" else "",
                mtu=iface.get("mtu"),
                speed_mbps=speed_mbps,
                gateway=iface.get("gateway") or "",
                dns=json_str_list(iface, "dns"),
                addresses=sorted(addresses, key=lambda a: not a.is_ipv4),
            )
        )
    return items


def _to_volumes(block_devices: list[JsonObject]) -> list[VolumeItem]:
    """block_devices(lsblk) 중 마운트된 데이터 볼륨 노드 → VolumeItem(파일시스템) 목록 (가상·부트 제외, mount ASC).

    물리 디스크(type=disk)와 별개 축 — 양 OS 일관 표시 (fstype 명시). 마운트된 노드(mountpoint 있음)만.
    """
    volumes: list[VolumeItem] = []
    for d in block_devices or []:
        path = d.get("mountpoint")
        fstype = d.get("fstype")
        if not path or is_swap(d.get("type")) or not is_data_volume(fstype, path):
            continue  # swap 노드(mountpoint="[SWAP]"/pagefile)는 데이터 볼륨 아님 — 별도 인벤토리 총량으로만 표시
        volumes.append(VolumeItem(mount=path, fstype=fstype, total_gb=bytes_to_gb(d.get("size_bytes"))))
    return sorted(volumes, key=lambda v: v.mount)


def _to_disk_item(d: JsonObject) -> DiskItem | None:
    """물리 디스크(block_device type=disk) 아니면 None."""
    name = d.get("name", "")
    if not is_physical_disk(d.get("type")):
        return None
    return DiskItem(
        name=name,
        size_gb=bytes_to_gb(d.get("size_bytes")),
    )


def _to_listen_port_item(p: JsonObject) -> ListenPortItem:
    port = p.get("port", 0)
    return ListenPortItem(
        proto=p.get("proto", ""),
        addr=p.get("addr", ""),
        port=port,
        uid=p.get("uid"),
        pid=p.get("pid"),
        comm=p.get("comm"),
        is_significant=port < DYNAMIC_PORT_MIN,
    )


def _to_service_item(s: JsonObject, listen_ports: list[JsonObject] | None = None) -> ServiceItem:
    """listen_ports가 주어지면 매핑된 포트를 채움 (detail). 없으면 빈 리스트 (list 화면)."""
    unit = s.get("unit", "")
    return ServiceItem(
        unit=unit,
        sub=s.get("sub", ""),
        category=classify(unit, listen_ports, s.get("pid")),
        ports=matched_ports(unit, listen_ports, s.get("pid")) if listen_ports else [],
        display_name=unit.removesuffix(".service"),
    )


def _services_or_none(
    raw: list[JsonObject] | None,
    listen_ports: list[JsonObject] | None = None,
) -> list[ServiceItem] | None:
    """services는 None을 보존 (non-systemd 호스트 = unknown 표시 대상 아님)."""
    if raw is None:
        return None
    return [_to_service_item(s, listen_ports) for s in raw]


def _os_display(
    os_id: str | None,
    os_version: str | None,
    kernel_version: str | None = None,
    product_name: str | None = None,
) -> str:
    ver = os_version
    if os_id == "windows":
        # 티어 1: ProductName 연도/세대 라벨("2019"·"2012 R2") — os_version(DisplayVersion)이 LTSC/SAC 를
        # 구분 못 하고 "1809" 를 공유하는 한계 보강. SAC(연도 없음)·미매칭·product_name 부재는 None -> 폴백.
        short = windows_short_label_from_product_name(product_name)
        if short:
            ver = short
        elif not ver:
            # 티어 3: os_version 빈값(레거시 Server 2012 R2 이하) -> kernel build 로 보강 ("windows 2012").
            ver = windows_legacy_version_from_build(kernel_version)
        # 티어 2(else): os_version 그대로 — SAC 는 여기서 "1809" 로 정확히 표시.
    parts = [p for p in [os_id, ver] if p]
    return " ".join(parts) or "-"


def build_server_inventory(
    detail: ServerDetail, is_online: bool, raw: ReportRowRaw | None = None
) -> ServerInventorySnapshot:
    """ServerDetail(+선택적 ReportRowRaw) -> 개별 보고서 인벤토리 (충실 표시 — 전체 IP(IPv4/IPv6)·하드웨어·

    식별자, 생략·왜곡 0). raw 는 ReportRowRaw — server_inventory 재현 필드(arch/bits/boot_firmware/
    secure_boot/edition/timezone) 보강용, 없으면 그 필드들만 None(환경/N대 등 raw 미보유 스코프 대비).
    """
    # 디스크 총량 — block_devices type=disk size_bytes 합 (양 OS 단일 산식, device_filters).
    disk_bytes = disk_total_bytes(detail.block_devices)
    return ServerInventorySnapshot(
        hostname=detail.hostname,
        os_display=_os_display(detail.os_id, detail.os_version, detail.kernel_version, detail.product_name),
        os_codename=detail.os_codename,
        kernel_version=detail.kernel_version,
        cpu_model=detail.cpu_model,
        cpu_cores=detail.cpu_cores,
        mem_total_gb=bytes_to_gib(detail.mem_total_bytes),
        swap_total_gb=bytes_to_gib(swap_total_bytes(detail.block_devices)),
        disk_total_gb=int(bytes_to_gb(disk_bytes) or 0),
        ip_internal=_to_ip_addrs(detail.net_interfaces),
        ip_external=_ext_ip_addrs(detail.ip_external),
        boot_time=detail.boot_time,
        agent_started_at=detail.agent_started_at,
        last_seen_at=detail.last_seen_at,
        agent_version=detail.agent_version,
        composite_id=detail.composite_id,
        machine_id=detail.machine_id,
        is_online=is_online,
        public_id=detail.public_id,
        agent_id=detail.agent_id,
        cpu_arch=detail.cpu_arch,
        cpu_bits=detail.cpu_bits,
        boot_firmware=raw.boot_firmware if raw is not None else None,
        secure_boot=raw.secure_boot if raw is not None else None,
        os_edition=raw.edition if raw is not None else None,
        timezone=raw.timezone if raw is not None else None,
    )


def build_memory_breakdown(raw: MemoryBreakdownRaw) -> MemoryBreakdown:
    return MemoryBreakdown(
        used_pct=raw.used_pct,
        available_pct=raw.available_pct,
        cached_pct=raw.cached_pct,
        buffers_pct=raw.buffers_pct,
    )


def build_cpu_breakdown(raw: CpuBreakdownRaw) -> CpuBreakdown:
    return CpuBreakdown(user_pct=raw.user_pct, system_pct=raw.system_pct, iowait_pct=raw.iowait_pct)


# --- 1차 매핑 (Outbound → ViewModel) --------------------------------------


def to_server_list_item(
    dto: ServerSummary,
    raw_period: ReportRowRaw | None = None,
    today: date | None = None,
    error_hosts: set[int] | None = None,
) -> ServerListItem:
    """ServerSummary -> ServerListItem. raw_period(ReportRowRaw)가 있으면 권장 조치 분류 채움.

    분류 라벨은 recommendation.LABEL_KO, provisioning_class 는 Recommendation enum 값 그대로 (P2 단일 진실).
    raw_period=None이면 미분류 — 빈 문자열 (페이지 2+ 등 raws_period 부재).
    today 주어지면 OS 지원 단계 판정(lookup_os_eol) — 카탈로그 매칭 여부로 판정 결과와 "미상(판정 불가)" 분리.
    카탈로그 미수록(oracle 외 tencent 등)·미매칭을 "지원 중"으로 단정하지 않는다.
    """
    # OsEolInfo 또는 미매칭 시 None. os_eol 은 매칭 iso(경과·미래 무관), status 는 판정 4단계 또는 unknown.
    info = lookup_os_eol(dto.os_id, dto.os_version, dto.kernel_version, today) if today else None
    if info is None:
        os_eol, os_eol_status = "", ("unknown" if today else "")
    else:
        os_eol, os_eol_status = info.eol_iso, info.status
    os_eol_disp = os_eol_display(os_eol_status, os_eol)
    # 물리 디스크 총량 — disk_total_bytes 단일 산식(type=disk 합, 목록·상세·보고서 일관).
    _disk_bytes = disk_total_bytes(dto.block_devices)
    _disk_gb = bytes_to_gb(_disk_bytes) if _disk_bytes else None
    storage_total_gb = round(_disk_gb, 1) if _disk_gb is not None else None

    _mem_gib = bytes_to_gib(dto.mem_total_bytes)  # 1자리 반올림 — ServerListItem.mem_total_gb 용
    # 정적 사양 한 줄 — spec_display_line 단일 진실(shared.py, 환경 자원 평가 compact 표와 공유).
    spec_display = spec_display_line(dto.cpu_cores, dto.mem_total_bytes, dto.block_devices)

    # 서비스 뱃지 — 시그니처 워크로드만(SIGNATURE_CATEGORIES, 환경 개요 도넛과 동일 기준). file·mail·infra·remote
    # 등 유틸/관리 서비스는 서버 성격 신호가 약해 목록에서 제외(노이즈 감소). 상세 페이지는 live classify 로 전부.
    known = [
        ServiceItem(unit="", sub="", category=cat, ports=[])
        for cat in dto.service_categories
        if cat in SIGNATURE_CATEGORIES
    ]
    services = known
    show_unknown = False

    rec_label, seg_key = "", ""
    if raw_period is not None:
        # build_resource_stats 단일 진실(net baseline 포함) — 보고서·대시보드와 동일 분류 입력 (#E3).
        # rollup_host 1회 산출 -> 분류 배지(classify_host 내부도 rollup_host 경유). 네트워크 혼잡(orthogonal,
        # host_status 미구동)은 목록 배지에서 뺀다 — 분류 칼럼에 붙어 분류의 일부처럼 보이나 실은 별개 트리거
        # (재전송·드롭 임계)라 화면만으로 근거를 확인할 수 없었다. 필요 시 서버 상세에서 확인.
        host = recommendation.rollup_host(build_resource_stats(raw_period, disk_baseline=None))
        rec = recommendation.host_status_to_recommendation(host.host_status)
        seg_key = rec
        # 라벨은 한국어 분류명(recommendation.LABEL_KO 단일 진실) — 서버목록 칼럼 한글 표시. 색은 목록이
        # provisioning_class 기반 under-only 강조(#E, _server_rows.html)라 분류색 미사용(다색 배지는 상세/보고서).
        rec_label = recommendation.LABEL_KO[seg_key]

    return ServerListItem(
        id=dto.id,
        public_id=dto.public_id,
        hostname=dto.hostname,
        os_id=dto.os_id,
        os_version=dto.os_version,
        cpu_cores=dto.cpu_cores,
        mem_total_gb=_mem_gib,
        storage_total_gb=storage_total_gb,
        is_online=False,
        ip_external=dto.ip_external,
        services=services,
        known_services=known,
        show_unknown_badge=show_unknown,
        os_display=_os_display(dto.os_id, dto.os_version, dto.kernel_version, dto.product_name),
        spec_display=spec_display,
        os_eol=os_eol,
        os_eol_status=os_eol_status,
        os_eol_label=os_eol_disp.label,
        os_eol_css=os_eol_disp.css,
        os_eol_title=os_eol_disp.title,
        os_eol_sort=os_eol_disp.sort,
        recommendation_label=rec_label,
        provisioning_class=seg_key,
        has_operational_event=error_hosts is not None and dto.id in error_hosts,
        os_distro=os_id_to_distro(dto.os_id),
    )


def storage_layers_gb(block_devices: list[JsonObject]) -> tuple[float | None, float | None, float | None]:
    """스토리지 블록 계층 3분 (GB) 단일 산식 — (배정 블록, 볼륨 배정, 미할당). block_devices(lsblk) 트리 기준.

    배정 = disk_total_bytes(type=disk size_bytes 합). 볼륨 배정 = 마운트된 데이터 볼륨 노드 size_bytes 합(블록크기).
    미할당 = max(0, 배정 - 볼륨 배정) — 확장 여력 추론(둘 다 있을 때만).
    본 함수는 lsblk 블록크기 계층 단일 소스(시스템 정보 카드 "배정 디스크/볼륨 배정 크기/기타"). df 기반 실용량은
    별개 축 — 스토리지 탭 "파일시스템 총 용량"은 to_storage_detail 이 MountUsageItem(df used+free)로 산출한다
    (fs 포맷 오버헤드만큼 볼륨 배정보다 작음, 그 탭 per-mount df 행과 정합). 두 축은 라벨로 구분(값 다름은 의도).
    """
    allocated = bytes_to_gb(disk_total_bytes(block_devices))
    fs_bytes = sum(
        (d.get("size_bytes") or 0)
        for d in block_devices or []
        if d.get("mountpoint")
        and not is_swap(d.get("type"))  # swap 노드는 파일시스템 층 아님 (별도 swap_total_bytes)
        and is_data_volume(d.get("fstype"), d.get("mountpoint"))
    )
    filesystem = bytes_to_gb(fs_bytes) if fs_bytes else None
    unallocated = None
    if allocated is not None and filesystem is not None:
        unallocated = round(max(0.0, allocated - filesystem), 2)
    return (
        round(allocated, 2) if allocated is not None else None,
        round(filesystem, 2) if filesystem is not None else None,
        unallocated,
    )


# --- 스토리지 레이아웃 트리 (block_devices 그래프 + lvm_vgs + filesystems 조립) --------------

_STORAGE_KIND_LABEL = {
    "disk": "디스크",
    "part": "파티션",
    "lvm": "LV",
    "raid": "RAID",
    "crypt": "암호화",
    "swap": "스왑",
    "volume": "볼륨",
    "mpath": "멀티패스",
    "dynamic": "동적 디스크",
    "unallocated": "미할당",
    "vg_free": "VG 여유",
}

# 미할당 갭 표시 하한 — 파티션 정렬 잔여(수 MB)를 노이즈로 숨김 (bytes).
_UNALLOC_MIN_BYTES = 64 * 1024 * 1024  # 64 MiB

# 게이지 정렬 — 트리 depth(중첩 ul) 만큼 밀리는 걸 .stree-info 폭 축소로 상쇄해 모든 게이지 시작 x 통일.
# _STREE_INDENT_PX 는 storage.html CSS ".stree ul"(margin-left 16 + border-left 1 + padding-left 16) 합과
# 동기화 필수 — CSS 값 변경 시 함께 갱신.
_STREE_INDENT_PX = 33
_STREE_INFO_TARGET_PX = 340  # depth 0 기준 게이지 시작 목표 x(=.stree-info 폭)
_STREE_INFO_MIN_PX = 160  # 깊은 중첩에서도 정보 컬럼 완전붕괴 방지 — 그 이상 깊이는 정렬 근사치로 degrade


def _rotational_label(rotational: object) -> str:
    """rotational -> HDD/SSD (미상 None 은 빈 문자열). Windows·Linux 통일 디바이스 특성."""
    if rotational is True:
        return "HDD"
    if rotational is False:
        return "SSD"
    return ""


def _ptable_label(pt: object) -> str:
    if not pt:
        return ""
    p = str(pt).lower()
    if p == "gpt":
        return "GPT"
    if p in ("dos", "mbr", "msdos"):
        return "MBR"
    return str(pt)


def _raid_level_num(raid_level: object) -> str:
    """raid_level -> 숫자 문자열 ("raid5"/"5"/5 -> "5"). 판독 불가 시 빈 문자열."""
    if raid_level is None:
        return ""
    s = str(raid_level).lower().removeprefix("raid")
    return s or ""


def _storage_node_meta(d: JsonObject, kind: str) -> tuple[str, list[str]]:
    """계층별 자기 속성만 -> (meta 한 줄, badges). 없는 축은 생략 (device_filters 계층 귀속)."""
    parts: list[str] = []
    badges: list[str] = []
    if kind == "disk":
        if rl := _rotational_label(d.get("rotational")):
            parts.append(rl)
        if pt := _ptable_label(d.get("partition_table")):
            parts.append(pt)
    elif kind == "lvm":
        if seg := d.get("lvm_segtype"):
            parts.append(str(seg))
        if vg := d.get("lvm_vg"):
            parts.append(f"VG {vg}")
        if (fst := d.get("fstype")) and not d.get("mountpoint"):
            parts.append(str(fst))  # 마운트 안 된 LV 는 fstype 만
    elif kind == "raid":
        if lvl := _raid_level_num(d.get("raid_level")):
            badges.append(f"RAID{lvl}")
    elif kind == "crypt":
        ct = d.get("crypt_type")
        badges.append(str(ct).upper() if ct and "luks" in str(ct).lower() else "암호화")
    elif kind == "swap":
        parts.append("[SWAP]")
    # 파티션·볼륨·마운트 LV 공통 — fstype + mountpoint
    if kind in ("part", "volume") or (kind == "lvm" and d.get("mountpoint")):
        if fst := d.get("fstype"):
            parts.append(str(fst))
        if mp := d.get("mountpoint"):
            parts.append(str(mp))
    return " · ".join(parts), badges


def _attach_fs_usage(node: StorageNode, fs: MountUsageRaw) -> None:
    """마운트 노드에 파일시스템 사용량 2축(bytes + inode) precompute. inode 미측정(Windows)이면 inode 축 None."""
    total = (fs.used_bytes + fs.free_bytes) if (fs.used_bytes is not None and fs.free_bytes is not None) else None
    node.usage_pct = usage_pct(fs.used_bytes, total)
    node.usage_class = _usage_badge_class(node.usage_pct)
    used_gb, total_gb = bytes_to_gb(fs.used_bytes), bytes_to_gb(total)
    node.usage_label = f"{storagesize(used_gb)} / {storagesize(total_gb)}"
    if fs.inodes_used is not None and fs.inodes_free is not None:
        inode_total = fs.inodes_used + fs.inodes_free
        node.inode_pct = usage_pct(fs.inodes_used, inode_total)
        node.inode_class = _usage_badge_class(node.inode_pct)
        node.inode_label = f"{node.inode_pct}%" if node.inode_pct is not None else ""


# 디스크 span 가능(다중 부모) 집계 device — 최초 도달 디스크에 배열 상세, 나머지 구성원 디스크엔 참조 스텁.
_SPANNING_KINDS = ("raid", "mpath", "dynamic")


def _member_ref_node(c: JsonObject, array_home: dict[str | None, str]) -> StorageNode:
    """이미 다른 디스크 아래 배치된 배열(RAID/멀티패스) 참조 스텁 — 빈 디스크 오인 방지.

    `kind_label` 에 배열 종류(RAID1 등)를 담는다. "구성원" 만으로는 무엇의 구성원인지 칩만 보고 못 읽는다.
    `name`·`size_gb` 가 홈 디스크 아래 실제 상세 행과 같은 값이라 중복 디바이스로 보일 수 있어, `meta` 가
    실제 상세가 있는 디스크를 가리킨다.
    """
    home = array_home.get(c.get("id"))
    lvl = _raid_level_num(c.get("raid_level"))
    label = f"RAID{lvl} 구성원" if lvl else "배열 구성원"
    meta = f"-> {home} 에 상세" if home else "-> 다른 디스크에 상세"
    return StorageNode(
        name=c.get("name", ""),
        kind="raid_member",
        kind_label=label,
        size_gb=bytes_to_gb(c.get("size_bytes")),
        meta=meta,
    )


def _build_storage_node(
    d: JsonObject,
    children_map: dict[str | None, list[JsonObject]],
    fs_by_dev: dict[str, MountUsageRaw],
    fs_by_mount: dict[str, MountUsageRaw],
    vg_free_gb: dict[str, float | None],
    visited: set[str | None],
    root_disk: str,
    array_home: dict[str | None, str],
    depth: int = 0,
) -> StorageNode:
    did = d.get("id")
    visited.add(did)
    kind = d.get("type") or "?"
    # 배열/멀티패스 최초 배치 디스크 기록 — 다른 구성원 디스크의 참조 스텁이 "상세는 X 아래"로 안내.
    if kind in _SPANNING_KINDS and did not in array_home:
        array_home[did] = root_disk
    node = StorageNode(
        name=d.get("name", ""),
        kind=kind,
        kind_label=_STORAGE_KIND_LABEL.get(kind, kind),
        size_gb=bytes_to_gb(d.get("size_bytes")),
    )
    node.meta, node.badges = _storage_node_meta(d, kind)

    mp = d.get("mountpoint")
    if mp and not is_swap(kind) and is_data_volume(d.get("fstype"), mp):
        node.mount = mp
        if fs := ((fs_by_dev.get(did) if did else None) or fs_by_mount.get(mp)):
            _attach_fs_usage(node, fs)
            if node.usage_pct is not None:
                # 게이지 있는 행만 — depth 들여쓰기(_STREE_INDENT_PX/행)를 .stree-info 폭 축소로 상쇄해
                # 모든 게이지 시작 x 를 depth 0 기준으로 통일(배지 텍스트 길이 무관, 사용자 보고 버그 원인은
                # 실제로는 텍스트 폭이 아니라 트리 depth — 배지 길이설은 반증됨, depth 로 정정).
                node.gauge_info_width_px = max(_STREE_INFO_MIN_PX, _STREE_INFO_TARGET_PX - depth * _STREE_INDENT_PX)

    direct = children_map.get(did, [])
    for c in direct:
        if c.get("id") in visited:
            # 이미 다른 구성원 디스크에 배치된 배열이면 참조 스텁(빈 디스크 오인 방지), 그 외 중복은 무시.
            if c.get("type") in _SPANNING_KINDS:
                node.children.append(_member_ref_node(c, array_home))
            continue
        node.children.append(
            _build_storage_node(
                c, children_map, fs_by_dev, fs_by_mount, vg_free_gb, visited, root_disk, array_home, depth + 1
            )
        )

    # 미할당 갭 (물리 디스크) — 배정 - 직속 자식(파티션·스왑·전체디스크 PV/RAID) 합. 디스크 직속 자식은 모두
    # 물리 영역 점유라 전부 차감 (part 만 빼면 whole-disk RAID/PV·스왑을 미할당으로 오계상). 확장 여력 추론.
    if is_physical_disk(kind):
        occupied = sum((c.get("size_bytes") or 0) for c in direct)
        gap = (d.get("size_bytes") or 0) - occupied
        if gap >= _UNALLOC_MIN_BYTES:
            node.children.append(
                StorageNode(name="미할당", kind="unallocated", kind_label="미할당", size_gb=bytes_to_gb(gap))
            )

    # VG 여유 (LV 를 담은 PV) — 직속 LV 자식들의 소속 VG 미할당 공간(확장 여력, lvm_vgs.free_bytes).
    vgs_here = {c.get("lvm_vg") for c in direct if c.get("type") == "lvm" and c.get("lvm_vg")}
    for vg in sorted(v for v in vgs_here if v in vg_free_gb):
        node.children.append(
            StorageNode(
                name=f"VG {vg}",
                kind="vg_free",
                kind_label="VG 여유",
                size_gb=vg_free_gb[vg],
                meta="확장 여력 (미할당)",
            )
        )
    return node


def build_storage_tree(
    block_devices: list[JsonObject], lvm_vgs: list[JsonObject], filesystems: list[MountUsageRaw]
) -> list[StorageNode]:
    """스토리지 레이아웃 트리 — block_devices(lsblk) parent 그래프를 물리 디스크 루트로 조립.

    각 계층 노드는 자기 계층 속성만(device_filters 원칙): 디스크=특성·배정, 파티션/LV=fstype·mount·VG,
    fs(마운트 노드)=사용량 2축(bytes+inode, filesystems 조인), VG=free_bytes(확장 여력). 다중 부모(RAID span·
    striped VG)는 DAG 라 최초 도달 디스크 그룹에만 배치(visited 가드). 물리 디스크 미도달 논리 볼륨은 별도 그룹.
    """
    devs = list(block_devices or [])
    by_id = {d.get("id"): d for d in devs if d.get("id")}
    children_map: dict[str | None, list[JsonObject]] = {}
    for d in devs:
        children_map.setdefault(d.get("parent"), []).append(d)

    fs_by_mount = {fs.mountpoint: fs for fs in (filesystems or []) if fs.mountpoint}
    fs_by_dev = {fs.device_id: fs for fs in (filesystems or []) if fs.device_id}
    vg_free_gb: dict[str, float | None] = {
        name: bytes_to_gb(v.get("free_bytes")) for v in (lvm_vgs or []) if (name := v.get("name"))
    }

    visited: set[str | None] = set()
    # 배열 id -> 최초 배치 디스크명 (구성원 참조 스텁 안내용). id 미발행 device 는 None 키로 들어온다.
    array_home: dict[str | None, str] = {}
    roots = [
        _build_storage_node(d, children_map, fs_by_dev, fs_by_mount, vg_free_gb, visited, d.get("name", ""), array_home)
        for d in devs
        if is_physical_disk(d.get("type"))
    ]
    # 물리 디스크에서 도달 못한 논리 볼륨(디스크 부재·부모 미상) — 디스크별 그룹 밖. 순서 안정 위해 원본 순.
    orphans = [
        _build_storage_node(d, children_map, fs_by_dev, fs_by_mount, vg_free_gb, visited, d.get("name", ""), array_home)
        for d in devs
        if d.get("id") not in visited
        and not is_physical_disk(d.get("type"))
        and (d.get("parent") is None or d.get("parent") not in by_id)
    ]
    return roots + orphans


def to_server_detail(dto: ServerDetail) -> ServerDetailResponse:
    detail = ServerDetailResponse(
        id=dto.id,
        public_id=dto.public_id,
        agent_id=dto.agent_id,
        composite_id=dto.composite_id,
        machine_id=dto.machine_id,
        hostname=dto.hostname,
        agent_version=dto.agent_version,
        os_family=dto.os_family,
        os_id=dto.os_id,
        os_version=dto.os_version,
        os_codename=dto.os_codename,
        kernel_version=dto.kernel_version,
        cpu_cores=dto.cpu_cores,
        cpu_model=dto.cpu_model,
        cpu_arch=dto.cpu_arch,
        cpu_bits=dto.cpu_bits,
        # Memory 표시 — RAM 계열 단일진실 bytes_to_gib(1dp, 카탈로그). 인접 스왑(bytes_to_gib)·환경 KPI·보고서와
        # 정밀도 통일. disksize 필터(소스 정밀도 그대로)와 조합 — detail.html 은 storagesize(.2f 강제) 아닌 disksize.
        mem_total_gb=bytes_to_gib(dto.mem_total_bytes),
        swap_total_gb=bytes_to_gib(swap_total_bytes(dto.block_devices)),
        boot_time=dto.boot_time,
        agent_started_at=dto.agent_started_at,
        ip_internal=_to_ip_addrs(dto.net_interfaces),
        ip_external=_ext_ip_addrs(dto.ip_external) if dto.ip_external else None,
        disks=[item for d in dto.block_devices if (item := _to_disk_item(d)) is not None],
        services=_services_or_none(dto.services, listen_ports=dto.listen_ports),
        listen_ports=[_to_listen_port_item(p) for p in dto.listen_ports],
        last_seen_at=dto.last_seen_at,
        product_name=dto.product_name,
        edition=dto.edition,
    )
    # 파일시스템 항목 — block_devices 중 마운트된 데이터 볼륨 노드. 물리 디스크(type=disk)와 별개 축, 양 OS 일관(fstype 명시).
    detail.volumes = _to_volumes(dto.block_devices)
    # 스토리지 3계층 — storage_layers_gb 단일 산식(배정/파일시스템/미할당). 소비처별 재구현 금지(#C).
    detail.disk_total_gb, detail.volume_total_gb, detail.disk_unallocated_gb = storage_layers_gb(
        dto.block_devices or []
    )
    return enrich_server_detail(detail)


def to_storage_detail(dto: StorageWithUsage) -> StorageDetailResponse:
    physical_disks = [d for d in dto.block_devices if is_physical_disk(d.get("type"))]

    # 마운트별 사용량 — filesystems(df 시계열)가 used/free/fstype/inode 를 함께 실어 단일 루프. lsblk 트리
    # (block_devices)는 물리 디스크 목록에만 쓰고, 마운트 사용량은 filesystems 단일 소스.
    mounts: list[MountUsageItem] = []
    for fs in dto.filesystems:
        path = fs.mountpoint
        if not is_data_volume(fs.fstype, path):
            continue
        mounts.append(
            _build_mount_item(mount=path, fstype=fs.fstype, used_bytes=fs.used_bytes, free_bytes=fs.free_bytes)
        )

    collected_ats = [fs.collected_at for fs in dto.filesystems if fs.collected_at is not None]
    snapshot_at = max(collected_ats) if collected_ats else None

    # 배정/미할당(블록 계층)만 storage_layers_gb 공유 — 시스템 정보 카드와 동일 값. 볼륨 배정 층(_fs)은 미사용:
    # 이 탭의 "파일시스템 총 용량"은 df 실용량(fs_total_gb, 아래)이라 별개 축(블록크기가 아니라 df used+free).
    allocated_gb, _fs, unallocated_gb = storage_layers_gb(dto.block_devices or [])
    return StorageDetailResponse(
        server_id=dto.server_id,
        public_id=dto.public_id,
        hostname=dto.hostname,
        disks=[item for d in physical_disks if (item := _to_disk_item(d)) is not None],
        mounts=sorted(mounts, key=lambda m: m.mount),
        fs_total_gb=sum((m.total_gb for m in mounts if m.total_gb is not None), 0.0) if mounts else None,
        snapshot_at=snapshot_at,
        inventory_at=dto.inventory_at,
        disk_total_gb=allocated_gb,
        disk_unallocated_gb=unallocated_gb,
        tree=build_storage_tree(dto.block_devices or [], dto.lvm_vgs or [], dto.filesystems),
        os_family=dto.os_family,
    )


def _build_mount_item(
    mount: str,
    fstype: str | None,
    used_bytes: int | None,
    free_bytes: int | None,
) -> MountUsageItem:
    total_bytes = (used_bytes + free_bytes) if (used_bytes is not None and free_bytes is not None) else None
    pct = usage_pct(used_bytes, total_bytes)
    return MountUsageItem(
        mount=mount,
        fstype=fstype,
        total_gb=bytes_to_gb(total_bytes),
        used_gb=bytes_to_gb(used_bytes),
        avail_gb=bytes_to_gb(free_bytes),
        usage_pct=pct,
        badge_class=_usage_badge_class(pct),
        bar_color=_MOUNT_BAR_COLOR,
    )


def to_network_detail(dto: NetworkWithIo) -> NetworkDetailResponse:
    collected_ats = [r.collected_at for r in dto.net_io]
    return NetworkDetailResponse(
        server_id=dto.server_id,
        public_id=dto.public_id,
        hostname=dto.hostname,
        ip_internal=_to_ip_addrs(dto.net_interfaces),
        ip_external=_ext_ip_addrs(dto.ip_external) if dto.ip_external else None,
        interfaces=compute_net_io(dto.net_io),
        inventory_at=dto.inventory_at,
        snapshot_at=max(collected_ats) if collected_ats else None,
        interfaces_info=build_network_interfaces(dto.net_interfaces, dto.link_speed_by_iface),
        os_family=dto.os_family,
    )


# --- enrich_server_detail (idempotent) ------------------------------------
# cache_serializer가 역직렬화 후 재호출 가능 — 두 번 호출해도 결과 동일.
# 입력 detail의 services / listen_ports 만 read-only로 사용, 파생 필드만 갱신.


def enrich_server_detail(detail: ServerDetailResponse) -> ServerDetailResponse:
    services = detail.services or []
    seen_chip_keys: set[str] = set()
    shown_port_numbers: set[int] = set()
    known: list[ServiceItem] = []

    # 카테고리 단위 포트 집계 단일 진실 — listen 소켓을 카테고리로 직접 분류한 결과.
    listen_dicts: list[JsonObject] = [
        {"proto": lp.proto, "port": lp.port, "comm": lp.comm} for lp in detail.listen_ports
    ]
    listen_by_cat = detect_listen_categories(listen_dicts)
    # 카드는 "실행 중인 서비스 카테고리" 요약 — 카테고리당 뱃지 1개(롤업). 같은 카테고리 유닛들의 matched_ports
    # (단일 규칙: pid 소유 포트 정확 귀속 + pid 없는 포트만 카테고리 fallback) union + 카테고리 listen 보강.
    # per-unit 상세(ssh.service·sshd-unix-local.socket 각각)는 서비스·포트 상세 표(sorted_services)가 담당 —
    # 두 화면이 같은 matched_ports 규칙을 쓰되, 카드는 카테고리 집계·표는 유닛 나열로 역할 분리(포트 이중귀속·빈 행 0).
    by_cat: dict[str, list[ServiceItem]] = {}
    for svc in services:
        if svc.category != "unknown":
            by_cat.setdefault(svc.category, []).append(svc)

    for category, svcs in by_cat.items():
        ports: list[MatchedPort] = []
        for svc in svcs:  # 유닛별 정확 귀속 포트 union
            for p in svc.ports:
                key = f"{p.proto}:{p.port}"
                if key not in seen_chip_keys:
                    seen_chip_keys.add(key)
                    shown_port_numbers.add(p.port)
                    ports.append(p)
        # 카테고리 listen 보강 — comm 귀속 실패한 워크로드 포트(W3SVC<->System 80 등)를 카테고리 뱃지에.
        for p in listen_by_cat.get(category, []):
            key = f"{p.proto}:{p.port}"
            if key not in seen_chip_keys:
                seen_chip_keys.add(key)
                shown_port_numbers.add(p.port)
                ports.append(p)
        ports.sort(key=lambda mp: (mp.port, mp.proto))  # P3 — 칩 정렬은 mapper 단일
        # 대표 유닛 — 포트 보유(실 listener) 우선, 없으면 첫 유닛.
        rep = next((s for s in svcs if s.ports), svcs[0])
        known.append(
            ServiceItem(unit=rep.unit, sub=rep.sub, category=category, ports=ports, display_name=rep.display_name)
        )

    # listen-only 워크로드 보충 (ADR 0032 union) — services 이름이 못 잡았지만 listen 소켓이
    # 증거하는 카테고리를 합성 뱃지로 추가. opaque 한 Windows SCM 이름을 1433/sqlservr 같은
    # 깨끗한 listen 신호로 구제. unit/display_name 없음 (특정 service 에 귀속 불가, T15).
    known_categories = {s.category for s in known}
    for category, ports in listen_by_cat.items():
        if category in known_categories:
            continue
        deduped_ports: list[MatchedPort] = []
        for p in ports:
            key = f"{p.proto}:{p.port}"
            if key in seen_chip_keys:
                continue
            seen_chip_keys.add(key)
            shown_port_numbers.add(p.port)
            deduped_ports.append(p)
        deduped_ports.sort(key=lambda mp: (mp.port, mp.proto))
        known.append(ServiceItem(unit="", sub="", category=category, ports=deduped_ports, display_name=""))

    # 뱃지 정렬 단일 기준 — 서버목록(_dedup_known)과 동일하게 category 알파벳 오름차순.
    known.sort(key=lambda s: s.category)
    detail.known_services = known
    # 이름 분류와 listen 소켓 탐지를 합쳐도 아무 카테고리를 못 잡았을 때만 unknown 뱃지.
    detail.show_unknown_badge = detail.services is not None and bool(detail.services) and not known
    detail.key_listen_ports = sorted(
        [lp for lp in detail.listen_ports if lp.is_significant and lp.port not in shown_port_numbers],
        key=lambda lp: (lp.port, lp.proto),
    )

    # 템플릿(P3)이 sort 못 하도록 mapper에서 한 번만 정렬
    detail.sorted_services = sorted(detail.services or [], key=lambda s: s.unit) if detail.services else []
    detail.sorted_listen_ports = sorted(detail.listen_ports, key=lambda lp: (lp.port, lp.proto))

    detail.os_display = _os_display(detail.os_id, detail.os_version, detail.kernel_version, detail.product_name)

    cpu_parts = [p for p in [detail.cpu_model, f"{detail.cpu_cores} cores" if detail.cpu_cores else None] if p]
    detail.cpu_display = " ".join(cpu_parts) or "-"

    # disk_total_gb·volume_total_gb·disk_unallocated_gb 는 to_server_detail 에서 storage_layers_gb 단일 산식으로 설정(#C).

    # P3 — count는 mapper에서 한 번만 계산. 템플릿이 `| length` 못 쓰도록.
    detail.services_count = len(detail.services or [])
    detail.listen_ports_count = len(detail.listen_ports)
    detail.disks_count = len(detail.disks)
    detail.volumes_count = len(detail.volumes)

    return detail


# --- role 추론 — services[].unit → 카테고리 빈도 최다 (export · report · attention 공용) ---


def workload_category_counter(
    services: list[JsonObject] | None,
    listen_ports: list[JsonObject] | None = None,
) -> Counter[str]:
    """호스트 워크로드 카테고리 카운터 — services 이름 분류와 listen 소켓 탐지의 합집합 (ADR 0032).

    services 이름 분류는 인스턴스 카운트(서버목록 뱃지와 일관). 단 런타임 스택(container)은
    구성 요소가 여러 서비스로 떠도 호스트당 1 (docker+containerd → container 1).
    listen 소켓 탐지(`detect_listen_categories`)는 이름이 못 잡은 카테고리만 +1 보충 — 같은
    워크로드 이중 카운트 회피. agent join key 부재(T15)로 opaque 한 service 이름을 listen 증거로 구제.
    role/뱃지/환경 분포 단일 진실.
    """
    # baseline(OS 기본·관리 — SSH·NTP·RPC 등)은 특징 워크로드 아님 -> 제외 (compute_service_categories 와 동일 기준).
    counter: Counter[str] = Counter()
    for s in services or []:
        unit = s.get("unit") if isinstance(s, dict) else None
        if not unit or is_baseline_service(unit):
            continue
        cat = classify(unit, listen_ports, s.get("pid"))
        if cat == "unknown":
            continue
        # 런타임 스택(container)은 구성 요소가 여러 서비스로 떠도 호스트당 1 (docker+containerd → 1).
        counter[cat] = 1 if cat in SINGLE_INSTANCE_CATEGORIES else counter[cat] + 1
    name_cats = set(counter)
    non_baseline_ports = [p for p in (listen_ports or []) if not is_baseline_socket(p)]
    for cat in detect_listen_categories(non_baseline_ports):
        if cat not in name_cats:
            counter[cat] = 1
    return counter


def workload_services_by_category(
    services: list[JsonObject] | None,
    listen_ports: list[JsonObject] | None = None,
) -> dict[str, list[str]]:
    """카테고리별 특징 서비스명 목록 — workload_category_counter 와 동일 기준(baseline 제외 + classify, unknown 제외).

    서비스 구성 breakdown 이 카테고리 카운트(total_count)와 같은 소스를 쓰게 해 total 과 종류 합 정합 + systemd/rpc
    등 OS 노이즈 배제. listen-only 로만 탐지된 카테고리는 이름 미상이라 여기 미포함(카테고리 자체는
    workload_category_counter 가 노출 — breakdown 에서 "(포트 탐지)"로 별도 합산). 표시명 = display_name 우선 unit.
    """
    by_cat: dict[str, list[str]] = {}
    for s in services or []:
        if not isinstance(s, dict):
            continue
        unit = s.get("unit")
        if not unit or is_baseline_service(unit):
            continue
        cat = classify(unit, listen_ports, s.get("pid"))
        if cat == "unknown":
            continue
        by_cat.setdefault(cat, []).append(s.get("display_name") or unit)
    return by_cat


def infer_role(services: list[JsonObject] | None, listen_ports: list[JsonObject] | None = None) -> str:
    """호스트 대표 역할 — `workload_category_counter` 최빈 카테고리. 비면 "unknown"."""
    counter = workload_category_counter(services, listen_ports)
    if not counter:
        return "unknown"
    return counter.most_common(1)[0][0]
