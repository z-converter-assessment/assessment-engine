"""서버 표시 mapper — 인벤토리·서비스·디스크·마운트·네트워크 표시 변환 + role 추론."""

from collections import Counter
from typing import TYPE_CHECKING, Literal

from assessment_engine.domain import right_sizing
from assessment_engine.domain.service_classifier import (
    SIGNATURE_CATEGORIES,
    SINGLE_INSTANCE_CATEGORIES,
    MatchedPort,
    classify_service,
    detect_listen_categories,
    is_baseline_service,
    is_baseline_socket,
    matched_ports,
)
from assessment_engine.json_types import JsonObject, json_list, json_str_list
from assessment_engine.web.services.device_filters import (
    disk_total_bytes,
    is_data_volume,
    is_physical_disk,
    is_swap,
    is_virtual_interface,
    swap_total_bytes,
)
from assessment_engine.web.services.mappers.constants import _USAGE_DANGER_PCT, _USAGE_WARN_PCT
from assessment_engine.web.services.mappers.host_display import spec_display_line
from assessment_engine.web.services.mappers.metric_dashboard import compute_net_io
from assessment_engine.web.services.mappers.os_eol import (
    lookup_os_eol,
    os_eol_display,
    os_id_to_distro,
    windows_legacy_version_from_build,
    windows_short_label_from_product_name,
)
from assessment_engine.web.services.mappers.resource_stats import build_resource_stats
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

# IANA 동적/private 포트 하한. 이 위는 RPC 동적 할당·ephemeral 이라 "주요 포트" 에서 뺀다.
# 그 아래는 고포트여도(RDP 3389·Redis 6379) 의도된 서비스 리스너로 본다.
# cache_serializer 가 본 상수를 import 해 역직렬화 fallback 을 같은 기준으로 판정한다 — 공개 상수인 이유.
DYNAMIC_PORT_MIN = 49152

type _Severity = Literal["ok", "warn", "danger"]

_BADGE_CLASS_BY_SEVERITY: dict[_Severity, str] = {
    "ok": "badge-ok",
    "warn": "badge-warn",
    "danger": "badge-danger",
}
# 게이지 막대는 임계별로 색을 가르지 않는다 — 위험/주의 신호는 badge_class 가 담당한다 (사용자 결정).
_MOUNT_BAR_COLOR = "var(--color-title)"  # base.html :root 테마색1


def _usage_severity(pct: float | None) -> _Severity:
    if pct is None or pct < _USAGE_WARN_PCT:
        return "ok"
    if pct < _USAGE_DANGER_PCT:
        return "warn"
    return "danger"


def _usage_badge_class(pct: float | None) -> str:
    return _BADGE_CLASS_BY_SEVERITY[_usage_severity(pct)] if pct is not None else ""


def _to_ip_addrs(net_interfaces: list[JsonObject]) -> list[IpAddr]:
    """net_interface 노드 목록 -> IpAddr(value=CIDR). loopback 제외, 나머지는 전 주소 표출.

    IPv4 를 앞에 세운다 — 실제 접속·식별 주력이고 IPv6(ULA/link-local)는 보조다.
    """
    items: list[IpAddr] = []
    for iface in net_interfaces or []:
        if iface.get("kind") == "loopback":
            continue
        for a in json_list(iface, "addresses"):
            addr = a.get("address", "")
            prefix = a.get("prefix")
            value = f"{addr}/{prefix}" if prefix is not None else addr
            items.append(IpAddr(value=value, is_ipv4=a.get("family") == "ipv4"))
    return sorted(items, key=lambda x: not x.is_ipv4)


def _ext_ip_addrs(ips: list[str] | None) -> list[IpAddr]:
    """외부 IP(평문 list) -> IpAddr. prefix/family 가 없어 is_ipv4 는 ':' 유무로 추정한다."""
    items = [IpAddr(value=s, is_ipv4=":" not in s) for s in ips or []]
    return sorted(items, key=lambda x: not x.is_ipv4)


def build_network_interfaces(
    net_interfaces: list[JsonObject], link_speed_by_iface: dict[str, int] | None = None
) -> list[NetworkInterfaceInfo]:
    """네트워크 인터페이스 정적 정보 — "네트워크 정보" 카드 소비. 물리(physical·bond_master)만 돌려준다.

    speed_mbps 는 인벤토리 원본 우선, null(virtio·Windows NT5.2 에서 흔하다)이면 link_speed_by_iface
    (bit/s) 로 폴백한다 — environment.py 의 동일 목적 폴백과 단일 산식.
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
                # .get(key, "") 는 키가 있고 값이 None 이면 None 을 돌려준다 — id 가 nullable 이라 or 로 받는다.
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
    """마운트된 데이터 볼륨 노드 -> VolumeItem. 물리 디스크(type=disk)와 별개 축이다."""
    volumes: list[VolumeItem] = []
    for d in block_devices or []:
        path = d.get("mountpoint")
        fstype = d.get("fstype")
        if not path or is_swap(d.get("type")) or not is_data_volume(fstype, path):
            continue  # swap 은 mountpoint 를 갖지만("[SWAP]"·pagefile) 데이터 볼륨이 아니다
        volumes.append(VolumeItem(mount=path, fstype=fstype, total_gb=bytes_to_gb(d.get("size_bytes"))))
    return sorted(volumes, key=lambda v: v.mount)


def _to_disk_item(d: JsonObject) -> DiskItem | None:
    """물리 디스크가 아니면 None."""
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
    """listen_ports 를 주면 귀속 포트를 채운다(상세). 안 주면 빈 리스트(목록)."""
    unit = s.get("unit", "")
    return ServiceItem(
        unit=unit,
        sub=s.get("sub", ""),
        category=classify_service(unit, listen_ports, s.get("pid")),
        ports=matched_ports(unit, listen_ports, s.get("pid")) if listen_ports else [],
        display_name=unit.removesuffix(".service"),
    )


def _services_or_none(
    raw: list[JsonObject] | None,
    listen_ports: list[JsonObject] | None = None,
) -> list[ServiceItem] | None:
    """None 을 보존한다 — non-systemd 호스트는 "서비스 없음" 이 아니라 미수집이다."""
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
        # 라벨 우선순위 셋. ProductName 연도/세대가 1순위, 없으면 os_version 그대로(SAC 는 이쪽이 정확),
        # os_version 마저 비면(레거시 Server 2012 R2 이하) kernel build 로 보강한다.
        short = windows_short_label_from_product_name(product_name)
        if short:
            ver = short
        elif not ver:
            ver = windows_legacy_version_from_build(kernel_version)
    parts = [p for p in [os_id, ver] if p]
    return " ".join(parts) or "-"


def build_server_inventory(
    detail: ServerDetail, is_online: bool, raw: ReportRowRaw | None = None
) -> ServerInventorySnapshot:
    """개별 보고서 인벤토리 — 전체 IP·하드웨어·식별자를 생략 없이 싣는다.

    raw 는 server_inventory 재현 필드(arch·boot_firmware·secure_boot·edition·timezone) 보강용이다.
    환경/N대처럼 raw 를 안 가진 스코프에서는 그 필드들만 None 이 된다.
    """
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


def to_server_list_item(
    dto: ServerSummary,
    raw_period: ReportRowRaw | None = None,
    today: date | None = None,
    error_hosts: set[int] | None = None,
) -> ServerListItem:
    """ServerSummary -> ServerListItem.

    raw_period 가 없으면(페이지 2+ 등) 분류 칸은 빈 문자열로 둔다. today 를 주면 OS 지원 단계를
    판정하되, 카탈로그 미수록·미매칭은 "미상" 으로 남긴다 — "지원 중" 으로 단정하지 않는다.
    """
    info = lookup_os_eol(dto.os_id, dto.os_version, dto.kernel_version, today) if today else None
    if info is None:
        os_eol, os_eol_status = "", ("unknown" if today else "")
    else:
        os_eol, os_eol_status = info.eol_iso, info.status
    os_eol_disp = os_eol_display(os_eol_status, os_eol)
    _disk_bytes = disk_total_bytes(dto.block_devices)
    _disk_gb = bytes_to_gb(_disk_bytes) if _disk_bytes else None
    storage_total_gb = round(_disk_gb, 1) if _disk_gb is not None else None

    _mem_gib = bytes_to_gib(dto.mem_total_bytes)
    spec_display = spec_display_line(dto.cpu_cores, dto.mem_total_bytes, dto.block_devices)

    # 목록 뱃지는 시그니처 워크로드만 — file·mail·infra·remote 같은 유틸/관리 서비스는 서버 성격
    # 신호가 약해 노이즈가 된다. 상세 페이지는 live classify 로 전부 보여준다.
    known = [
        ServiceItem(unit="", sub="", category=cat, ports=[])
        for cat in dto.service_categories
        if cat in SIGNATURE_CATEGORIES
    ]
    services = known
    show_unknown = False

    rec_label, seg_key = "", ""
    if raw_period is not None:
        # 네트워크 혼잡은 여기 배지에 안 싣는다 — host_status 를 구동하지 않는 별개 트리거(재전송·드롭)라
        # 분류 칼럼에 붙으면 분류의 일부로 읽히는데 화면에서 근거를 확인할 수 없다. 서버 상세에서 본다.
        host = right_sizing.rollup_host(build_resource_stats(raw_period, disk_baseline=None))
        rec = right_sizing.host_status_to_recommendation(host.host_status)
        seg_key = rec
        rec_label = right_sizing.RECOMMENDATION_LABEL_KO[seg_key]

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
    """스토리지 블록 계층 3분 (GB) — (배정 블록, 볼륨 배정, 미할당).

    여기는 lsblk 블록크기 축이다. df 실용량("파일시스템 총 용량", to_storage_detail)은 fs 포맷
    오버헤드만큼 작게 나오는 별개 축이라 값이 다른 것이 정상이고, 화면은 라벨로 둘을 구분한다.
    """
    allocated = bytes_to_gb(disk_total_bytes(block_devices))
    fs_bytes = sum(
        (d.get("size_bytes") or 0)
        for d in block_devices or []
        if d.get("mountpoint")
        and not is_swap(d.get("type"))  # swap 은 파일시스템 층이 아니다
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

# storage.html CSS ".stree ul"(margin-left 16 + border-left 1 + padding-left 16) 합과 동기화 의무.
_STREE_INDENT_PX = 33
_STREE_INFO_TARGET_PX = 340  # depth 0 기준 게이지 시작 목표 x(=.stree-info 폭)
_STREE_INFO_MIN_PX = 160  # 깊은 중첩에서 정보 컬럼이 붕괴하지 않게 — 그 아래로는 정렬을 근사로 포기


def _rotational_label(rotational: object) -> str:
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
    """raid_level -> 숫자 문자열. 입력이 "raid5"·"5"·5 로 섞여 온다."""
    if raid_level is None:
        return ""
    s = str(raid_level).lower().removeprefix("raid")
    return s or ""


def _storage_node_meta(d: JsonObject, kind: str) -> tuple[str, list[str]]:
    """계층별 자기 속성만 -> (meta 한 줄, badges). 없는 축은 생략한다."""
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
            parts.append(str(fst))
    elif kind == "raid":
        if lvl := _raid_level_num(d.get("raid_level")):
            badges.append(f"RAID{lvl}")
    elif kind == "crypt":
        ct = d.get("crypt_type")
        badges.append(str(ct).upper() if ct and "luks" in str(ct).lower() else "암호화")
    elif kind == "swap":
        parts.append("[SWAP]")
    if kind in ("part", "volume") or (kind == "lvm" and d.get("mountpoint")):
        if fst := d.get("fstype"):
            parts.append(str(fst))
        if mp := d.get("mountpoint"):
            parts.append(str(mp))
    return " · ".join(parts), badges


def _attach_fs_usage(node: StorageNode, fs: MountUsageRaw) -> None:
    """마운트 노드에 사용량 2축 precompute. inode 미측정(Windows)이면 inode 축은 None 으로 남는다."""
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
    """이미 다른 디스크 아래 배치된 배열의 참조 스텁 — 빈 디스크로 오인하지 않게.

    `kind_label` 에 배열 종류를 담는다. "구성원" 만으로는 무엇의 구성원인지 칩만 보고 못 읽는다.
    name·size_gb 가 홈 디스크의 실제 상세 행과 같아 중복 디바이스로 보이므로, meta 가 그 디스크를 가리킨다.
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
    # 최초 배치 디스크를 기록해 둬야 다른 구성원 디스크의 참조 스텁이 "상세는 X 아래" 로 안내한다.
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
                # 게이지 시작 x 를 흔드는 것은 배지 텍스트 길이가 아니라 트리 depth 다. depth 만큼
                # 밀린 들여쓰기를 .stree-info 폭 축소로 상쇄해 모든 게이지를 depth 0 기준에 맞춘다.
                node.gauge_info_width_px = max(_STREE_INFO_MIN_PX, _STREE_INFO_TARGET_PX - depth * _STREE_INDENT_PX)

    direct = children_map.get(did, [])
    for c in direct:
        if c.get("id") in visited:
            # 이미 배치된 배열만 참조 스텁으로 남기고, 그 외 중복은 무시한다.
            if c.get("type") in _SPANNING_KINDS:
                node.children.append(_member_ref_node(c, array_home))
            continue
        node.children.append(
            _build_storage_node(
                c, children_map, fs_by_dev, fs_by_mount, vg_free_gb, visited, root_disk, array_home, depth + 1
            )
        )

    # 미할당 갭 — 직속 자식은 종류를 가리지 않고 전부 차감한다. part 만 빼면 whole-disk PV/RAID·스왑을
    # 미할당으로 오계상한다.
    if is_physical_disk(kind):
        occupied = sum((c.get("size_bytes") or 0) for c in direct)
        gap = (d.get("size_bytes") or 0) - occupied
        if gap >= _UNALLOC_MIN_BYTES:
            node.children.append(
                StorageNode(name="미할당", kind="unallocated", kind_label="미할당", size_gb=bytes_to_gb(gap))
            )

    # 직속 LV 가 속한 VG 의 미할당 공간 — 디스크를 더 붙이지 않고 바로 늘릴 수 있는 여력.
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
    """스토리지 레이아웃 트리 — block_devices parent 그래프를 물리 디스크 루트로 조립.

    다중 부모(RAID span·striped VG)가 있어 원본은 트리가 아니라 DAG 다. 최초 도달 디스크 그룹에만
    실제로 배치하고(visited 가드) 나머지는 참조 스텁으로 남긴다. 물리 디스크에서 못 닿는 논리 볼륨은
    별도 루트 그룹으로 뒤에 붙는다.
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
    # 배열 id -> 최초 배치 디스크명. id 를 발행하지 않은 device 는 None 키로 들어온다.
    array_home: dict[str | None, str] = {}
    roots = [
        _build_storage_node(d, children_map, fs_by_dev, fs_by_mount, vg_free_gb, visited, d.get("name", ""), array_home)
        for d in devs
        if is_physical_disk(d.get("type"))
    ]
    # 부모가 없거나 미상이라 디스크에서 못 닿는 논리 볼륨. 순서를 흔들지 않으려고 원본 순서를 지킨다.
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
        # RAM 계열은 GiB 1자리 — 스왑·환경 KPI·보고서와 정밀도를 맞춘다.
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
    detail.volumes = _to_volumes(dto.block_devices)
    detail.disk_total_gb, detail.volume_total_gb, detail.disk_unallocated_gb = storage_layers_gb(
        dto.block_devices or []
    )
    return enrich_server_detail(detail)


def to_storage_detail(dto: StorageWithUsage) -> StorageDetailResponse:
    physical_disks = [d for d in dto.block_devices if is_physical_disk(d.get("type"))]

    # 마운트 사용량은 filesystems(df) 단일 소스다 — lsblk 트리는 물리 디스크 목록에만 쓴다.
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

    # 볼륨 배정 층(_fs)은 안 쓴다 — 이 탭의 "파일시스템 총 용량" 은 블록크기가 아니라 df 실용량이다.
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


# cache_serializer 가 역직렬화 후 다시 부르므로 idempotent 여야 한다 — services·listen_ports 는
# read-only 로만 읽고 파생 필드만 덮는다.


def enrich_server_detail(detail: ServerDetailResponse) -> ServerDetailResponse:
    services = detail.services or []
    seen_chip_keys: set[str] = set()
    shown_port_numbers: set[int] = set()
    known: list[ServiceItem] = []

    listen_dicts: list[JsonObject] = [
        {"proto": lp.proto, "port": lp.port, "comm": lp.comm} for lp in detail.listen_ports
    ]
    listen_by_cat = detect_listen_categories(listen_dicts)
    # 카드는 카테고리당 뱃지 1개로 롤업한다. 유닛별 나열(ssh.service·sshd-unix-local.socket 각각)은
    # 아래 sorted_services 표가 맡는다 — 같은 matched_ports 규칙을 쓰되 역할만 가른다.
    by_cat: dict[str, list[ServiceItem]] = {}
    for svc in services:
        if svc.category != "unknown":
            by_cat.setdefault(svc.category, []).append(svc)

    for category, svcs in by_cat.items():
        ports: list[MatchedPort] = []
        for svc in svcs:
            for p in svc.ports:
                key = f"{p.proto}:{p.port}"
                if key not in seen_chip_keys:
                    seen_chip_keys.add(key)
                    shown_port_numbers.add(p.port)
                    ports.append(p)
        # comm 으로 unit 에 못 붙은 포트(IIS W3SVC 의 80 이 System 소유로 잡히는 식)를 카테고리로 보강.
        for p in listen_by_cat.get(category, []):
            key = f"{p.proto}:{p.port}"
            if key not in seen_chip_keys:
                seen_chip_keys.add(key)
                shown_port_numbers.add(p.port)
                ports.append(p)
        ports.sort(key=lambda mp: (mp.port, mp.proto))
        rep = next((s for s in svcs if s.ports), svcs[0])  # 실 listener 를 대표 유닛으로
        known.append(
            ServiceItem(unit=rep.unit, sub=rep.sub, category=category, ports=ports, display_name=rep.display_name)
        )

    # 이름이 못 잡았지만 listen 소켓이 증거하는 카테고리를 합성 뱃지로 보충한다 — opaque 한 Windows
    # SCM 이름을 1433/sqlservr 같은 깨끗한 신호로 구제. 특정 unit 에 귀속할 수 없어 unit 은 빈다.
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

    known.sort(key=lambda s: s.category)
    detail.known_services = known
    detail.show_unknown_badge = detail.services is not None and bool(detail.services) and not known
    detail.key_listen_ports = sorted(
        [lp for lp in detail.listen_ports if lp.is_significant and lp.port not in shown_port_numbers],
        key=lambda lp: (lp.port, lp.proto),
    )

    # 정렬·개수는 여기서 한 번만 낸다 — 템플릿은 계산하지 않는다.
    detail.sorted_services = sorted(detail.services or [], key=lambda s: s.unit) if detail.services else []
    detail.sorted_listen_ports = sorted(detail.listen_ports, key=lambda lp: (lp.port, lp.proto))

    detail.os_display = _os_display(detail.os_id, detail.os_version, detail.kernel_version, detail.product_name)

    cpu_parts = [p for p in [detail.cpu_model, f"{detail.cpu_cores} cores" if detail.cpu_cores else None] if p]
    detail.cpu_display = " ".join(cpu_parts) or "-"

    # 스토리지 3계층은 여기가 아니라 to_server_detail 이 storage_layers_gb 로 채운다.
    detail.services_count = len(detail.services or [])
    detail.listen_ports_count = len(detail.listen_ports)
    detail.disks_count = len(detail.disks)
    detail.volumes_count = len(detail.volumes)

    return detail


def workload_category_counter(
    services: list[JsonObject] | None,
    listen_ports: list[JsonObject] | None = None,
) -> Counter[str]:
    """호스트 워크로드 카테고리 카운터 — 이름 분류와 listen 소켓 탐지의 합집합. role·뱃지·환경 분포 공용.

    이름 분류는 인스턴스 수를 센다. 런타임 스택(container)만 예외로 호스트당 1 — 구성 요소가 여러
    서비스로 뜨기 때문이다(docker+containerd). listen 탐지는 이름이 못 잡은 카테고리만 보충해
    같은 워크로드를 두 번 세지 않는다.
    """
    # baseline(SSH·NTP·RPC 등 OS 기본·관리)은 특징 워크로드가 아니다.
    counter: Counter[str] = Counter()
    for s in services or []:
        unit = s.get("unit") if isinstance(s, dict) else None
        if not unit or is_baseline_service(unit):
            continue
        cat = classify_service(unit, listen_ports, s.get("pid"))
        if cat == "unknown":
            continue
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
    """카테고리별 특징 서비스명 — `workload_category_counter` 와 같은 기준이라 카운트와 종류 합이 맞는다.

    listen-only 로만 탐지된 카테고리는 이름을 모르므로 여기 안 담긴다. 그 카테고리 자체는
    `workload_category_counter` 가 노출하고 breakdown 에서 "(포트 탐지)" 로 따로 합산된다.
    """
    by_cat: dict[str, list[str]] = {}
    for s in services or []:
        if not isinstance(s, dict):
            continue
        unit = s.get("unit")
        if not unit or is_baseline_service(unit):
            continue
        cat = classify_service(unit, listen_ports, s.get("pid"))
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
