import dataclasses
import json
from datetime import datetime

from assessment_engine.web.services.mappers.server import (
    DYNAMIC_PORT_MIN,
    enrich_server_detail,
)
from assessment_engine.web.services.serialization_util import json_default as _json_default
from assessment_engine.web.view_models.metric import (
    CpuSnapshot,
    DiskIoSnapshot,
    MemSnapshot,
    MetricDashboard,
    MountDashSnapshot,
    NetIoSnapshot,
)
from assessment_engine.web.view_models.server import (
    DiskItem,
    IpAddr,
    ListenPortItem,
    MatchedPort,
    ServerDetailResponse,
    ServiceItem,
    VolumeItem,
)

_DETAIL_DISPLAY_FIELDS = frozenset(
    {
        "known_services",
        "show_unknown_badge",
        "key_listen_ports",
        "os_display",
        "cpu_display",
        "disk_total_gb",
        "disk_unallocated_gb",
        "sorted_services",
        "sorted_listen_ports",
        "services_count",
        "listen_ports_count",
        "disks_count",
        "volume_total_gb",
        "volumes_count",
    }
)


def server_detail_to_json(v: ServerDetailResponse) -> str:
    return json.dumps(dataclasses.asdict(v), default=_json_default)


def server_detail_from_json(raw: str) -> ServerDetailResponse:
    data = json.loads(raw)
    data["disks"] = [DiskItem(name=d["name"], size_gb=d.get("size_gb")) for d in data.get("disks") or []]
    data["volumes"] = [VolumeItem(**v) for v in data.get("volumes") or []]
    data["ip_internal"] = [IpAddr(**a) for a in data.get("ip_internal") or []]
    if data.get("ip_external") is not None:
        data["ip_external"] = [IpAddr(**a) for a in data["ip_external"]]
    raw_services = data.get("services")
    data["services"] = (
        [
            ServiceItem(
                unit=s["unit"],
                sub=s["sub"],
                category=s.get("category") or "unknown",
                ports=[MatchedPort(proto=p["proto"], port=p["port"]) for p in s.get("ports") or []],
                display_name=s.get("display_name") or s["unit"].removesuffix(".service"),
                category_count=s.get("category_count", 1),
            )
            for s in raw_services
        ]
        if raw_services is not None
        else None
    )
    data["listen_ports"] = [
        ListenPortItem(
            proto=p["proto"],
            addr=p["addr"],
            port=p["port"],
            uid=p.get("uid"),
            pid=p.get("pid"),
            comm=p.get("comm"),
            is_significant=p.get("is_significant", p.get("port", 0) < DYNAMIC_PORT_MIN),
        )
        for p in data.get("listen_ports") or []
    ]
    for f in ("boot_time", "agent_started_at", "last_seen_at"):
        if isinstance(data.get(f), str):
            data[f] = datetime.fromisoformat(data[f])
    for key in _DETAIL_DISPLAY_FIELDS:
        data.pop(key, None)
    if "public_id" not in data:
        data["public_id"] = ""
    data.setdefault("composite_id", None)
    data.setdefault("machine_id", None)
    data.setdefault("os_family", None)
    return enrich_server_detail(ServerDetailResponse(**data))


def dashboard_to_json(v: MetricDashboard) -> str:
    return json.dumps(dataclasses.asdict(v), default=_json_default)


def dashboard_from_json(raw: str) -> MetricDashboard:
    data = json.loads(raw)
    raw_ca = data.get("collected_at")
    return MetricDashboard(
        collected_at=datetime.fromisoformat(raw_ca) if isinstance(raw_ca, str) else None,
        cpu=CpuSnapshot(**data["cpu"]) if data.get("cpu") else None,
        cpu_run_queue=data.get("cpu_run_queue"),
        disk_await_ms=data.get("disk_await_ms"),
        disk_queue=data.get("disk_queue"),
        mem_pages_input_rate=data.get("mem_pages_input_rate"),
        net_retrans_pct=data.get("net_retrans_pct"),
        memory=MemSnapshot(**data["memory"]) if data.get("memory") else None,
        disk_io=[DiskIoSnapshot(**d) for d in data.get("disk_io") or []],
        net_io=[NetIoSnapshot(**n) for n in data.get("net_io") or []],
        mounts=[MountDashSnapshot(**m) for m in data.get("mounts") or []],
    )
