import dataclasses
import json
from datetime import datetime

from web.view_models import (
    CpuSnapshot,
    DiskIoSnapshot,
    DiskItem,
    ListenPortItem,
    MemSnapshot,
    MetricDashboard,
    MountDashSnapshot,
    NetIoSnapshot,
    ServerDetailResponse,
    ServiceItem,
    SwapSnapshot,
)

_DETAIL_DISPLAY_FIELDS = frozenset({
    "known_services", "show_unknown_badge", "key_listen_ports",
    "os_display", "cpu_display", "disk_total_gb",
})


def _json_default(obj: object) -> str:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Cannot serialize {type(obj)}")


def server_detail_to_json(v: ServerDetailResponse) -> str:
    return json.dumps(dataclasses.asdict(v), default=_json_default)


def server_detail_from_json(raw: str) -> ServerDetailResponse:
    from web.services.mappers import enrich_server_detail
    data = json.loads(raw)
    data["disks"] = [DiskItem(**d) for d in data.get("disks") or []]
    raw_services = data.get("services")
    data["services"] = (
        [ServiceItem(unit=s["unit"], sub=s["sub"], category=s.get("category") or "unknown", ports=s.get("ports") or [], display_name=s.get("display_name") or s["unit"].removesuffix(".service")) for s in raw_services]
        if raw_services is not None else None
    )
    data["listen_ports"] = [ListenPortItem(**p) for p in data.get("listen_ports") or []]
    for f in ("boot_time", "last_seen_at"):
        if isinstance(data.get(f), str):
            data[f] = datetime.fromisoformat(data[f])
    for key in ("mem_total_kb", "swap_total_kb", *_DETAIL_DISPLAY_FIELDS):
        data.pop(key, None)
    if "public_id" not in data:
        data["public_id"] = ""
    return enrich_server_detail(ServerDetailResponse(**data))


def dashboard_to_json(v: MetricDashboard) -> str:
    return json.dumps(dataclasses.asdict(v), default=_json_default)


def dashboard_from_json(raw: str) -> MetricDashboard:
    data = json.loads(raw)
    raw_ca = data.get("collected_at")
    return MetricDashboard(
        collected_at=datetime.fromisoformat(raw_ca) if isinstance(raw_ca, str) else None,
        cpu=CpuSnapshot(**data["cpu"]) if data.get("cpu") else None,
        load_1m=data.get("load_1m"),
        load_5m=data.get("load_5m"),
        load_15m=data.get("load_15m"),
        memory=MemSnapshot(**data["memory"]) if data.get("memory") else None,
        swap=SwapSnapshot(**data["swap"]) if data.get("swap") else None,
        disk_io=[DiskIoSnapshot(**d) for d in data.get("disk_io") or []],
        net_io=[NetIoSnapshot(**n) for n in data.get("net_io") or []],
        mounts=[MountDashSnapshot(**m) for m in data.get("mounts") or []],
    )
