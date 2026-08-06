"""Redis 캐시 serde — `ServerDetailResponse` / `MetricDashboard` (#C3).

역직렬화 직후 `enrich_*` 를 다시 부른다. 그래야 SSR·JSON·캐시 어느 경로로 와도 같은 ViewModel 이 된다.
"""

import dataclasses
import json
from datetime import datetime

from assessment_engine.json_types import JsonObject, json_list
from assessment_engine.service_classifier import MatchedPort
from assessment_engine.web.services.mappers.server import (
    DYNAMIC_PORT_MIN,
    enrich_server_detail,
)
from assessment_engine.web.services.serialization import json_default
from assessment_engine.web.view_models.metric import (
    CpuCoreSnapshot,
    CpuSnapshot,
    DiskIoSnapshot,
    ErrorSignal,
    MemSnapshot,
    MetricDashboard,
    MountDashSnapshot,
    NetIoSnapshot,
    SaturationSignal,
)
from assessment_engine.web.view_models.server import (
    DiskItem,
    IpAddr,
    ListenPortItem,
    ServerDetailResponse,
    ServiceItem,
    VolumeItem,
)

# pop 후 enrich_server_detail 이 재계산하는 파생 필드만 나열. 여기 넣은 필드는 캐시값을 버리고 enrich 재산출로
# 복원되므로, enrich 가 못 만드는 필드(block_devices 등 캐시에 없는 raw 의존)는 넣지 말 것 — 넣으면 pop 후
# 복원 불가로 None 이 된다. disk_total_gb·volume_total_gb·disk_unallocated_gb 는 storage_layers_gb(block_devices)
# 산식이라 enrich 가 재계산 못 함 -> asdict 저장분을 그대로 보존(여기 미포함).
_DETAIL_DISPLAY_FIELDS = frozenset(
    {
        "known_services",
        "show_unknown_badge",
        "key_listen_ports",
        "os_display",
        "cpu_display",
        "sorted_services",
        "sorted_listen_ports",
        "services_count",
        "listen_ports_count",
        "disks_count",
        "volumes_count",
    }
)


def _error_signal_from_dict(e: JsonObject) -> ErrorSignal:
    """ErrorSignal 복원 — last_at 만 datetime 재구성, 나머지는 그대로."""
    last_at = e.get("last_at")
    return ErrorSignal(
        key=e["key"],
        label=e["label"],
        state=e["state"],
        count=e.get("count"),
        context=e.get("context"),
        last_at=datetime.fromisoformat(last_at) if last_at else None,
        window_label=e.get("window_label"),
        detail=e.get("detail"),
    )


def server_detail_to_json(v: ServerDetailResponse) -> str:
    return json.dumps(dataclasses.asdict(v), default=json_default)


def server_detail_from_json(raw: str) -> ServerDetailResponse:
    data = json.loads(raw)
    data["disks"] = [DiskItem(name=d["name"], size_gb=d.get("size_gb")) for d in json_list(data, "disks")]
    data["volumes"] = [VolumeItem(**v) for v in json_list(data, "volumes")]
    data["ip_internal"] = [IpAddr(**a) for a in json_list(data, "ip_internal")]
    if data.get("ip_external") is not None:
        data["ip_external"] = [IpAddr(**a) for a in data["ip_external"]]
    raw_services = data.get("services")
    data["services"] = (
        [
            ServiceItem(
                unit=s["unit"],
                sub=s["sub"],
                category=s.get("category") or "unknown",
                ports=[MatchedPort(proto=p["proto"], port=p["port"]) for p in json_list(s, "ports")],
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
        for p in json_list(data, "listen_ports")
    ]
    for f in ("boot_time", "agent_started_at", "last_seen_at"):
        if isinstance(data.get(f), str):
            data[f] = datetime.fromisoformat(data[f])
    for key in _DETAIL_DISPLAY_FIELDS:
        data.pop(key, None)
    if "public_id" not in data:
        data["public_id"] = ""
    data.setdefault("agent_id", "")
    data.setdefault("composite_id", None)
    data.setdefault("machine_id", None)
    data.setdefault("os_family", None)
    return enrich_server_detail(ServerDetailResponse(**data))


def dashboard_to_json(v: MetricDashboard) -> str:
    return json.dumps(dataclasses.asdict(v), default=json_default)


def dashboard_from_json(raw: str) -> MetricDashboard:
    data = json.loads(raw)
    raw_ca = data.get("collected_at")
    return MetricDashboard(
        collected_at=datetime.fromisoformat(raw_ca) if isinstance(raw_ca, str) else None,
        cpu=CpuSnapshot(**data["cpu"]) if data.get("cpu") else None,
        memory=MemSnapshot(**data["memory"]) if data.get("memory") else None,
        disk_io=[DiskIoSnapshot(**d) for d in json_list(data, "disk_io")],
        net_io=[NetIoSnapshot(**n) for n in json_list(data, "net_io")],
        mounts=[MountDashSnapshot(**m) for m in json_list(data, "mounts")],
        disk_usage_pct=data.get("disk_usage_pct"),
        cpu_saturation=[SaturationSignal(**s) for s in json_list(data, "cpu_saturation")],
        mem_saturation=[SaturationSignal(**s) for s in json_list(data, "mem_saturation")],
        disk_saturation=[SaturationSignal(**s) for s in json_list(data, "disk_saturation")],
        net_saturation=[SaturationSignal(**s) for s in json_list(data, "net_saturation")],
        errors=[_error_signal_from_dict(e) for e in json_list(data, "errors")],
        cpu_cores=[CpuCoreSnapshot(**c) for c in json_list(data, "cpu_cores")],
    )
