import json
from typing import TYPE_CHECKING

from assessment_engine.consumer.mappers import to_inventory_create, to_metric_create
from assessment_engine.consumer.schemas import InventoryInput, MetricsInput

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject

_MAC = "02:42:ac:11:00:03"
_NET_DEV = f"mac:{_MAC}"
_DISK0 = "gptid:{3484c6ca-d135-4483-a716-9207f855c8db}"
_DISK1 = "mbrsig:2579770672"


def _meta() -> JsonObject:
    return {
        "schema_version": "1.0",
        "agent_id": "00000000-0000-4000-8000-000000000001",
        "composite_id": "a" * 64,
        "machine_id": "12345678-1234-1234-1234-123456789abc",
        "agent_version": "2.0.0",
        "collected_at": "2026-05-27T00:00:00Z",
        "message_id": "00000000-0000-4000-8000-000000000001",
        "agent_started_at": "2026-05-27T00:00:00Z",
        "boot_time": "2026-05-26T00:00:00Z",
    }


def _windows_inventory() -> JsonObject:
    return {
        **_meta(),
        "message_type": "inventory",
        "os_family": "windows",
        "hostname": "WIN-HOST",
        "os_id": "windows",
        "os_version": "10.0.14393",
        "os_codename": None,
        "kernel_version": "14393",
        "cpu_cores": 4,
        "cpu_model": "Intel(R) Core(TM) i7",
        "mem_total_bytes": 8589934592,
        "ip_external": None,
        "block_devices": [
            {
                "name": "PhysicalDrive0",
                "type": "disk",
                "size_bytes": 256000000000,
                "fstype": None,
                "mountpoint": None,
                "parent": None,
                "id": "{3484c6ca-d135-4483-a716-9207f855c8db}",
                "id_type": "gptid",
            },
            {
                "name": "C:",
                "type": "volume",
                "size_bytes": 256000000000,
                "fstype": "ntfs",
                "mountpoint": "C:",
                "parent": "{3484c6ca-d135-4483-a716-9207f855c8db}",
                "id": "{86c0d2cb-c087-4dd4-aafb-671200504e25}",
                "id_type": "volguid",
            },
        ],
        "net_interfaces": [
            {
                "name": "Ethernet0",
                "id": _MAC,
                "id_type": "mac",
                "kind": "physical",
                "speed_mbps": None,
                "addresses": [{"address": "10.0.0.5", "prefix": 24, "family": "ipv4"}],
                "gateway": "10.0.0.1",
            }
        ],
        "lvm_vgs": [],
        "services": [],
        "listen_ports": [{"proto": "tcp", "addr": "0.0.0.0", "port": 445, "uid": None, "pid": 4, "comm": "System"}],
    }


def _windows_metrics() -> JsonObject:
    return {
        **_meta(),
        "message_type": "metrics",
        "os_family": "windows",
        "system.cpu": {
            "cpu.time": {
                "type": "counter",
                "unit": "s",
                "points": [
                    {"attr": {"cpu": "0", "state": "user"}, "value": 1000.0},
                    {"attr": {"cpu": "0", "state": "system"}, "value": 500.0},
                    {"attr": {"cpu": "0", "state": "idle"}, "value": 8000.0},
                ],
            },
            "cpu.logical.count": {"type": "gauge", "unit": "cpu", "points": [{"attr": {}, "value": 4}]},
            "cpu.run_queue": {
                "type": "gauge",
                "unit": "tasks",
                "points": [{"attr": {"source": "processor_queue"}, "value": None}],
            },
        },
        "system.memory": {
            "memory.usage": {
                "type": "gauge",
                "unit": "By",
                "points": [{"attr": {"state": "available"}, "value": 4000000000}],
            },
            "memory.limit": {"type": "gauge", "unit": "By", "points": [{"attr": {}, "value": 8589934592}]},
        },
        "system.disk": {
            "disk.io": {
                "type": "counter",
                "unit": "By",
                "points": [{"attr": {"device": _DISK0, "direction": "write"}, "value": 512000}],
            },
            "disk.pending_operations": {
                "type": "gauge",
                "unit": "operations",
                "points": [
                    {"attr": {"device": _DISK0}, "value": 1.5},
                    {"attr": {"device": _DISK1}, "value": 3.0},
                ],
            },
        },
        "system.network": {
            "network.io": {
                "type": "counter",
                "unit": "By",
                "points": [
                    {"attr": {"device": _NET_DEV, "direction": "receive"}, "value": 1000},
                    {"attr": {"device": _NET_DEV, "direction": "transmit"}, "value": 2000},
                ],
            },
            "network.link.speed": {
                "type": "gauge",
                "unit": "bit/s",
                "points": [{"attr": {"device": _NET_DEV}, "value": None}],
            },
        },
        "system.paging": {
            "paging.operations": {
                "type": "counter",
                "unit": "operations",
                "points": [{"attr": {"direction": "in"}, "value": None}],
            },
        },
        "system.pressure": None,
    }


def test_windows_inventory_wire_parses() -> None:
    data = InventoryInput.model_validate_json(json.dumps(_windows_inventory()))
    assert data.os_family == "windows"
    assert data.net_interfaces[0].id == _MAC
    assert data.net_interfaces[0].id_type == "mac"
    assert data.listen_ports[0].uid is None
    assert data.block_devices[0].name == "PhysicalDrive0"
    assert data.block_devices[0].id_type == "gptid"
    assert data.services == []
    assert data.lvm_vgs == []


def test_windows_inventory_to_dto_preserves_mac() -> None:
    data = InventoryInput.model_validate_json(json.dumps(_windows_inventory()))
    dto = to_inventory_create(data)
    assert dto.os_family == "windows"
    assert dto.mem_total_bytes == 8589934592
    ni = dto.net_interfaces[0]
    assert ni["id"] == _MAC
    assert ni["id_type"] == "mac"
    assert ni["name"] == "Ethernet0"
    assert ni["kind"] == "physical"
    assert ni["gateway"] == "10.0.0.1"
    assert ni["addresses"] == [{"address": "10.0.0.5", "prefix": 24, "family": "ipv4"}]


def test_net_interfaces_default_empty_when_absent() -> None:
    payload = _windows_inventory()
    del payload["net_interfaces"]
    data = InventoryInput.model_validate_json(json.dumps(payload))
    assert data.net_interfaces == []


def test_windows_metrics_wire_parses() -> None:
    data = MetricsInput.model_validate_json(json.dumps(_windows_metrics()))
    assert data.os_family == "windows"
    assert data.system_cpu is not None
    assert data.system_memory is not None
    assert data.system_disk is not None
    assert data.system_network is not None
    assert "cpu.time" in data.system_cpu
    states = {p.attr.get("state") for p in data.system_cpu["cpu.time"].points}
    assert "user" in states
    assert "idle" in states
    assert "iowait" not in states
    assert "steal" not in states
    assert data.system_pressure is None


def test_windows_metrics_to_dto() -> None:
    data = MetricsInput.model_validate_json(json.dumps(_windows_metrics()))
    dto = to_metric_create(data)
    assert dto.cpu_user_s == 1000.0
    assert dto.cpu_idle_s == 8000.0
    assert dto.cpu_iowait_s is None
    assert dto.cpu_steal_s is None
    assert dto.cpu_run_queue is None
    assert dto.mem_available_bytes == 4000000000
    assert dto.mem_cached_bytes is None
    assert dto.mem_buffered_bytes is None
    pending = {e.device_id: e.pending_ops for e in dto.disk_io}
    assert pending == {_DISK0: 1.5, _DISK1: 3.0}


def test_windows_metrics_saturation_measured_path() -> None:
    payload = _windows_metrics()
    payload["system.cpu"]["cpu.run_queue"]["points"] = [{"attr": {"source": "processor_queue"}, "value": 12.0}]
    payload["system.disk"]["disk.pending_operations"]["points"] = [{"attr": {"device": _DISK0}, "value": 0.5}]
    payload["system.paging"]["paging.operations"]["points"] = [{"attr": {"direction": "in"}, "value": 250000}]
    data = MetricsInput.model_validate_json(json.dumps(payload))
    dto = to_metric_create(data)
    assert dto.cpu_run_queue == 12.0
    assert dto.paging_in == 250000
    assert [e.pending_ops for e in dto.disk_io] == [0.5]


def test_long_net_interface_name_within_limit() -> None:
    payload = _windows_inventory()
    payload["net_interfaces"][0]["name"] = "Y" * 256
    data = InventoryInput.model_validate_json(json.dumps(payload))
    assert len(data.net_interfaces[0].name) == 256
