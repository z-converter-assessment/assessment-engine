"""InventoryInput.net_interfaces 내부 IP 파싱 회귀 가드 (wire v2 — #B).

내부 네트워크는 net_interfaces(안정키 id=MAC + 다중 addresses[])로 발행된다. IP/서브넷 수용은
NetAddressInfo 의 bare address(ip_address 형식 검증) + prefix(0~128) + family(ipv4|ipv6)로 이관됐다.
본 테스트는 (a) IPv4 address 수용 (b) IPv6 address 수용 (c) 형식 불량 address·prefix reject
(d) ip_external bare/CIDR 수용을 고정한다. NetAddressInfo 를 좁히는 변경(예: address CIDR 강제,
family 축소)이 수용을 깨면 본 테스트가 잡는다.
"""

import pytest
from pydantic import ValidationError

from assessment_engine.consumer.schemas import InventoryInput


def _iface(
    *,
    name: str = "eth0",
    address: str = "10.0.1.15",
    prefix: int = 24,
    family: str = "ipv4",
    kind: str = "physical",
    gateway: str = "10.0.1.1",
) -> dict:
    """v2 net_interface — address/prefix/family 는 중첩 addresses[] 로. 단일 주소 편의 빌더."""
    return {
        "name": name,
        "id": "52:54:00:12:34:56",
        "id_type": "mac",
        "kind": kind,
        "gateway": gateway,
        "addresses": [{"address": address, "prefix": prefix, "family": family}],
    }


def _inventory(net_interfaces: list[dict], ip_external=None) -> dict:
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
    # address 는 bare 문자열 그대로 보존 (prefix 는 별도 필드 — 변환·정규화 0).
    addrs = [(i.addresses[0].address, i.addresses[0].prefix) for i in m.net_interfaces]
    assert addrs == [("10.0.1.15", 24), ("172.16.0.3", 16)]
    assert m.net_interfaces[0].addresses[0].family == "ipv4"


def test_ipv6_interface_accepted():
    # Windows/Linux agent 는 IPv6 interface(fe80::1/64 등)도 발행 — 형식 검증 통과.
    m = InventoryInput.model_validate(_inventory([_iface(address="fe80::1", prefix=64, family="ipv6")]))
    a = m.net_interfaces[0].addresses[0]
    assert a.address == "fe80::1"
    assert a.prefix == 64
    assert a.family == "ipv6"


def test_malformed_interface_address_rejected():
    with pytest.raises(ValidationError):
        InventoryInput.model_validate(_inventory([_iface(address="not-an-ip")]))


def test_out_of_range_prefix_rejected():
    # prefix 는 0~128 (ge=0 le=128) — 범위 밖은 reject.
    with pytest.raises(ValidationError):
        InventoryInput.model_validate(_inventory([_iface(prefix=129)]))


def test_ip_external_bare_and_cidr_accepted():
    # ip_external 은 bare IP·CIDR 둘 다 수용 (ip_interface 형식 검증만).
    m = InventoryInput.model_validate(_inventory([_iface()], ip_external=["54.123.45.67", "203.0.113.0/24"]))
    assert m.ip_external == ["54.123.45.67", "203.0.113.0/24"]
