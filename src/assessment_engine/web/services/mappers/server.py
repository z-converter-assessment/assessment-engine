"""서버 표시 mapper — ServerSummary/ServerDetail/StorageWithUsage/NetworkWithIo → ViewModel (P2).

본 sub-module 책임: 인벤토리·서비스·디스크·마운트·네트워크 표시 변환 + role 추론.
다른 sub-module 이 import 하는 항목: `infer_role`, `DYNAMIC_PORT_MIN`, `enrich_server_detail`.
"""

from collections import Counter
from typing import Literal

from assessment_engine import recommendation
from assessment_engine.db.dtos.outbound import (
    NetworkWithIo,
    ServerDetail,
    ServerSummary,
    StorageWithUsage,
)
from assessment_engine.service_classifier import (
    SIGNATURE_CATEGORIES,
    SINGLE_INSTANCE_CATEGORIES,
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
    swap_total_bytes,
)
from assessment_engine.web.services.mappers.shared import (
    _DONUT_SEGMENT_DEFS,
    _DONUT_SEGMENT_FROM_REC,
    _USAGE_DANGER_PCT,
    _USAGE_WARN_PCT,
    os_id_to_distro,
    windows_legacy_version_from_build,
)
from assessment_engine.web.services.metrics_calculator import compute_net_io
from assessment_engine.web.services.unit_converter import bytes_to_gb, bytes_to_gib, usage_pct
from assessment_engine.web.view_models.environment_report import (
    CpuBreakdown,
    MemoryBreakdown,
    ServerInventorySnapshot,
    VolumeUsage,
)
from assessment_engine.web.view_models.server import (
    DiskItem,
    IpAddr,
    ListenPortItem,
    MountUsageItem,
    NetworkDetailResponse,
    ServerDetailResponse,
    ServerListItem,
    ServiceItem,
    StorageDetailResponse,
    VolumeItem,
)

# IANA 동적/private 포트 하한 (49152~65535). 이 이상은 RPC 동적 할당·ephemeral 이라 "주요 포트" 표시에서 제외.
# 그 미만(0~49151)은 system·registered·user 포트 = 의도된 서비스 리스너로 간주해 표시 (RDP 3389·Redis 6379 등 고포트 포함).
# cache_serializer 가 본 상수를 import 해 역직렬화 후 enrich 재호출 시 동일 분기 적용.
DYNAMIC_PORT_MIN = 49152

_Severity = Literal["ok", "warn", "danger"]

_BADGE_CLASS_BY_SEVERITY: dict[_Severity, str] = {
    "ok": "badge-ok",
    "warn": "badge-warn",
    "danger": "badge-danger",
}
# 파일시스템 사용량 게이지 막대 = 테마 주색(blue-500) 단색 (사용자 결정 — 임계별 색 분기 없이 통일).
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


# ─── raw dict → typed ViewModel 단일 변환 진입점 ──────────────────────────


def _to_ip_addrs(net_interfaces: list[dict]) -> list[IpAddr]:
    """net_interface 노드 목록 → IpAddr(value=CIDR, is_ipv4). IPv4 우선 정렬(안정), loopback 제외.

    v2 노드는 kind 는 인터페이스 레벨, 주소는 nested addresses[]({address,prefix,family}) — 다중 IP 호스트는
    전 주소 표출. IPv4 는 실제 접속·식별 주력이라 상단·진하게 표시, IPv6(ULA/link-local)는 보조(연하게).
    """
    items: list[IpAddr] = []
    for iface in net_interfaces or []:
        if iface.get("kind") == "loopback":
            continue  # 표시 무의미
        for a in iface.get("addresses") or []:
            addr = a.get("address", "")
            prefix = a.get("prefix")
            value = f"{addr}/{prefix}" if prefix is not None else addr
            items.append(IpAddr(value=value, is_ipv4=a.get("family") == "ipv4"))
    return sorted(items, key=lambda x: not x.is_ipv4)


def _ext_ip_addrs(ips: list[str] | None) -> list[IpAddr]:
    """외부 IP(list[str] 평문) → IpAddr. prefix/family 정보 없음 — is_ipv4 는 ':' 유무로 추정, CIDR 없이 표시."""
    items = [IpAddr(value=s, is_ipv4=":" not in s) for s in ips or []]
    return sorted(items, key=lambda x: not x.is_ipv4)


def _to_volumes(block_devices: list[dict]) -> list[VolumeItem]:
    """block_devices(lsblk) 중 마운트된 데이터 볼륨 노드 → VolumeItem(파일시스템) 목록 (가상·부트 제외, mount ASC).

    물리 디스크(type=disk)와 별개 축 — 양 OS 일관 표시 (fstype 명시). 마운트된 노드(mountpoint 有)만.
    """
    volumes: list[VolumeItem] = []
    for d in block_devices or []:
        path = d.get("mountpoint")
        fstype = d.get("fstype")
        if not path or is_swap(d.get("type")) or not is_data_volume(fstype, path):
            continue  # swap 노드(mountpoint="[SWAP]"/pagefile)는 데이터 볼륨 아님 — 별도 인벤토리 총량으로만 표시
        volumes.append(VolumeItem(mount=path, fstype=fstype, total_gb=bytes_to_gb(d.get("size_bytes"))))
    return sorted(volumes, key=lambda v: v.mount)


def _to_disk_item(d: dict) -> DiskItem | None:
    """물리 디스크(block_device type=disk) 아니면 None."""
    name = d.get("name", "")
    if not is_physical_disk(d.get("type")):
        return None
    return DiskItem(
        name=name,
        size_gb=bytes_to_gb(d.get("size_bytes")),
    )


def _to_listen_port_item(p: dict) -> ListenPortItem:
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


def _to_service_item(s: dict, listen_ports: list[dict] | None = None) -> ServiceItem:
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
    raw: list[dict] | None,
    listen_ports: list[dict] | None = None,
) -> list[ServiceItem] | None:
    """services는 None을 보존 (non-systemd 호스트 = unknown 표시 대상 아님)."""
    if raw is None:
        return None
    return [_to_service_item(s, listen_ports) for s in raw]


def _os_display(os_id: str | None, os_version: str | None, kernel_version: str | None = None) -> str:
    ver = os_version
    if os_id == "windows" and not ver:
        # 레거시 Windows Server 는 os_version 빈값 -> kernel build 로 버전 보강 ("windows 2012").
        ver = windows_legacy_version_from_build(kernel_version)
    parts = [p for p in [os_id, ver] if p]
    return " ".join(parts) or "-"


def build_server_inventory(detail, is_online: bool) -> ServerInventorySnapshot:
    """ServerDetail -> 개별 보고서 인벤토리 (충실 표시 — 전체 IP(IPv4/IPv6)·하드웨어·식별자, 생략·왜곡 0)."""
    # 디스크 총량 — block_devices type=disk size_bytes 합 (양 OS 단일 산식, device_filters).
    disk_bytes = disk_total_bytes(detail.block_devices)
    return ServerInventorySnapshot(
        hostname=detail.hostname,
        os_display=_os_display(detail.os_id, detail.os_version, detail.kernel_version),
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
    )


def build_volumes(raws) -> list[VolumeUsage]:
    """ReportMountUsageRaw list -> VolumeUsage (bytes -> GB). 개별 보고서 마운트별 스토리지."""
    return [VolumeUsage(mount=r.mountpoint, total_gb=bytes_to_gb(r.total_bytes), used_pct=r.used_pct) for r in raws]


def build_memory_breakdown(raw) -> MemoryBreakdown:
    return MemoryBreakdown(
        used_pct=raw.used_pct,
        available_pct=raw.available_pct,
        cached_pct=raw.cached_pct,
        buffers_pct=raw.buffers_pct,
    )


def build_cpu_breakdown(raw) -> CpuBreakdown:
    return CpuBreakdown(user_pct=raw.user_pct, system_pct=raw.system_pct, iowait_pct=raw.iowait_pct)


# ─── 1차 매핑 (Outbound → ViewModel) ──────────────────────────────────────


def to_server_list_item(dto: ServerSummary, raw_period=None) -> ServerListItem:
    """ServerSummary -> ServerListItem. raw_period(ReportRowRaw)가 있으면 권장 조치 분류 채움.

    분류 색·라벨은 shared._DONUT_SEGMENT_FROM_REC + _DONUT_SEGMENT_DEFS와 동기화 (P2 단일 진실).
    raw_period=None이면 미분류 — 빈 문자열 (페이지 2+ 등 raws_period 부재).
    """
    # 물리 디스크 총량 — disk_total_bytes 단일 산식(type=disk 합, 목록·상세·보고서 일관).
    _disk_bytes = disk_total_bytes(dto.block_devices)
    storage_total_gb = round(bytes_to_gb(_disk_bytes), 1) if _disk_bytes else None

    # 서비스 뱃지 — 시그니처 워크로드만(SIGNATURE_CATEGORIES, 환경 개요 도넛과 동일 기준). file·mail·infra·remote
    # 등 유틸/관리 서비스는 서버 성격 신호가 약해 목록에서 제외(노이즈 감소). 상세 페이지는 live classify 로 전부.
    known = [
        ServiceItem(unit="", sub="", category=cat, ports=[])
        for cat in dto.service_categories
        if cat in SIGNATURE_CATEGORIES
    ]
    services = known
    show_unknown = False

    rec_label, rec_color, seg_key = "", "", ""
    net_congested = False
    if raw_period is not None:
        # build_resource_stats 단일 진실(net baseline 포함) — 보고서·대시보드와 동일 분류 입력 (#E3).
        # report mapper 지연 import: report.py 가 본 모듈을 import 하므로 모듈 레벨 순환 회피.
        from assessment_engine.web.services.mappers.report import build_resource_stats

        # rollup_host 1회 산출 -> 분류 배지 + orthogonal 네트워크 혼잡 플래그 (classify_host 내부도 rollup_host 경유).
        host = recommendation.rollup_host(build_resource_stats(raw_period))
        rec = recommendation.host_status_to_recommendation(host.host_status)
        net_congested = host.network_congested
        seg_key = _DONUT_SEGMENT_FROM_REC.get(rec, "insufficient_data")
        # 색은 _DONUT_SEGMENT_DEFS, 라벨은 한국어 분류명(recommendation.LABEL_KO 단일 진실) — 서버목록 칼럼 한글 표시.
        for key, _label, color, _desc in _DONUT_SEGMENT_DEFS:
            if key == seg_key:
                rec_color = color
                rec_label = recommendation.LABEL_KO.get(seg_key, seg_key)
                break

    return ServerListItem(
        id=dto.id,
        public_id=dto.public_id,
        hostname=dto.hostname,
        os_id=dto.os_id,
        os_version=dto.os_version,
        cpu_cores=dto.cpu_cores,
        mem_total_gb=bytes_to_gib(dto.mem_total_bytes),
        storage_total_gb=storage_total_gb,
        is_online=False,
        ip_external=dto.ip_external,
        services=services,
        known_services=known,
        show_unknown_badge=show_unknown,
        os_display=_os_display(dto.os_id, dto.os_version, dto.kernel_version),
        recommendation_label=rec_label,
        recommendation_color=rec_color,
        provisioning_class=seg_key,
        network_congested=net_congested,
        os_distro=os_id_to_distro(dto.os_id),
    )


def storage_layers_gb(block_devices: list[dict]) -> tuple[float | None, float | None, float | None]:
    """스토리지 3계층 (GB) 단일 산식 — (배정 블록, 파일시스템, 미할당). block_devices(lsblk) 트리 기준.

    배정 = disk_total_bytes(type=disk size_bytes 합). 파일시스템 = 마운트된 데이터 볼륨 노드 size_bytes 합.
    미할당 = max(0, 배정 - 파일시스템) — 확장 여력 추론(둘 다 있을 때만).
    소비처(시스템 정보·스토리지 탭)는 본 함수만 호출 — raw sum·재필터 금지.
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


def to_server_detail(dto: ServerDetail) -> ServerDetailResponse:
    detail = ServerDetailResponse(
        id=dto.id,
        public_id=dto.public_id,
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

    # 스토리지 3계층 — 시스템 정보와 동일 단일 산식(배정/파일시스템/미할당). fs 층은 아래 fs_total_gb(표시 상세).
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
    )


# ─── enrich_server_detail (idempotent) ────────────────────────────────────
# cache_serializer가 역직렬화 후 재호출 가능 — 두 번 호출해도 결과 동일.
# 입력 detail의 services / listen_ports 만 read-only로 사용, 파생 필드만 갱신.


def enrich_server_detail(detail: ServerDetailResponse) -> ServerDetailResponse:
    services = detail.services or []
    seen_chip_keys: set[str] = set()
    shown_port_numbers: set[int] = set()
    known: list[ServiceItem] = []

    # 카테고리 단위 포트 집계 단일 진실 — listen 소켓을 카테고리로 직접 분류한 결과.
    listen_dicts = [{"proto": lp.proto, "port": lp.port, "comm": lp.comm} for lp in detail.listen_ports]
    listen_by_cat = detect_listen_categories(listen_dicts)
    assigned_cats: set[str] = set()  # 카테고리 listen 포트는 카테고리당 1회만 뱃지에 부여
    seen_single_cats: set[str] = set()  # 런타임 스택(container)은 첫 unit 만 뱃지 (docker+containerd → 1뱃지)

    for svc in services:
        if svc.category == "unknown":
            continue
        if svc.category in SINGLE_INSTANCE_CATEGORIES:
            if svc.category in seen_single_cats:
                continue
            seen_single_cats.add(svc.category)
        ports: list = []
        # comm 으로 unit 에 귀속된 포트 (per-unit 정확 매핑)
        for p in svc.ports:
            key = f"{p.proto}:{p.port}"
            if key not in seen_chip_keys:
                seen_chip_keys.add(key)
                shown_port_numbers.add(p.port)
                ports.append(p)
        # 카테고리 단위 listen 포트 보강 (Q1) — comm 귀속 실패한 워크로드 포트
        # (W3SVC<->System 의 80 등)를 같은 카테고리 뱃지에 붙임. 카테고리당 1회 (중복 회피).
        if svc.category not in assigned_cats:
            assigned_cats.add(svc.category)
            for p in listen_by_cat.get(svc.category, []):
                key = f"{p.proto}:{p.port}"
                if key not in seen_chip_keys:
                    seen_chip_keys.add(key)
                    shown_port_numbers.add(p.port)
                    ports.append(p)
        ports.sort(key=lambda mp: (mp.port, mp.proto))  # P3 — 칩 정렬은 mapper 단일
        known.append(
            ServiceItem(
                unit=svc.unit,
                sub=svc.sub,
                category=svc.category,
                ports=ports,
                display_name=svc.display_name,
            )
        )

    # listen-only 워크로드 보충 (ADR 0032 union) — services 이름이 못 잡았지만 listen 소켓이
    # 증거하는 카테고리를 합성 뱃지로 추가. opaque 한 Windows SCM 이름을 1433/sqlservr 같은
    # 깨끗한 listen 신호로 구제. unit/display_name 없음 (특정 service 에 귀속 불가, T15).
    known_categories = {s.category for s in known}
    for category, ports in listen_by_cat.items():
        if category in known_categories:
            continue
        deduped_ports: list = []
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
    # union(이름 ∪ listen)으로도 아무 카테고리도 못 잡았을 때만 unknown 뱃지.
    detail.show_unknown_badge = detail.services is not None and bool(detail.services) and not known
    detail.key_listen_ports = sorted(
        [lp for lp in detail.listen_ports if lp.is_significant and lp.port not in shown_port_numbers],
        key=lambda lp: (lp.port, lp.proto),
    )

    # 템플릿(P3)이 sort 못 하도록 mapper에서 한 번만 정렬
    detail.sorted_services = sorted(detail.services or [], key=lambda s: s.unit) if detail.services else []
    detail.sorted_listen_ports = sorted(detail.listen_ports, key=lambda lp: (lp.port, lp.proto))

    detail.os_display = _os_display(detail.os_id, detail.os_version, detail.kernel_version)

    cpu_parts = [p for p in [detail.cpu_model, f"{detail.cpu_cores} cores" if detail.cpu_cores else None] if p]
    detail.cpu_display = " ".join(cpu_parts) or "-"

    # disk_total_gb·volume_total_gb·disk_unallocated_gb 는 to_server_detail 에서 storage_layers_gb 단일 산식으로 설정(#C).

    # P3 — count는 mapper에서 한 번만 계산. 템플릿이 `| length` 못 쓰도록.
    detail.services_count = len(detail.services or [])
    detail.listen_ports_count = len(detail.listen_ports)
    detail.disks_count = len(detail.disks)
    detail.volumes_count = len(detail.volumes)

    return detail


# ─── role 추론 — services[].unit → 카테고리 빈도 최다 (export · report · attention 공용) ───


def workload_category_counter(
    services: list[dict] | None,
    listen_ports: list[dict] | None = None,
) -> Counter[str]:
    """호스트 워크로드 카테고리 카운터 — services 이름 분류 ∪ listen 소켓 탐지 (ADR 0032).

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
    services: list[dict] | None,
    listen_ports: list[dict] | None = None,
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


def infer_role(services: list[dict] | None, listen_ports: list[dict] | None = None) -> str:
    """호스트 대표 역할 — `workload_category_counter` 최빈 카테고리. 비면 "unknown"."""
    counter = workload_category_counter(services, listen_ports)
    if not counter:
        return "unknown"
    return counter.most_common(1)[0][0]
