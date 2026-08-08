import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any

from assessment_engine.db.dtos.outbound import ReportRowRaw

BUILDER_NOW = datetime(2026, 5, 12, 0, 0, tzinfo=UTC)
"""빌더가 쓰는 고정 앵커. 테스트가 시각을 직접 만들 때도 이 값을 기준으로 상대 계산한다."""

_DEFAULT_NIC: dict[str, Any] = {
    "id": "52:54:00:12:34:56",
    "id_type": "mac",
    "name": "eth0",
    "kind": "physical",
    "speed_mbps": 1000,
    "gateway": None,
    "addresses": [{"address": "10.0.0.1", "prefix": 24, "family": "ipv4"}],
}

_DEFAULT_DISK: dict[str, Any] = {"name": "sda", "size_bytes": 50 * 10**9, "type": "disk"}


def report_row_raw(**overrides: Any) -> ReportRowRaw:
    base: dict[str, Any] = {
        "server_id": 1,
        "public_id": "a",
        "hostname": "h",
        "os_family": None,
        "os_id": "ubuntu",
        "os_version": "22.04",
        "os_codename": "jammy",
        "kernel_version": "5.15",
        "net_interfaces": [dict(_DEFAULT_NIC)],
        "services": None,
        "last_seen_at": BUILDER_NOW,
        "cpu_p95_pct": None,
        "cpu_avg_pct": None,
        "cpu_peak_pct": None,
        "mem_p95_pct": None,
        "mem_avg_pct": None,
        "mem_peak_pct": None,
        "cpu_cores": 2,
        "mem_total_bytes": 2 * 1024**3,
        "block_devices": [dict(_DEFAULT_DISK)],
        "boot_time": BUILDER_NOW - timedelta(days=30),
    }
    base.update(overrides)
    return ReportRowRaw(**base)


def server_detail(server_id: int, hostname: str, **overrides: Any) -> Any:
    from datetime import timedelta

    from assessment_engine.db.dtos.outbound import ServerDetail

    base: dict[str, Any] = {
        "id": server_id,
        "public_id": f"p{server_id}",
        "agent_id": f"00000000-0000-4000-8000-{server_id:012d}",
        "composite_id": f"composite-{server_id}",
        "machine_id": None,
        "hostname": hostname,
        "agent_version": "1.0.0",
        "os_family": "linux",
        "os_id": "ubuntu",
        "os_version": "22.04",
        "os_codename": "jammy",
        "kernel_version": "5.15.0",
        "cpu_cores": 4,
        "cpu_model": "builder-cpu",
        "cpu_arch": "x86_64",
        "cpu_bits": 64,
        "mem_total_bytes": 8 * 1024**3,
        "swap_total_bytes": 0,
        "last_seen_at": BUILDER_NOW,
        "boot_time": BUILDER_NOW - timedelta(days=30),
        "agent_started_at": BUILDER_NOW - timedelta(days=1),
        "net_interfaces": [dict(_DEFAULT_NIC)],
        "ip_external": None,
        "block_devices": [dict(_DEFAULT_DISK)],
        "lvm_vgs": [],
        "services": None,
        "listen_ports": [],
    }
    base.update(overrides)
    fields = {f.name for f in dataclasses.fields(ServerDetail)}
    return ServerDetail(**{k: v for k, v in base.items() if k in fields})
