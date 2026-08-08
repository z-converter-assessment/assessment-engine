import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_DNS, uuid5

ANCHOR = datetime(2026, 1, 1, tzinfo=UTC)
"""전 시드가 기준으로 삼는 고정 시각. 캡처 대상 endpoint 는 `end`/`anchor_at` 을 이 값으로 넘긴다."""


def public_id(n: int) -> str:
    return str(uuid5(NAMESPACE_DNS, f"http-seed-{n}"))


_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_GENERATED_AT = re.compile(r'("generated_at":\s*")[^"]+(")')
_RENDERED_NOW = re.compile(r"(발행|기준) \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_ASSET_V = re.compile(r"\?v=[0-9a-f]+")
_ENGINE_VERSION = re.compile(r"\bv\d+\.\d+\.\d+\b")
_SEED_IDS = {public_id(n) for n in range(1, 7)}


def normalize(value: object) -> object:
    if isinstance(value, str):
        text = _ENGINE_VERSION.sub(
            "vENGINE_VERSION",
            _RENDERED_NOW.sub(
                r"\1 RENDER_TIME", _GENERATED_AT.sub(r"\1GENERATED_AT\2", _ASSET_V.sub("?v=ASSET_V", value))
            ),
        )
        counter: dict[str, str] = {}

        def _sub(m: re.Match[str]) -> str:
            found = m.group(0)
            if found in _SEED_IDS:
                return found
            return counter.setdefault(found, f"UUID-{len(counter) + 1}")

        return _UUID_RE.sub(_sub, text)
    if isinstance(value, list):
        return [normalize(v) for v in value]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return value


def _server_detail(n: int, **over: Any) -> Any:
    from assessment_engine.db.dtos.outbound import ServerDetail

    base: dict[str, Any] = {
        "id": n,
        "public_id": public_id(n),
        "agent_id": str(uuid5(NAMESPACE_DNS, f"agent-{n}")),
        "composite_id": f"composite-{n}",
        "machine_id": None,
        "hostname": f"host-{n}",
        "agent_version": "1.0.0",
        "os_family": "linux",
        "os_id": "ubuntu",
        "os_version": "22.04",
        "os_codename": "jammy",
        "kernel_version": "5.15.0",
        "cpu_cores": 4,
        "cpu_model": "seed-cpu",
        "cpu_arch": "x86_64",
        "cpu_bits": 64,
        "mem_total_bytes": 8 * 1024**3,
        "swap_total_bytes": 0,
        "last_seen_at": ANCHOR - timedelta(minutes=1),
        "boot_time": ANCHOR - timedelta(days=30),
        "agent_started_at": ANCHOR - timedelta(days=1),
        "net_interfaces": [
            {
                "id": f"52:54:00:00:00:{n:02d}",
                "id_type": "mac",
                "name": "eth0",
                "kind": "physical",
                "speed_mbps": 1000,
                "gateway": "10.0.0.254",
                "addresses": [{"address": f"10.0.0.{n}", "prefix": 24, "family": "ipv4"}],
            }
        ],
        "ip_external": None,
        "block_devices": [{"id": f"disk-{n}", "name": "sda", "type": "disk", "size_bytes": 50 * 10**9}],
        "lvm_vgs": [],
        "services": [{"unit": "nginx.service", "sub": "running"}],
        "listen_ports": [{"proto": "tcp", "port": 80, "comm": "nginx"}],
    }
    base.update(over)
    fields = {f.name for f in __import__("dataclasses").fields(ServerDetail)}
    return ServerDetail(**{k: v for k, v in base.items() if k in fields})


def QUERY_SEED() -> dict[str, Any]:  # noqa: N802
    from tests.builders import report_row_raw

    details = [_server_detail(1), _server_detail(2), _server_detail(3, os_family="windows", os_id="windows")]
    return {
        "list_server_ids": [1, 2, 3],
        "resolve_server_id": 1,
        "resolve_server_ids": {public_id(n): n for n in (1, 2, 3)},
        "list_server_public_ids": [public_id(n) for n in (1, 2, 3)],
        "get_servers": details,
        "get_server": details[0],
        "list_servers": [_server_summary(n) for n in (1, 2, 3)],
        "get_network": _network(1),
        "get_storage": _storage(1),
        "get_report_aggregate": [
            report_row_raw(server_id=1, public_id=public_id(1), hostname="host-1", cpu_p95_pct=82.0, mem_p95_pct=91.0),
            report_row_raw(server_id=2, public_id=public_id(2), hostname="host-2", cpu_p95_pct=2.0, mem_p95_pct=8.0),
            report_row_raw(server_id=3, public_id=public_id(3), hostname="host-3", os_family="windows"),
        ],
    }


def _server_summary(n: int) -> Any:
    from assessment_engine.db.dtos.outbound import ServerSummary

    fields = {f.name for f in __import__("dataclasses").fields(ServerSummary)}
    base: dict[str, Any] = {
        "id": n,
        "public_id": public_id(n),
        "hostname": f"host-{n}",
        "os_family": "linux",
        "os_id": "ubuntu",
        "os_version": "22.04",
        "cpu_cores": 4,
        "mem_total_bytes": 8 * 1024**3,
        "last_seen_at": ANCHOR - timedelta(minutes=1),
        "composite_id": f"composite-{n}",
        "kernel_version": "5.15.0",
        "product_name": None,
        "ip_external": None,
        "block_devices": [{"id": f"disk-{n}", "name": "sda", "type": "disk", "size_bytes": 50 * 10**9}],
        "service_categories": ["web"],
    }
    return ServerSummary(**{k: v for k, v in base.items() if k in fields})


def _network(n: int) -> Any:
    from assessment_engine.db.dtos.outbound import NetworkWithIo

    return NetworkWithIo(
        server_id=n,
        public_id=public_id(n),
        hostname=f"host-{n}",
        net_interfaces=[
            {
                "id": f"52:54:00:00:00:{n:02d}",
                "id_type": "mac",
                "name": "eth0",
                "kind": "physical",
                "speed_mbps": 1000,
                "gateway": "10.0.0.254",
                "addresses": [{"address": f"10.0.0.{n}", "prefix": 24, "family": "ipv4"}],
            }
        ],
        ip_external=None,
        net_io=[],
        inventory_at=ANCHOR - timedelta(minutes=5),
    )


def _storage(n: int) -> Any:
    from assessment_engine.db.dtos.outbound import MountUsageRaw, StorageWithUsage

    return StorageWithUsage(
        server_id=n,
        public_id=public_id(n),
        hostname=f"host-{n}",
        block_devices=[{"id": f"disk-{n}", "name": "sda", "type": "disk", "size_bytes": 50 * 10**9}],
        lvm_vgs=[],
        filesystems=[
            MountUsageRaw(
                mountpoint="/",
                device_id=f"disk-{n}",
                fstype="ext4",
                used_bytes=10**10,
                free_bytes=4 * 10**10,
                collected_at=ANCHOR - timedelta(minutes=5),
            )
        ],
        inventory_at=ANCHOR - timedelta(minutes=5),
    )
