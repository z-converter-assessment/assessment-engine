"""Outbound DTO → ViewModel 변환 — P2 단일 표현 진입점.

원칙:
- raw `list[dict]`(JSONB 컬럼)는 본 모듈에서만 typed ViewModel로 변환. 다른 곳에서 dict 키 매핑 금지.
- 변환은 idempotent해야 한다 (cache_serializer가 역직렬화 후 enrich_server_detail 재호출).
- 임계값(usage 90/75)은 모듈 상단 상수. 동일 의미의 차트 JS 상수와 동기화.
"""
from datetime import datetime
from typing import Literal

from assessment_engine.db.repositories.outbound import (
    CollectionStatus,
    DiskUsageWarningRaw,
    InventoryExportEntry,
    MetricGapWarningRaw,
    MetricSeries,
    NetworkWithIo,
    ReportRowRaw,
    ServerDetail,
    ServerSummary,
    StorageWithUsage,
)
from assessment_engine.web.services import recommendation
from assessment_engine.web.services.device_filters import (
    find_parent_disk,
    is_physical_disk,
    is_virtual_mount,
)
from assessment_engine.web.services.metrics_calculator import compute_net_io
from assessment_engine.web.services.service_classifier import classify, matched_ports
from assessment_engine.web.services.units import bytes_to_gb, kb_to_gb, usage_pct
from assessment_engine.web.view_models import (
    AttentionRow,
    CollectionStatusItem,
    DiskItem,
    ListenPortItem,
    MetricSeriesItem,
    MountUsageItem,
    NetworkDetailResponse,
    ReportRowItem,
    ReportTotals,
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
# attention 신호 — metric 갭이 30분 이상이면 위험 색 (5~30분은 경고).
_GAP_DANGER_MINUTES = 30
# disk_warnings stale 임계 — last_metric_at이 24h 이상 안 갱신된 mount는 meta에 "마지막 수집 ..." 추가 표시.
# 7d cutoff(SQL) 안에서도 1d 이상 stale은 운영자가 인지해야 함.
_DISK_STALE_HOURS = 24

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

def to_server_list_item(dto: ServerSummary, raw_period=None) -> ServerListItem:
    """ServerSummary -> ServerListItem. raw_period(ReportRowRaw)가 있으면 권장 조치 분류 채움.

    분류 색·라벨은 _DONUT_SEGMENT_FROM_REC + _DONUT_SEGMENT_DEFS와 동기화 (P2 단일 진실).
    raw_period=None이면 미분류 — 빈 문자열 (페이지 2+ 등 raws_period 부재).
    """
    physical = [d for d in dto.disks if is_physical_disk(d.get("name", ""))]
    raw_total = sum(bytes_to_gb(d.get("size_bytes")) or 0.0 for d in physical)
    storage_total_gb = round(raw_total, 1) if physical else None

    services = _services_or_none(dto.services, listen_ports=None)
    known, show_unknown = _dedup_known(services)

    rec_label, rec_color = "", ""
    if raw_period is not None:
        rec = recommendation.classify(recommendation.ResourceStats(
            cpu_p95_pct=raw_period.cpu_p95_pct, cpu_peak_pct=raw_period.cpu_peak_pct,
            mem_p95_pct=raw_period.mem_p95_pct, swap_used=raw_period.swap_used, net_avg_kbps=None,
        ))
        seg_key = _DONUT_SEGMENT_FROM_REC.get(rec, "normal")
        # 색은 풀네임 def에서 추출, 라벨은 약어 dict — 셀 좁은 칸 대응.
        for key, _label, color in _DONUT_SEGMENT_DEFS:
            if key == seg_key:
                rec_color = color
                break
        rec_label = _DONUT_SEGMENT_SHORT_LABEL.get(seg_key, "")

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
        recommendation_label=rec_label,
        recommendation_color=rec_color,
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

    # P3 — count는 mapper에서 한 번만 계산. 템플릿이 `| length` 못 쓰도록.
    detail.services_count = len(detail.services or [])
    detail.listen_ports_count = len(detail.listen_ports)
    detail.disks_count = len(detail.disks)

    return detail


# ─── Inventory JSON Export ─────────────────


def infer_role(services: list[dict] | None) -> str:
    """services[].unit를 service_classifier로 분류 → 가장 빈도 높은 카테고리.

    "unknown"은 결정에서 제외. 모두 unknown이면 "unknown" 반환.
    """
    if not services:
        return "unknown"
    from collections import Counter
    counter: Counter[str] = Counter()
    for s in services:
        unit = s.get("unit") if isinstance(s, dict) else None
        if not unit:
            continue
        cat = classify(unit)
        if cat != "unknown":
            counter[cat] += 1
    if not counter:
        return "unknown"
    return counter.most_common(1)[0][0]


def _split_disks(disks: list[dict], mounts: list[dict]) -> tuple[int | None, list[dict]]:
    """disks 중 가장 큰 1개를 boot, 나머지를 additional로 분리.

    additional의 mount_point는 find_parent_disk(mount.major/minor -> disk) 역방향 매칭.
    fstype은 동일 mount의 fstype 필드. iops_baseline은 mapper 호출자가 별도 주입.
    """
    if not disks:
        return (None, [])
    sorted_disks = sorted(disks, key=lambda d: d.get("size_bytes") or 0, reverse=True)
    boot = sorted_disks[0]
    boot_gb = (boot["size_bytes"] // 10**9) if boot.get("size_bytes") else None
    additional: list[dict] = []
    for d in sorted_disks[1:]:
        mount_point = None
        fstype = None
        for m in mounts or []:
            if find_parent_disk(m.get("major"), m.get("minor"), [d]) == d.get("name"):
                mount_point = m.get("mount")
                fstype = m.get("fstype")
                break
        size_gb = (d["size_bytes"] // 10**9) if d.get("size_bytes") else None
        additional.append({
            "mount_point": mount_point,
            "size_gb": size_gb,
            "fstype": fstype,
        })
    return (boot_gb, additional)


def _network_addresses(ip_internal: list[str] | None, ip_external: list[str] | None) -> list[dict]:
    """v4·v6 family 자동 분류 — `:` 포함 시 v6, 아니면 v4. scope는 input 파라미터로 결정."""
    out: list[dict] = []
    for ip in ip_internal or []:
        out.append({"scope": "internal", "family": "v6" if ":" in ip else "v4", "address": ip})
    for ip in ip_external or []:
        out.append({"scope": "external", "family": "v6" if ":" in ip else "v4", "address": ip})
    return out


def _services_for_export(services: list[dict] | None, listen_ports: list[dict] | None = None) -> list[dict]:
    """services[] + listen_ports[] -> [{category, unit, listeners}] for SG 자동화 입력.

    `unknown` 카테고리는 제외 — 보안그룹 룰 자동 생성에 의미 없음.
    listeners: ports 매핑별 실제 (proto, address) 정보 — listen_ports inventory와 매칭.
    매칭 실패 시 service_classifier의 `_SERVICE_PORTS` 폴백 (proto=tcp, address=0.0.0.0 가정).
    """
    if not services:
        return []
    from assessment_engine.web.services.service_classifier import _SERVICE_PORTS  # noqa: PLC0415
    # listen_ports를 port → list of (proto, addr) 인덱스 (서비스가 여러 인터페이스 listen할 수 있음)
    by_port: dict[int, list[dict]] = {}
    for lp in listen_ports or []:
        if not isinstance(lp, dict):
            continue
        port = lp.get("port")
        if port is None:
            continue
        by_port.setdefault(port, []).append({
            "proto": lp.get("proto") or "tcp",
            "address": lp.get("addr") or "0.0.0.0",
        })

    out: list[dict] = []
    for s in services:
        unit = s.get("unit") if isinstance(s, dict) else None
        if not unit:
            continue
        cat = classify(unit)
        if cat == "unknown":
            continue
        # _SERVICE_PORTS는 unit normalized 이름(`nginx`/`postgresql` 등) 키 — classifier와 동일 normalize 의무.
        unit_normalized = unit.lower().removesuffix(".service")
        port_list: list[int] = []
        for keyword, ports in _SERVICE_PORTS.items():
            if keyword in unit_normalized:
                port_list = ports
                break
        listeners: list[dict] = []
        for port in port_list:
            matched = by_port.get(port, [])
            if matched:
                for m in matched:
                    listeners.append({"port": port, "proto": m["proto"], "address": m["address"]})
            else:
                # 폴백 — 카테고리 표준 포트만 명시 (proto/address는 자동화 도구가 기본 가정 사용)
                listeners.append({"port": port, "proto": "tcp", "address": "0.0.0.0"})
        out.append({"category": cat, "unit": unit, "listeners": listeners})
    return out


def to_disk_warning_item(raw: DiskUsageWarningRaw, now: datetime) -> AttentionRow:
    """DiskUsageWarningRaw → AttentionRow (P2: 단위 변환·badge 분류·표시 string·stale 분기 단일 변환).

    threshold(85%)는 repo가 이미 거름 — mapper는 단위 + badge + 표시 string만. SQL이 total_bytes>0를 거름.
    last_metric_at이 _DISK_STALE_HOURS 이상 안 갱신된 mount는 meta_at 채워서 "마지막 수집" 표시 활성.
    """
    used_pct = (1 - raw.avail_bytes / raw.total_bytes) * 100
    free_gb = raw.avail_bytes / 1024 ** 3
    total_gb = raw.total_bytes / 1024 ** 3
    badge = "rec-under_provisioned" if used_pct >= _USAGE_DANGER_PCT else "rec-right_size"
    is_stale = (now - raw.last_metric_at).total_seconds() / 3600 >= _DISK_STALE_HOURS
    meta_text = f"잔여 {free_gb:.1f} / {total_gb:.1f} GB"
    if is_stale:
        meta_text += " · 마지막 수집 "
    return AttentionRow(
        badge_class=badge,
        badge_text=f"{used_pct:.0f}%",
        link_href=f"/servers/{raw.public_id}/storage",
        link_text=raw.hostname,
        mount_path=raw.mount,
        meta_text=meta_text,
        meta_at=raw.last_metric_at if is_stale else None,
    )


def to_gap_warning_item(raw: MetricGapWarningRaw, now: datetime) -> AttentionRow:
    """MetricGapWarningRaw → AttentionRow (P2: gap_minutes·badge·표시 string 단일 변환).

    last_metric_at은 KST 표시 위해 meta_at으로 그대로 전달 (KST 변환은 template kst 필터 #F2).
    mount path 없는 카테고리 — mount_path=None.
    """
    gap_min = int((now - raw.last_metric_at).total_seconds() // 60)
    badge = "rec-under_provisioned" if gap_min >= _GAP_DANGER_MINUTES else "rec-right_size"
    return AttentionRow(
        badge_class=badge,
        badge_text=f"{gap_min}분",
        link_href=f"/servers/{raw.public_id}",
        link_text=raw.hostname,
        meta_text="마지막 수집 ",
        meta_at=raw.last_metric_at,
    )


def build_report_summary_bullets(rows: list, raws: list | None = None) -> list[str]:
    """양식 A 자동 분석 요약 문장 생성 — 정량 신호 기반 정성 요약 (P2).

    호출자는 ReportRowItem list + 선택적 ReportRowRaw list 전달.
    raws가 있으면 OS EOL 신호 생성 (raws.os_id/os_version 사용).
    빈 리스트면 ["대상 서버 없음"] 반환.
    """
    if not rows:
        return ["대상 서버 없음."]

    bullets: list[str] = []
    n_high = sum(1 for r in rows if r.risk_level == "high")
    n_attention = sum(1 for r in rows if r.risk_level == "attention")

    if n_high:
        hosts = [r.hostname for r in rows if r.risk_level == "high"][:3]
        suffix = " 외" if n_high > 3 else ""
        bullets.append(f"고위험 {n_high}대 ({', '.join(hosts)}{suffix}) — 자원 부족 신호. 즉시 instance type 상향 검토.")
    if n_attention:
        hosts = [r.hostname for r in rows if r.risk_level == "attention"][:3]
        suffix = " 외" if n_attention > 3 else ""
        bullets.append(f"주의 필요 {n_attention}대 ({', '.join(hosts)}{suffix}) — 저사용·과다 프로비저닝. 다운사이즈 검토 후보.")

    # 역할별 평균 CPU·MEM — 가장 자원 집약 역할 1개 식별
    from collections import defaultdict  # noqa: PLC0415
    role_cpu: defaultdict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.cpu_p95_pct is not None:
            role_cpu[r.role].append(r.cpu_p95_pct)
    if role_cpu:
        top_cpu_role = max(role_cpu, key=lambda k: sum(role_cpu[k]) / len(role_cpu[k]))
        top_cpu_avg = sum(role_cpu[top_cpu_role]) / len(role_cpu[top_cpu_role])
        if top_cpu_avg >= 70.0:
            bullets.append(f"{top_cpu_role} 계열 서버의 평균 CPU p95가 {top_cpu_avg:.0f}%로 높게 관찰됨.")

    # I/O wait 신호 — p95 >= 20% 서버 카운트
    n_iowait = sum(1 for r in rows if r.iowait_p95_pct is not None and r.iowait_p95_pct >= 20.0)
    if n_iowait:
        hosts = [r.hostname for r in rows if r.iowait_p95_pct is not None and r.iowait_p95_pct >= 20.0][:3]
        suffix = " 외" if n_iowait > 3 else ""
        bullets.append(f"I/O wait p95 20%+ {n_iowait}대 ({', '.join(hosts)}{suffix}) — 디스크 병목. SSD/iops 상향 검토.")

    # Mount 임박 — 30일 안 채워질 마운트가 있는 서버 카운트
    n_mount = sum(
        1 for r in rows
        if r.worst_mount_days_until_full is not None and r.worst_mount_days_until_full <= 30
    )
    if n_mount:
        hosts = []
        for r in rows:
            if r.worst_mount_days_until_full is not None and r.worst_mount_days_until_full <= 30:
                hosts.append(f"{r.hostname}({r.worst_mount} {r.worst_mount_days_until_full}일)")
                if len(hosts) >= 3:
                    break
        suffix = " 외" if n_mount > 3 else ""
        bullets.append(f"디스크 채움 임박 {n_mount}대 ({', '.join(hosts)}{suffix}) — 디스크 증설 또는 정리 검토.")

    # 재부팅 빈번 — period 안 3회 이상
    n_reboot = sum(1 for r in rows if r.reboot_count >= 3)
    if n_reboot:
        hosts = [f"{r.hostname}({r.reboot_count}회)" for r in rows if r.reboot_count >= 3][:3]
        suffix = " 외" if n_reboot > 3 else ""
        bullets.append(f"재부팅 빈번 {n_reboot}대 ({', '.join(hosts)}{suffix}) — 안정성 점검 필요.")

    # Saturation — load_15m_max / cpu_cores >= 1.0 (saturated)
    n_sat = sum(1 for r in rows if r.saturation_ratio is not None and r.saturation_ratio >= 1.0)
    if n_sat:
        hosts = [f"{r.hostname}({r.saturation_ratio:.1f})" for r in rows
                 if r.saturation_ratio is not None and r.saturation_ratio >= 1.0][:3]
        suffix = " 외" if n_sat > 3 else ""
        bullets.append(f"Saturation {n_sat}대 ({', '.join(hosts)}{suffix}) — load가 cpu_cores 초과. 처리 한계 신호.")

    # 변동성 큼 — cpu peak/p95 >= 1.5
    n_var = sum(1 for r in rows if r.cpu_variance_ratio is not None and r.cpu_variance_ratio >= 1.5)
    if n_var:
        hosts = [r.hostname for r in rows
                 if r.cpu_variance_ratio is not None and r.cpu_variance_ratio >= 1.5][:3]
        suffix = " 외" if n_var > 3 else ""
        bullets.append(f"CPU 부하 변동 큼 {n_var}대 ({', '.join(hosts)}{suffix}) — 일시 spike 빈번. 평균보다 peak 기준 sizing 권장.")

    # OS EOL 신호 — raws 있을 때만
    if raws:
        eol_hosts: list[str] = []
        for r in raws:
            # 버전 prefix 매칭 (예: ubuntu 18.04 -> ("ubuntu", "18"))
            for (eol_os, eol_ver), date in _OS_EOL.items():
                if r.os_id == eol_os and (r.os_version or "").startswith(eol_ver):
                    eol_hosts.append(f"{r.hostname}({r.os_id} {r.os_version}, EOL {date})")
                    break
        if eol_hosts:
            shown = eol_hosts[:3]
            suffix = " 외" if len(eol_hosts) > 3 else ""
            bullets.append(f"OS EOL {len(eol_hosts)}대 ({', '.join(shown)}{suffix}) — 마이그레이션 전 OS 업그레이드 검토.")

    if not bullets:
        bullets.append("전체 서버가 정상 범위. 추가 조치 불필요.")
    return bullets


_RISK_FROM_RECOMMENDATION: dict[str, tuple[str, str, str]] = {
    # USE Method 분류 -> (risk_level, 한글 라벨, badge CSS 클래스)
    # 옵션 B 매핑 — 양식 A(고객용) KPI 3단계 압축:
    #   under_provisioned → 고위험 (자원 부족, 즉시 조치)
    #   shutdown·idle·over_provisioned → 주의 (저사용·과다 — 운영자 점검)
    #   optimal·insufficient_data → 정상 (또는 데이터 부족)
    "under_provisioned":  ("high",      "고위험",   "rec-under_provisioned"),
    "shutdown":           ("attention", "주의 필요", "rec-over_provisioned"),
    "idle":               ("attention", "주의 필요", "rec-over_provisioned"),
    "over_provisioned":   ("attention", "주의 필요", "rec-over_provisioned"),
    "optimal":            ("normal",    "정상",     "rec-right_size"),
    "insufficient_data":  ("normal",    "정상",     "rec-right_size"),
}


def _build_diagnosis(raw: ReportRowRaw, saturation: float | None,
                     cpu_variance: float | None, mem_variance: float | None) -> str:
    """saturation·variance·iowait·disk·swap·mem·cpu 종합 자동 진단 — 양식 B "판단" 컬럼.

    우선순위 (가장 시급한 신호 1개 선택):
    1. swap_used → "메모리 부족 (스왑 발생)" — paging 활성, 1차 강신호
    2. iowait_p95 >= 20% → "디스크 I/O 병목"
    3. saturation >= 1.0 → "CPU saturation (LOAD > cores)"
    4. mem_p95 >= 80% → "메모리 압박"
    5. cpu_p95 >= 70% → "CPU 압박"
    6. cpu_variance >= 1.5 또는 mem_variance >= 1.5 → "변동성 큼 (burst)"
    7. cpu_p95 <= 3% → "거의 미사용"
    8. cpu_p95 <= 30% and mem_p95 <= 50% → "여유 있음 (축소 검토)"
    9. 그 외 → "정상"
    """
    if raw.swap_used:
        return "메모리 부족 (스왑 발생)"
    if raw.iowait_p95_pct is not None and raw.iowait_p95_pct >= 20:
        return "디스크 I/O 병목"
    if saturation is not None and saturation >= 1.0:
        return "CPU saturation"
    if raw.mem_p95_pct is not None and raw.mem_p95_pct >= recommendation.MEM_UPSIZE_P95_PCT:
        return "메모리 압박"
    if raw.cpu_p95_pct is not None and raw.cpu_p95_pct >= recommendation.CPU_UPSIZE_P95_PCT:
        return "CPU 압박"
    if (cpu_variance is not None and cpu_variance >= 1.5) or (mem_variance is not None and mem_variance >= 1.5):
        return "변동성 큼 (burst)"
    if raw.cpu_p95_pct is not None and raw.cpu_p95_pct <= recommendation.SHUTDOWN_CPU_P95_PCT:
        return "거의 미사용"
    if (raw.cpu_p95_pct is not None and raw.cpu_p95_pct <= recommendation.CPU_DOWNSIZE_P95_PCT
            and raw.mem_p95_pct is not None and raw.mem_p95_pct <= recommendation.MEM_DOWNSIZE_P95_PCT):
        return "여유 있음 (축소 검토)"
    return "정상"


def to_report_row_item(raw: ReportRowRaw, is_online: bool, now: datetime) -> ReportRowItem:
    """ReportRowRaw(repo) + is_online + now -> ReportRowItem(ViewModel) — P2 단일 변환.

    `now`로 uptime_days 계산 (now - boot_time).
    표시 파생 (role / recommendation / risk_level / badge_class / os_display / internal_ip[0])은 모두 여기서.
    USE Method 분류(`recommendation`)는 양식 B(엔지니어용)·`risk_level`은 양식 A(고객용) KPI/표 노출.
    `diagnosis`는 양식 B "판단" 컬럼 자동 해석.
    """
    rec = recommendation.classify(recommendation.ResourceStats(
        cpu_p95_pct=raw.cpu_p95_pct,
        cpu_peak_pct=raw.cpu_peak_pct,
        mem_p95_pct=raw.mem_p95_pct,
        swap_used=raw.swap_used,
        net_avg_kbps=None,  # 1차 MVP — net 집계 미구현
    ))
    risk_level, risk_label, risk_badge_class = _RISK_FROM_RECOMMENDATION[rec]
    uptime_days: int | None = None
    if raw.boot_time is not None:
        delta = now - raw.boot_time
        uptime_days = max(0, int(delta.total_seconds() // 86400))

    saturation = None
    if raw.load_15m_max is not None and raw.cpu_cores and raw.cpu_cores > 0:
        saturation = raw.load_15m_max / raw.cpu_cores

    cpu_variance = None
    if raw.cpu_p95_pct and raw.cpu_peak_pct and raw.cpu_p95_pct > 0:
        cpu_variance = raw.cpu_peak_pct / raw.cpu_p95_pct
    mem_variance = None
    if raw.mem_p95_pct and raw.mem_peak_pct and raw.mem_p95_pct > 0:
        mem_variance = raw.mem_peak_pct / raw.mem_p95_pct
    return ReportRowItem(
        server_id=raw.server_id,
        public_id=raw.public_id,
        hostname=raw.hostname,
        role=infer_role(raw.services),
        is_online=is_online,
        os_display=_os_display(raw.os_id, raw.os_version),
        kernel_version=raw.kernel_version,
        internal_ip=raw.ip_internal[0] if raw.ip_internal else None,
        cpu_p95_pct=raw.cpu_p95_pct,
        cpu_peak_pct=raw.cpu_peak_pct,
        mem_p95_pct=raw.mem_p95_pct,
        mem_peak_pct=raw.mem_peak_pct,
        load_15m_max=raw.load_15m_max,
        swap_used=raw.swap_used,
        recommendation=rec,
        recommendation_label=recommendation.LABEL_KO[rec],
        badge_class=recommendation.BADGE_CLASS[rec],
        risk_level=risk_level,
        risk_label=risk_label,
        risk_badge_class=risk_badge_class,
        iowait_p95_pct=raw.iowait_p95_pct,
        iowait_peak_pct=raw.iowait_peak_pct,
        worst_mount=raw.worst_mount,
        worst_mount_used_pct=raw.worst_mount_used_pct,
        worst_mount_days_until_full=raw.worst_mount_days_until_full,
        uptime_days=uptime_days,
        reboot_count=raw.reboot_count,
        saturation_ratio=saturation,
        cpu_variance_ratio=cpu_variance,
        mem_variance_ratio=mem_variance,
        disk_iops_baseline=raw.disk_iops_baseline,
        disk_iops_p95=raw.disk_iops_p95,
        disk_iops_peak=raw.disk_iops_peak,
        disk_throughput_kbps=raw.disk_throughput_kbps,
        disk_throughput_kbps_p95=raw.disk_throughput_kbps_p95,
        disk_throughput_kbps_peak=raw.disk_throughput_kbps_peak,
        net_rx_kbps=raw.net_rx_kbps,
        net_rx_kbps_p95=raw.net_rx_kbps_p95,
        net_rx_kbps_peak=raw.net_rx_kbps_peak,
        net_tx_kbps=raw.net_tx_kbps,
        net_tx_kbps_p95=raw.net_tx_kbps_p95,
        net_tx_kbps_peak=raw.net_tx_kbps_peak,
        diagnosis=_build_diagnosis(raw, saturation, cpu_variance, mem_variance),
        # P3 임계 분류 색 — 템플릿 산술·임계 분기 금지 (#E1 P3)
        saturation_color=("#b91c1c" if (saturation is not None and saturation >= 1.0) else "#94a3b8"),
        cpu_variance_color=("#92400e" if (cpu_variance is not None and cpu_variance >= 1.5) else "#1e293b"),
        mem_variance_color=("#92400e" if (mem_variance is not None and mem_variance >= 1.5) else "#94a3b8"),
        worst_mount_days_color=(
            "#b91c1c" if (raw.worst_mount_days_until_full is not None and raw.worst_mount_days_until_full <= 30)
            else "#64748b"
        ),
        reboot_count_color=("#b91c1c" if raw.reboot_count >= 3 else "#94a3b8"),
    )


def build_role_distribution(raws: list) -> dict[str, int]:
    """ReportRowRaw list -> 역할별 서버 수 dict. 양식 A 상단 표시용 (M4)."""
    from collections import Counter  # noqa: PLC0415
    counter: Counter[str] = Counter()
    for r in raws:
        counter[infer_role(r.services)] += 1
    return dict(counter.most_common())


# UtilizationBar 임계 — 환경 평균 활용률 색 결정 (P3 임계 분기 금지 → mapper 단일)
_UTIL_LOW_PCT  = 60        # 미만 → 녹색 (여유)
_UTIL_HIGH_PCT = 80        # 이상 → 빨강 (압박)
_UTIL_COLOR_LOW  = "#22c55e"
_UTIL_COLOR_MID  = "#f59e0b"
_UTIL_COLOR_HIGH = "#ef4444"
_UTIL_COLOR_NONE = "#cbd5e1"  # 표본 부재

# 도넛 SVG 원주 — r=42, 2*pi*r ≈ 263.89. pct 0~100을 0~_UTIL_DONUT_CIRC로 매핑.
_UTIL_DONUT_CIRC = 263.89

# ─── 프로비저닝 분포 도넛 (3 카테고리) ──────────────────────────────────
# idle/shutdown은 over_provisioned의 극단으로 흡수 — 사용자가 사양 큰 상태 통합 표시 원함.
# 보고서 KPI(_RISK_FROM_RECOMMENDATION)는 그대로 3단계(high/attention/normal) 유지.
_DONUT_SEGMENT_FROM_REC: dict[str, str] = {
    "under_provisioned": "under",
    "over_provisioned":  "over",
    "shutdown":          "over",   # 사양 큰 상태 극단 — over로 흡수
    "idle":              "over",
    "optimal":           "normal",
    "insufficient_data": "normal",
}

# 도넛 그리는 순서(언더 12시 방향 시작) + 범례 순서. (key, label, hex)
# 색 정책: 빨강(위험)·초록(정상) 이분과 헷갈리지 않게 over는 청록 — 운영자가 "검토 영역" 직관 인지.
# 도넛 범례는 풀네임(공간 충분), 서버 목록 셀은 _SHORT_LABEL 별도 (좁은 칸).
_DONUT_SEGMENT_DEFS: list[tuple[str, str, str]] = [
    ("under",  "언더 프로비저닝", "#ef4444"),  # 자원 부족 — 사양 상향 (빨강 = 위험)
    ("over",   "오버 프로비저닝", "#06b6d4"),  # 자원 여유 — 사양 축소·종료 검토 (청록 = 정보 검토)
    ("normal", "정상",           "#22c55e"),  # 적정 (초록 = 안전)
]

# 서버 목록 셀 안 표시용 약어 — 좁은 칸. 도넛 범례(풀네임)와 별도 매핑.
_DONUT_SEGMENT_SHORT_LABEL: dict[str, str] = {
    "under":  "Under",
    "over":   "Over",
    "normal": "Normal",
}


def _bar_color(pct: float | None) -> str:
    if pct is None:
        return _UTIL_COLOR_NONE
    if pct >= _UTIL_HIGH_PCT:
        return _UTIL_COLOR_HIGH
    if pct >= _UTIL_LOW_PCT:
        return _UTIL_COLOR_MID
    return _UTIL_COLOR_LOW


def _dash_length(pct: float | None) -> float:
    if pct is None:
        return 0.0
    return max(0.0, min(pct, 100.0)) / 100.0 * _UTIL_DONUT_CIRC


def build_risk_donut_segments(risk_counts: dict[str, int]) -> tuple[list, int, int]:
    """카테고리별 카운트 -> (RiskDonutSegment list, total, under_count).

    risk_counts 예: {"under": 1, "over": 2, "normal": 7}.
    누락 키는 0으로 취급. dash_length·dash_offset은 누적 비례 계산.
    under_count는 도넛 중앙 강조용 (가장 시급한 카테고리).
    """
    from assessment_engine.web.view_models import RiskDonutSegment  # noqa: PLC0415
    total = sum(risk_counts.values())
    segments: list = []
    cum_offset = 0.0
    for key, label, color in _DONUT_SEGMENT_DEFS:
        count = risk_counts.get(key, 0)
        dash_length = (count / total) * _UTIL_DONUT_CIRC if (total > 0 and count > 0) else 0.0
        segments.append(RiskDonutSegment(
            key=key, label=label, color=color, count=count,
            dash_length=dash_length, dash_offset=-cum_offset,  # 음수 offset = 시계방향 이동
        ))
        cum_offset += dash_length
    under_count = risk_counts.get("under", 0)
    return segments, total, under_count


def build_environment_overview(details: list, online_count: int, utilization=None, risk_counts=None):
    """ServerDetail list + online_count + EnvironmentUtilizationRaw + risk_counts -> EnvironmentOverview.

    list 화면 상단 환경 요약 — 총 N대·자원 합계·역할 분포·온라인/오프라인·평균 활용률·위험도 분포.
    utilization=None이면 활용률 빈 list. risk_counts=None이면 위험도 도넛 빈 list.
    """
    from collections import Counter  # noqa: PLC0415

    from assessment_engine.web.view_models import EnvironmentOverview, UtilizationBar  # noqa: PLC0415

    total = len(details)
    total_vcpus = sum(d.cpu_cores or 0 for d in details)
    total_mem_kb = sum(d.mem_total_kb or 0 for d in details)
    total_disk_bytes = 0
    for d in details:
        for disk in d.disks or []:
            total_disk_bytes += disk.get("size_bytes") or 0
    role_counter: Counter[str] = Counter()
    for d in details:
        role_counter[infer_role(d.services)] += 1

    util_bars: list = []
    util_sample = 0
    if utilization is not None:
        util_sample = utilization.sample_size
        util_bars = [
            UtilizationBar(label="CPU",     pct=utilization.cpu_avg_pct,
                           bar_color=_bar_color(utilization.cpu_avg_pct),
                           dash_length=_dash_length(utilization.cpu_avg_pct)),
            UtilizationBar(label="메모리", pct=utilization.mem_avg_pct,
                           bar_color=_bar_color(utilization.mem_avg_pct),
                           dash_length=_dash_length(utilization.mem_avg_pct)),
            UtilizationBar(label="디스크", pct=utilization.disk_avg_pct,
                           bar_color=_bar_color(utilization.disk_avg_pct),
                           dash_length=_dash_length(utilization.disk_avg_pct)),
        ]

    risk_segments: list = []
    risk_total = 0
    risk_under = 0
    if risk_counts is not None:
        risk_segments, risk_total, risk_under = build_risk_donut_segments(risk_counts)

    return EnvironmentOverview(
        total=total,
        online=online_count,
        offline=total - online_count,
        total_vcpus=total_vcpus,
        total_memory_gb=round(total_mem_kb / 1024 / 1024, 1),
        total_disk_gb=int(total_disk_bytes / 10**9),
        role_distribution=dict(role_counter.most_common()),
        utilization=util_bars,
        util_sample_size=util_sample,
        risk_donut=risk_segments,
        risk_donut_total=risk_total,
        risk_high_count=risk_under,
    )


# capacity trigger 3종 색 — hue 명확 분리. 본문 badge와 범례 단일 진실.
# 한 서버에 여러 trigger 동시 발동 가능 (CPU + 메모리 등). 묶음 카테고리 없이 각각 표시.
_CAPACITY_TRIGGER_COLORS: dict[str, str] = {
    "스왑":   "#dc2626",   # 빨강 — 메모리 부족 1차 신호 (paging 발생)
    "CPU":    "#2563eb",   # 파랑 — CPU 임계 초과
    "메모리": "#8b5cf6",   # 보라 — 메모리 임계 초과
}

# inactive trigger badge 톤 — active 색은 위 dict, inactive는 본 상수. 둘 다 mapper 단일 진실.
_CAPACITY_TRIGGER_INACTIVE_BG = "#f8fafc"
_CAPACITY_TRIGGER_INACTIVE_FG = "#cbd5e1"


def to_capacity_warning_item(raw):
    """ReportRowRaw -> CapacityWarningItem. caller가 under_provisioned 필터링 후 호출.

    triggers list — 3종(스왑/CPU/메모리) 항상 포함, active로 분기:
    - swap_used=True → "스왑" active (메모리 부족 1차 신호)
    - cpu_p95 >= CPU_UPSIZE_P95_PCT → "CPU" active
    - mem_p95 >= MEM_UPSIZE_P95_PCT → "메모리" active
    비활성 trigger도 list에 포함 (시각 일관 — "이 카드는 3종 자원 추적" 명시).
    """
    from assessment_engine.web.view_models import CapacityTriggerBadge, CapacityWarningItem  # noqa: PLC0415
    swap_active = bool(raw.swap_used)
    cpu_active = (raw.cpu_p95_pct or 0) >= recommendation.CPU_UPSIZE_P95_PCT
    mem_active = (raw.mem_p95_pct or 0) >= recommendation.MEM_UPSIZE_P95_PCT

    def _badge(label: str, active: bool) -> CapacityTriggerBadge:
        color = _CAPACITY_TRIGGER_COLORS[label]
        if active:
            bg, fg = color, "#fff"
        else:
            bg, fg = _CAPACITY_TRIGGER_INACTIVE_BG, _CAPACITY_TRIGGER_INACTIVE_FG
        return CapacityTriggerBadge(label=label, color=color, active=active, bg_color=bg, fg_color=fg)

    triggers = [
        _badge("스왑",   swap_active),
        _badge("CPU",    cpu_active),
        _badge("메모리", mem_active),
    ]
    return CapacityWarningItem(
        public_id=raw.public_id,
        hostname=raw.hostname,
        cpu_p95_pct=raw.cpu_p95_pct,
        mem_p95_pct=raw.mem_p95_pct,
        swap_used=raw.swap_used,
        triggers=triggers,
    )


def to_disk_days_warning_item(
    public_id: str,
    hostname: str,
    mount: str,
    days_until_full: int,
    used_pct: float | None,
) -> AttentionRow:
    """raw tuple -> AttentionRow. caller가 days <= 30 필터링 후 호출.

    badge=N일 (rec-under_provisioned), mount_path 별도 attribute, meta="{used_pct}%" (없으면 빈 string).
    """
    meta = ""
    if used_pct is not None:
        meta = f"{used_pct:.0f}%"
    return AttentionRow(
        badge_class="rec-under_provisioned",
        badge_text=f"{days_until_full}일",
        link_href=f"/servers/{public_id}/storage",
        link_text=hostname,
        mount_path=mount,
        meta_text=meta,
    )


def to_os_eol_warning_item(raw) -> AttentionRow | None:
    """ReportRowRaw -> AttentionRow if matches _OS_EOL, else None.

    badge=EOL 라벨 + meta="{os_display} · EOL {eol_date}".
    """
    for (eol_os, eol_ver), date in _OS_EOL.items():
        if raw.os_id == eol_os and (raw.os_version or "").startswith(eol_ver):
            return AttentionRow(
                badge_class="rec-over_provisioned",
                badge_text="EOL",
                link_href=f"/servers/{raw.public_id}",
                link_text=raw.hostname,
                meta_text=f"{_os_display(raw.os_id, raw.os_version)} · EOL {date}",
            )
    return None


def to_agent_unstable_item(public_id: str, hostname: str, restart_count: int) -> AttentionRow:
    """raw -> AttentionRow. caller가 임계 필터링 후 호출."""
    return AttentionRow(
        badge_class="rec-over_provisioned",
        badge_text=f"{restart_count}회",
        link_href=f"/servers/{public_id}",
        link_text=hostname,
        meta_text="신뢰도 낮음",
    )


# OS EOL (End-of-Life) 정적 매핑 — 정성 요약에서 legacy OS 신호 생성용.
# 확장 시 본 dict에 (os_id, os_version_prefix) 키 추가. ISO 날짜 문자열만.
_OS_EOL: dict[tuple[str, str], str] = {
    ("centos", "7"):       "2024-06-30",
    ("rhel",   "7"):       "2024-06-30",
    ("ubuntu", "18.04"):   "2023-05-31",
    ("debian", "10"):      "2024-06-30",
    ("debian", "11"):      "2024-07-14",  # standard support EOL (LTS는 2026-08까지)
    ("centos", "8"):       "2024-05-31",  # CentOS Stream 8 (RHEL 8 family 중 Stream만 EOL — AlmaLinux/Rocky 8은 2029까지 active)
}


def compute_report_totals_from_raw(raws: list) -> ReportTotals:
    """ReportRowRaw list -> 묶음 자원 총량. cpu_cores·mem_total_kb·disks 합산 (P2).

    양식 A 상단의 마이그레이션 capacity 산정 입력 — "총 N대 = 총 X vCPU·Y GB·Z TB".
    """
    total_vcpus = sum(r.cpu_cores or 0 for r in raws)
    total_mem_kb = sum(r.mem_total_kb or 0 for r in raws)
    total_disk_bytes = 0
    for r in raws:
        for d in r.disks or []:
            total_disk_bytes += d.get("size_bytes") or 0
    return ReportTotals(
        total_vcpus=total_vcpus,
        total_memory_gb=int(total_mem_kb / 1024 / 1024),       # KB -> GB
        total_disk_gb=int(total_disk_bytes / 10**9),           # bytes -> GB (벤더 표기 관례)
    )


def to_inventory_export_entry(
    detail: ServerDetail,
    stats: ReportRowRaw | None = None,
) -> InventoryExportEntry:
    """ServerDetail(outbound) + 선택적 ReportRowRaw -> InventoryExportEntry v2.

    `stats`가 None이면 right-sizing 필드 null로 발행 — 신규 서버 / 데이터 부족 시.
    스키마·정제 원칙·사용처: docs/architecture/inventory-export.md.
    """
    boot_gb, additional = _split_disks(detail.disks, detail.mounts)
    if stats is not None:
        rec = recommendation.classify(recommendation.ResourceStats(
            cpu_p95_pct=stats.cpu_p95_pct,
            cpu_peak_pct=stats.cpu_peak_pct,
            mem_p95_pct=stats.mem_p95_pct,
            swap_used=stats.swap_used,
            net_avg_kbps=None,  # 현재 net 집계 미통합 — idle/shutdown 판정 skip
        ))
        cpu_p95 = stats.cpu_p95_pct
        cpu_peak = stats.cpu_peak_pct
        mem_p95 = stats.mem_p95_pct
        mem_peak = stats.mem_peak_pct
        load_15m_max = stats.load_15m_max
        swap_used = stats.swap_used
    else:
        rec = "insufficient_data"
        cpu_p95 = cpu_peak = mem_p95 = mem_peak = load_15m_max = None
        swap_used = False

    return InventoryExportEntry(
        machine_id=detail.machine_id,
        hostname=detail.hostname,
        role=infer_role(detail.services),
        last_seen_at=detail.last_seen_at,
        services=_services_for_export(detail.services, detail.listen_ports),
        os={
            "family": detail.os_id,
            "version": detail.os_version,
            "kernel": detail.kernel_version,
        },
        compute={
            "vcpu_count": detail.cpu_cores,
            "memory_mb": (detail.mem_total_kb // 1024) if detail.mem_total_kb else None,
            "cpu_p95_pct": cpu_p95,
            "cpu_peak_pct": cpu_peak,
            "mem_p95_pct": mem_p95,
            "mem_peak_pct": mem_peak,
            "load_15m_max": load_15m_max,
            "swap_used": swap_used,
            "recommended_size_class": {
                "key": rec,
                "label": recommendation.LABEL_KO.get(rec, rec),
            },
        },
        storage={
            "boot_disk_gb": boot_gb,
            "iops_baseline": stats.disk_iops_baseline if stats else None,
            "iops_p95": stats.disk_iops_p95 if stats else None,
            "iops_peak": stats.disk_iops_peak if stats else None,
            "throughput_kbps_baseline": stats.disk_throughput_kbps if stats else None,
            "throughput_kbps_p95": stats.disk_throughput_kbps_p95 if stats else None,
            "throughput_kbps_peak": stats.disk_throughput_kbps_peak if stats else None,
            "additional_disks": additional,
        },
        network={
            "addresses": _network_addresses(detail.ip_internal, detail.ip_external),
            "rx_kbps_baseline": stats.net_rx_kbps if stats else None,
            "rx_kbps_p95": stats.net_rx_kbps_p95 if stats else None,
            "rx_kbps_peak": stats.net_rx_kbps_peak if stats else None,
            "tx_kbps_baseline": stats.net_tx_kbps if stats else None,
            "tx_kbps_p95": stats.net_tx_kbps_p95 if stats else None,
            "tx_kbps_peak": stats.net_tx_kbps_peak if stats else None,
        },
    )