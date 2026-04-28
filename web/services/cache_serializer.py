import dataclasses
import json
from datetime import datetime

from web.view_models import (
    CpuSnapshot,
    DiskIoSnapshot,
    DiskItem,
    MemSnapshot,
    MetricDashboard,
    MountDashSnapshot,
    NetIoSnapshot,
    ServerDetailResponse,
    SwapSnapshot,
)


def _json_default(obj: object) -> str:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Cannot serialize {type(obj)}")


def server_detail_to_json(v: ServerDetailResponse) -> str:
    return json.dumps(dataclasses.asdict(v), default=_json_default)


def server_detail_from_json(raw: str) -> ServerDetailResponse:
    data = json.loads(raw)
    data["disks"] = [DiskItem(**d) for d in data.get("disks") or []]
    for field in ("boot_time", "last_seen_at"):
        if isinstance(data.get(field), str):
            data[field] = datetime.fromisoformat(data[field])
    data.pop("mem_total_kb", None)
    data.pop("swap_total_kb", None)
    return ServerDetailResponse(**data)


def dashboard_to_json(v: MetricDashboard) -> str:
    return json.dumps(dataclasses.asdict(v), default=_json_default)


def dashboard_from_json(raw: str) -> MetricDashboard:
    data = json.loads(raw)
    return MetricDashboard(
        collected_at=data.get("collected_at"),
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
