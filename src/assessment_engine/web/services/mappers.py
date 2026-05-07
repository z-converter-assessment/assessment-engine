"""Outbound DTO → ViewModel 변환 — P2 단일 표현 진입점.

원칙:
- raw `list[dict]`(JSONB 컬럼)는 본 모듈에서만 typed ViewModel로 변환. 다른 곳에서 dict 키 매핑 금지.
- 변환은 idempotent해야 한다 (cache_serializer가 역직렬화 후 enrich_server_detail 재호출).
- 임계값(usage 90/75)은 모듈 상단 상수. 동일 의미의 차트 JS 상수와 동기화.
"""
from typing import Literal

from assessment_engine.db.repositories.outbound import (
    CollectionStatus,
    MetricSeries,
    NetworkWithIo,
    ServerSummary,
    ServerDetail,
    StorageWithUsage,
)
from assessment_engine.web.services.device_filters import (
    find_parent_disk,
    is_physical_disk,
    is_virtual_mount,
)
from assessment_engine.web.services.metrics_calculator import compute_net_io
from assessment_engine.web.services.service_classifier import classify, matched_ports
from assessment_engine.web.services.units import bytes_to_gb, kb_to_gb, usage_pct
from assessment_engine.web.view_models import (
    CollectionStatusItem,
    DiskItem,
    ListenPortItem,
    MetricSeriesItem,
    MountUsageItem,
    NetworkDetailResponse,
    ServerDetailResponse,
    ServerListItem,
    ServiceItem,
    StorageDetailResponse,
)


# ─── 임계값 상수 ──────────────────────────────────────────────────────────
# (차트 JS의 USAGE_DANGER_PCT/USAGE_WARN_PCT와 동기화)
_USAGE_DANGER_PCT = 90
_USAGE_WARN_PCT   = 75
# IANA well-known port 상한. listen_port의 well-known 표시 분기에 사용.
_WELL_KNOWN_PORT_MAX = 1024

_Severity = Literal["ok", "warn", "danger"]

_BADGE_CLASS_BY_SEVERITY: dict[_Severity, str] = {
    "ok":     "badge-ok",
    "warn":   "badge-warn",
    "danger": "badge-danger",
}
_BAR_COLOR_BY_SEVERITY: dict[_Severity, str] = {
    "ok":     "#22c55e",
    "warn":   "#f59e0b",
    "danger": "#ef4444",
}


def _usage_severity(pct: float | None) -> _Severity:
    if pct is None or pct < _USAGE_WARN_PCT:
        return "ok"
    if pct < _USAGE_DANGER_PCT:
        return "warn"
    return "danger"


def _usage_badge_class(pct: float | None) -> str:
    return _BADGE_CLASS_BY_SEVERITY[_usage_severity(pct)] if pct is not None else ""


def _usage_bar_color(pct: float | None) -> str:
    return _BAR_COLOR_BY_SEVERITY[_usage_severity(pct)]


# ─── raw dict → typed ViewModel 단일 변환 진입점 ──────────────────────────

def _to_disk_item(d: dict) -> DiskItem | None:
    """inventory.disks의 raw dict → DiskItem. 물리 디스크 아니면 None."""
    name = d.get("name", "")
    if not is_physical_disk(name):
        return None
    return DiskItem(
        name=name,
        size_gb=bytes_to_gb(d.get("size_bytes")),
        type=d.get("type"),
    )


def _to_listen_port_item(p: dict) -> ListenPortItem:
    port = p.get("port", 0)
    return ListenPortItem(
        proto=p.get("proto", ""),
        addr=p.get("addr", ""),
        port=port,
        uid=p.get("uid", 0),
        pid=p.get("pid"),
        comm=p.get("comm"),
        is_well_known=port <= _WELL_KNOWN_PORT_MAX,
    )


def _to_service_item(s: dict, listen_ports: list[dict] | None = None) -> ServiceItem:
    """inventory.services의 raw dict → ServiceItem.

    listen_ports가 주어지면 매핑된 포트를 채움 (detail). 없으면 빈 리스트 (list 화면).
    """
    unit = s.get("unit", "")
    return ServiceItem(
        unit=unit,
        sub=s.get("sub", ""),
        category=classify(unit),
        ports=matched_ports(unit, listen_ports) if listen_ports else [],
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


def _dedup_known(services: list[ServiceItem] | None) -> tuple[list[ServiceItem], bool]:
    """known 카테고리만 dedup된 list + show_unknown_badge boolean.

    show_unknown_badge: services는 있지만 모두 unknown인 경우만 True.
    """
    if services is None:
        return [], False
    known: list[ServiceItem] = []
    seen_categories: set[str] = set()
    for s in services:
        if s.category == "unknown":
            continue
        if s.category not in seen_categories:
            seen_categories.add(s.category)
            known.append(s)
    show_unknown = bool(services) and not known
    return known, show_unknown


def _os_display(os_id: str | None, os_version: str | None) -> str:
    parts = [p for p in [os_id, os_version] if p]
    return " ".join(parts) or "-"


# ─── 1차 매핑 (Outbound → ViewModel) ──────────────────────────────────────

def to_server_list_item(dto: ServerSummary) -> ServerListItem:
    physical = [d for d in dto.disks if is_physical_disk(d.get("name", ""))]
    raw_total = sum(bytes_to_gb(d.get("size_bytes")) or 0.0 for d in physical)
    storage_total_gb = round(raw_total, 1) if physical else None

    services = _services_or_none(dto.services, listen_ports=None)
    known, show_unknown = _dedup_known(services)

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
        os_display=_os_display(dto.os_id, dto.os_version),
    )


def to_server_detail(dto: ServerDetail) -> ServerDetailResponse:
    detail = ServerDetailResponse(
        id=dto.id,
        public_id=dto.public_id,
        machine_id=dto.machine_id,
        hostname=dto.hostname,
        agent_version=dto.agent_version,
        os_id=dto.os_id,
        os_version=dto.os_version,
        os_codename=dto.os_codename,
        kernel_version=dto.kernel_version,
        cpu_cores=dto.cpu_cores,
        cpu_model=dto.cpu_model,
        mem_total_gb=kb_to_gb(dto.mem_total_kb),
        swap_total_gb=kb_to_gb(dto.swap_total_kb),
        boot_time=dto.boot_time,
        ip_internal=dto.ip_internal,
        ip_external=dto.ip_external,
        disks=[item for d in dto.disks if (item := _to_disk_item(d)) is not None],
        services=_services_or_none(dto.services, listen_ports=dto.listen_ports),
        listen_ports=[_to_listen_port_item(p) for p in dto.listen_ports],
        last_seen_at=dto.last_seen_at,
    )
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
        if is_virtual_mount(fstype, path):
            continue
        usage = usage_by_mount.get(path)
        mounts.append(_build_mount_item(
            mount=path,
            fstype=fstype,
            total_bytes=inv.get("total_bytes"),
            avail_bytes=usage.avail_bytes if usage else None,
            mount_major=inv.get("major"),
            mount_minor=inv.get("minor"),
            disks=physical_disks,
        ))

    # inventory에 없지만 시계열에 있는 mount (mount_usage 시계열엔 major/minor 없음 → device_name 매핑 불가)
    for path, usage in usage_by_mount.items():
        if path in seen or is_virtual_mount(None, path):
            continue
        mounts.append(_build_mount_item(
            mount=path, fstype=None,
            total_bytes=usage.total_bytes,
            avail_bytes=usage.avail_bytes,
            mount_major=None,
            mount_minor=None,
            disks=physical_disks,
        ))

    collected_ats = [u.collected_at for u in dto.mount_usage if u.collected_at is not None]
    snapshot_at = max(collected_ats) if collected_ats else None

    return StorageDetailResponse(
        server_id=dto.server_id,
        public_id=dto.public_id,
        hostname=dto.hostname,
        disks=[item for d in physical_disks if (item := _to_disk_item(d)) is not None],
        mounts=sorted(mounts, key=lambda m: m.mount),
        snapshot_at=snapshot_at,
        inventory_at=dto.inventory_at,
    )


def _build_mount_item(
    mount: str,
    fstype: str | None,
    total_bytes: int | None,
    avail_bytes: int | None,
    mount_major: int | None = None,
    mount_minor: int | None = None,
    disks: list[dict] | None = None,
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
        bar_color=_usage_bar_color(pct),
        device_name=find_parent_disk(mount_major, mount_minor, disks or []),
    )


def to_network_detail(dto: NetworkWithIo) -> NetworkDetailResponse:
    collected_ats = [r.collected_at for r in dto.net_io]
    return NetworkDetailResponse(
        server_id=dto.server_id,
        public_id=dto.public_id,
        hostname=dto.hostname,
        ip_internal=dto.ip_internal,
        ip_external=dto.ip_external,
        interfaces=compute_net_io(dto.net_io),
        inventory_at=dto.inventory_at,
        snapshot_at=max(collected_ats) if collected_ats else None,
    )


def to_collection_status_item(dto: CollectionStatus, is_online: bool) -> CollectionStatusItem:
    return CollectionStatusItem(
        last_metric_at=dto.last_metric_at,
        last_inventory_at=dto.last_inventory_at,
        is_online=is_online,
    )


def to_metric_series_item(dto: MetricSeries) -> MetricSeriesItem:
    return MetricSeriesItem(
        collected_at=dto.collected_at,
        value=dto.value,
        dimension=dto.dimension,
    )


# ─── enrich_server_detail (idempotent) ────────────────────────────────────
# cache_serializer가 역직렬화 후 재호출 가능 — 두 번 호출해도 결과 동일.
# 입력 detail의 services / listen_ports 만 read-only로 사용, 파생 필드만 갱신.

def enrich_server_detail(detail: ServerDetailResponse) -> ServerDetailResponse:
    services = detail.services or []
    seen_chip_keys: set[str] = set()
    shown_port_numbers: set[int] = set()
    known: list[ServiceItem] = []

    for svc in services:
        if svc.category == "unknown":
            continue
        deduped: list = []
        for p in svc.ports:
            key = f"{p.proto}:{p.port}"
            if key not in seen_chip_keys:
                seen_chip_keys.add(key)
                shown_port_numbers.add(p.port)
                deduped.append(p)
        known.append(ServiceItem(
            unit=svc.unit, sub=svc.sub, category=svc.category,
            ports=deduped, display_name=svc.display_name,
        ))

    detail.known_services = known
    detail.show_unknown_badge = (
        detail.services is not None and bool(detail.services) and not known
    )
    detail.key_listen_ports = sorted(
        [lp for lp in detail.listen_ports if lp.is_well_known and lp.port not in shown_port_numbers],
        key=lambda lp: (lp.port, lp.proto),
    )

    # 템플릿(P3)이 sort 못 하도록 mapper에서 한 번만 정렬
    detail.sorted_services = sorted(detail.services or [], key=lambda s: s.unit) if detail.services else []
    detail.sorted_listen_ports = sorted(detail.listen_ports, key=lambda lp: (lp.port, lp.proto))

    detail.os_display = _os_display(detail.os_id, detail.os_version)

    cpu_parts = [p for p in [detail.cpu_model, f"{detail.cpu_cores} cores" if detail.cpu_cores else None] if p]
    detail.cpu_display = " ".join(cpu_parts) or "-"

    detail.disk_total_gb = round(sum(d.size_gb or 0.0 for d in detail.disks), 1) if detail.disks else None

    return detail