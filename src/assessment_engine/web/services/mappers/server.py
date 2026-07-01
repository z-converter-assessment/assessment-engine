"""서버 표시 mapper — ServerSummary/ServerDetail/StorageWithUsage/NetworkWithIo → ViewModel (P2).

본 sub-module 책임: 인벤토리·서비스·디스크·마운트·네트워크 표시 변환 + role 추론.
다른 sub-module 이 import 하는 항목: `infer_role`, `WELL_KNOWN_PORT_MAX`, `enrich_server_detail`.
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
    SINGLE_INSTANCE_CATEGORIES,
    classify,
    detect_listen_categories,
    matched_ports,
)
from assessment_engine.web.services.device_filters import (
    disk_total_bytes,
    is_data_volume,
    is_physical_disk,
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
from assessment_engine.web.services.unit_converter import bytes_to_gb, kb_to_gb, usage_pct
from assessment_engine.web.view_models.environment_report import (
    CpuBreakdown,
    MemoryBreakdown,
    ServerInventory,
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

# IANA well-known port 상한. listen_port의 well-known 표시 분기에 사용.
# cache_serializer 가 본 상수를 import 해 역직렬화 후 enrich 재호출 시 동일 분기 적용.
WELL_KNOWN_PORT_MAX = 1024

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


def _to_ip_addrs(interfaces: list[dict]) -> list[IpAddr]:
    """interface dict 목록 → IpAddr(value=CIDR, is_ipv4). IPv4 우선 정렬(안정), loopback 제외.

    IPv4 는 실제 접속·식별 주력이라 상단·진하게 표시, IPv6(ULA/link-local)는 보조(연하게).
    """
    items: list[IpAddr] = []
    for i in interfaces or []:
        if i.get("kind") == "loopback":
            continue  # 표시 무의미
        addr = i.get("address", "")
        prefix = i.get("prefix")
        value = f"{addr}/{prefix}" if prefix is not None else addr
        items.append(IpAddr(value=value, is_ipv4=i.get("family") == "ipv4"))
    return sorted(items, key=lambda x: not x.is_ipv4)


def _to_volumes(mounts: list[dict]) -> list[VolumeItem]:
    """inventory.mounts → VolumeItem(파일시스템) 목록 (가상 마운트 제외, mount ASC).

    물리 디스크(disks)와 별개 축 — 양 OS 일관 표시 (fstype 명시).
    Windows 는 disks 미발행이라 본 항목이 유일한 스토리지 정보.
    """
    volumes: list[VolumeItem] = []
    for m in mounts:
        path = m.get("mount", "")
        fstype = m.get("fstype")
        if not is_data_volume(path, m.get("major"), fstype):
            continue
        volumes.append(VolumeItem(mount=path, fstype=fstype, total_gb=bytes_to_gb(m.get("total_bytes"))))
    return sorted(volumes, key=lambda v: v.mount)


def _to_disk_item(d: dict) -> DiskItem | None:
    """물리 디스크 아니면 None."""
    name = d.get("name", "")
    if not is_physical_disk(name):
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
        is_well_known=port <= WELL_KNOWN_PORT_MAX,
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


def build_server_inventory(detail, is_online: bool) -> ServerInventory:
    """ServerDetail -> 개별 보고서 인벤토리 (충실 표시 — 전체 IP(IPv4/IPv6)·하드웨어·식별자, 생략·왜곡 0)."""
    # 디스크 총량 — 물리 disks 우선, 비면(Windows 등 물리 미발행) 파일시스템 mounts fallback.
    # device_filters.disk_total_bytes 단일 산식 (환경·세부 목록 보고서와 동일, Windows 포함 일관).
    disk_bytes = disk_total_bytes(detail.disks or [], detail.mounts or [])
    return ServerInventory(
        hostname=detail.hostname,
        os_display=_os_display(detail.os_id, detail.os_version, detail.kernel_version),
        os_codename=detail.os_codename,
        kernel_version=detail.kernel_version,
        cpu_model=detail.cpu_model,
        cpu_cores=detail.cpu_cores,
        mem_total_gb=kb_to_gb(detail.mem_total_kb),
        swap_total_gb=kb_to_gb(detail.swap_total_kb),
        disk_total_gb=int(bytes_to_gb(disk_bytes) or 0),
        ip_internal=_to_ip_addrs(detail.interfaces),
        ip_external=_to_ip_addrs(detail.ip_external) if detail.ip_external else [],
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
    return [VolumeUsage(mount=r.mount, total_gb=bytes_to_gb(r.total_bytes), used_pct=r.used_pct) for r in raws]


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
    physical = [d for d in dto.disks if is_physical_disk(d.get("name", ""))]
    raw_total = sum(bytes_to_gb(d.get("size_bytes")) or 0.0 for d in physical)
    storage_total_gb = round(raw_total, 1) if physical else None

    # 서비스 뱃지 — ingest 사전계산 저장값(service_classifier 단일 진실, #E7). 이름·comm·포트 어느 신호로
    # 식별되든 상세·리포트·필터와 동일 집합. services JSONB 행별 재분류 제거(목록 경량).
    known = [ServiceItem(unit="", sub="", category=cat, ports=[]) for cat in dto.service_categories]
    services = known
    show_unknown = False

    rec_label, rec_color, seg_key = "", "", ""
    if raw_period is not None:
        # build_resource_stats 단일 진실(net baseline 포함) — 보고서·대시보드와 동일 분류 입력 (#E3).
        # report mapper 지연 import: report.py 가 본 모듈을 import 하므로 모듈 레벨 순환 회피.
        from assessment_engine.web.services.mappers.report import build_resource_stats

        rec = recommendation.classify(build_resource_stats(raw_period))
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
        mem_total_gb=kb_to_gb(dto.mem_total_kb),
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
        os_distro=os_id_to_distro(dto.os_id),
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
        mem_total_gb=kb_to_gb(dto.mem_total_kb),
        swap_total_gb=kb_to_gb(dto.swap_total_kb),
        boot_time=dto.boot_time,
        agent_started_at=dto.agent_started_at,
        ip_internal=_to_ip_addrs(dto.interfaces),
        ip_external=_to_ip_addrs(dto.ip_external) if dto.ip_external else None,
        disks=[item for d in dto.disks if (item := _to_disk_item(d)) is not None],
        services=_services_or_none(dto.services, listen_ports=dto.listen_ports),
        listen_ports=[_to_listen_port_item(p) for p in dto.listen_ports],
        last_seen_at=dto.last_seen_at,
    )
    # 파일시스템 항목 — inventory.mounts(가상 마운트 제외). 물리 디스크(disks)와 별개 축, 양 OS 일관(fstype 명시).
    detail.volumes = _to_volumes(dto.mounts)
    return enrich_server_detail(detail)


def to_storage_detail(dto: StorageWithUsage) -> StorageDetailResponse:
    usage_by_mount = {u.mount: u for u in dto.mount_usage}
    physical_disks = [d for d in dto.disks if is_physical_disk(d.get("name", ""))]

    mounts: list[MountUsageItem] = []
    seen: set[str] = set()

    for inv in dto.inventory_mounts:
        path = inv.get("mount", "")
        fstype = inv.get("fstype")
        seen.add(path)
        if not is_data_volume(path, inv.get("major"), fstype):
            continue
        usage = usage_by_mount.get(path)
        mounts.append(
            _build_mount_item(
                mount=path,
                fstype=fstype,
                total_bytes=inv.get("total_bytes"),
                avail_bytes=usage.avail_bytes if usage else None,
            )
        )

    # inventory에 없지만 시계열에 있는 mount (mount_usage 시계열 전용)
    for path, usage in usage_by_mount.items():
        if path in seen or not is_data_volume(path, getattr(usage, "major", None)):
            continue
        mounts.append(
            _build_mount_item(
                mount=path,
                fstype=None,
                total_bytes=usage.total_bytes,
                avail_bytes=usage.avail_bytes,
            )
        )

    collected_ats = [u.collected_at for u in dto.mount_usage if u.collected_at is not None]
    snapshot_at = max(collected_ats) if collected_ats else None

    return StorageDetailResponse(
        server_id=dto.server_id,
        public_id=dto.public_id,
        hostname=dto.hostname,
        disks=[item for d in physical_disks if (item := _to_disk_item(d)) is not None],
        mounts=sorted(mounts, key=lambda m: m.mount),
        fs_total_gb=sum((m.total_gb for m in mounts if m.total_gb is not None), 0.0) if mounts else None,
        snapshot_at=snapshot_at,
        inventory_at=dto.inventory_at,
    )


def _build_mount_item(
    mount: str,
    fstype: str | None,
    total_bytes: int | None,
    avail_bytes: int | None,
) -> MountUsageItem:
    used_bytes = (total_bytes - avail_bytes) if (total_bytes and avail_bytes is not None) else None
    pct = usage_pct(used_bytes, total_bytes)
    return MountUsageItem(
        mount=mount,
        fstype=fstype,
        total_gb=bytes_to_gb(total_bytes),
        used_gb=bytes_to_gb(used_bytes),
        avail_gb=bytes_to_gb(avail_bytes),
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
        ip_internal=_to_ip_addrs(dto.interfaces),
        ip_external=_to_ip_addrs(dto.ip_external) if dto.ip_external else None,
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
        [lp for lp in detail.listen_ports if lp.is_well_known and lp.port not in shown_port_numbers],
        key=lambda lp: (lp.port, lp.proto),
    )

    # 템플릿(P3)이 sort 못 하도록 mapper에서 한 번만 정렬
    detail.sorted_services = sorted(detail.services or [], key=lambda s: s.unit) if detail.services else []
    detail.sorted_listen_ports = sorted(detail.listen_ports, key=lambda lp: (lp.port, lp.proto))

    detail.os_display = _os_display(detail.os_id, detail.os_version, detail.kernel_version)

    cpu_parts = [p for p in [detail.cpu_model, f"{detail.cpu_cores} cores" if detail.cpu_cores else None] if p]
    detail.cpu_display = " ".join(cpu_parts) or "-"

    detail.disk_total_gb = round(sum(d.size_gb or 0.0 for d in detail.disks), 1) if detail.disks else None
    detail.volume_total_gb = round(sum(v.total_gb or 0.0 for v in detail.volumes), 1) if detail.volumes else None

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
    counter: Counter[str] = Counter()
    for s in services or []:
        unit = s.get("unit") if isinstance(s, dict) else None
        if not unit:
            continue
        cat = classify(unit, listen_ports, s.get("pid"))
        if cat == "unknown":
            continue
        # 런타임 스택(container)은 구성 요소가 여러 서비스로 떠도 호스트당 1 (docker+containerd → 1).
        counter[cat] = 1 if cat in SINGLE_INSTANCE_CATEGORIES else counter[cat] + 1
    name_cats = set(counter)
    for cat in detect_listen_categories(listen_ports or []):
        if cat not in name_cats:
            counter[cat] = 1
    return counter


def infer_role(services: list[dict] | None, listen_ports: list[dict] | None = None) -> str:
    """호스트 대표 역할 — `workload_category_counter` 최빈 카테고리. 비면 "unknown"."""
    counter = workload_category_counter(services, listen_ports)
    if not counter:
        return "unknown"
    return counter.most_common(1)[0][0]
