from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from assessment_engine.consumer.schemas import InventoryInput

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject


def _iface(
    *,
    name: str = "eth0",
    address: str = "10.0.1.15",
    prefix: int = 24,
    family: str = "ipv4",
    kind: str = "physical",
    gateway: str = "10.0.1.1",
) -> JsonObject:
    return {
        "name": name,
        "id": "52:54:00:12:34:56",
        "id_type": "mac",
        "kind": kind,
        "gateway": gateway,
        "addresses": [{"address": address, "prefix": prefix, "family": family}],
    }


def _inventory(net_interfaces: list[JsonObject], ip_external: list[str] | None = None) -> JsonObject:
    return {
        "schema_version": "1.0",
        "message_type": "inventory",
        "agent_id": "00000000-0000-4000-8000-000000000001",
        "composite_id": "a" * 64,
        "machine_id": "12345678-1234-1234-1234-123456789abc",
        "agent_version": "1.0.0",
        "collected_at": "2026-06-01T00:00:00Z",
        "hostname": "host-01",
        "message_id": "00000000-0000-4000-8000-000000000001",
        "agent_started_at": "2026-06-01T00:00:00Z",
        "boot_time": "2026-05-31T00:00:00Z",
        "os_family": "linux",
        "os_id": "ubuntu",
        "os_version": "22.04",
        "os_codename": "jammy",
        "kernel_version": "5.15.0-101-generic",
        "cpu_cores": 4,
        "cpu_model": "AMD EPYC",
        "mem_total_bytes": 8 * 1024**3,
        "net_interfaces": net_interfaces,
        "ip_external": ip_external,
        "block_devices": [],
        "lvm_vgs": [],
        "services": [],
        "listen_ports": [],
    }


def test_interface_address_prefix_accepted():
    m = InventoryInput.model_validate(
        _inventory([_iface(address="10.0.1.15", prefix=24), _iface(name="eth1", address="172.16.0.3", prefix=16)])
    )
    addrs = [(i.addresses[0].address, i.addresses[0].prefix) for i in m.net_interfaces]
    assert addrs == [("10.0.1.15", 24), ("172.16.0.3", 16)]
    assert m.net_interfaces[0].addresses[0].family == "ipv4"


def test_ipv6_interface_accepted():
    m = InventoryInput.model_validate(_inventory([_iface(address="fe80::1", prefix=64, family="ipv6")]))
    a = m.net_interfaces[0].addresses[0]
    assert a.address == "fe80::1"
    assert a.prefix == 64
    assert a.family == "ipv6"


def test_malformed_interface_address_rejected():
    with pytest.raises(ValidationError):
        InventoryInput.model_validate(_inventory([_iface(address="not-an-ip")]))


def test_out_of_range_prefix_rejected():
    with pytest.raises(ValidationError):
        InventoryInput.model_validate(_inventory([_iface(prefix=129)]))


def test_ip_external_bare_and_cidr_accepted():
    m = InventoryInput.model_validate(_inventory([_iface()], ip_external=["54.123.45.67", "203.0.113.0/24"]))
    assert m.ip_external == ["54.123.45.67", "203.0.113.0/24"]
