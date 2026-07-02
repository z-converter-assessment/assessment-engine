"""InventoryInput.interfaces 내부 IP 파싱 회귀 가드 (agent payload v4 — #B).

agent v4 가 내부 네트워크를 ip_internal(CIDR 문자열 list)에서 interfaces(구조화
[{name, address, prefix, family, kind, gateway}])로 교체했다. 옛 CIDR 파싱 취지 —
내부 IP/서브넷을 reject 없이 수용 — 는 이제 InterfaceInfo 의 bare address(ip_address 형식 검증)
+ prefix(0~128) + family 로 이관됐다. 본 테스트는 (a) IPv4 interface 수용 (b) IPv6 interface 수용
(c) 형식 불량 address·prefix reject (d) ip_external bare/CIDR 하위호환을 고정한다.
InterfaceInfo 를 좁히는 변경(예: address 를 CIDR 강제, family 축소)이 수용을 깨면 본 테스트가 잡는다.
"""

import pytest
from pydantic import ValidationError

from assessment_engine.consumer.schemas import InventoryInput


def _iface(**over) -> dict:
    base = {
        "name": "eth0",
        "address": "10.0.1.15",
        "prefix": 24,
        "family": "ipv4",
        "kind": "physical",
        "gateway": "10.0.1.1",
    }
    base.update(over)
    return base


def _inventory(interfaces: list[dict], ip_external=None) -> dict:
    return {
        "agent_id": "00000000-0000-4000-8000-000000000001",
        "composite_id": "a" * 64,
        "machine_id": "12345678-1234-1234-1234-123456789abc",
        "agent_version": "4.0.0",
        "collected_at": "2026-06-01T00:00:00Z",
        "hostname": "host-01",
        "message_id": "00000000-0000-4000-8000-000000000001",
        "agent_started_at": "2026-06-01T00:00:00Z",
        "boot_time": "2026-05-31T00:00:00Z",
        "message_type": "inventory",
        "os_family": "linux",
        "os_id": "ubuntu",
        "os_version": "22.04",
        "os_codename": "jammy",
        "kernel_version": "5.15.0-101-generic",
        "cpu_cores": 4,
        "cpu_model": "AMD EPYC",
        "mem_total_kb": 8388608,
        "swap_total_kb": 0,
        "interfaces": interfaces,
        "ip_external": ip_external,
        "mac_addresses": ["02:42:ac:11:00:03"],
        "disks": [],
        "mounts": [],
        "services": [],
        "listen_ports": [],
    }


def test_interface_address_prefix_accepted():
    m = InventoryInput.model_validate(
        _inventory([_iface(address="10.0.1.15", prefix=24), _iface(name="eth1", address="172.16.0.3", prefix=16)])
    )
    # address 는 bare 문자열 그대로 보존 (prefix 는 별도 필드 — 변환·정규화 0).
    assert [(i.address, i.prefix) for i in m.interfaces] == [("10.0.1.15", 24), ("172.16.0.3", 16)]
    assert m.interfaces[0].family == "ipv4"


def test_ipv6_interface_accepted():
    # Windows/Linux agent 는 IPv6 interface(fe80::1/64 등)도 발행 — 형식 검증 통과.
    m = InventoryInput.model_validate(_inventory([_iface(address="fe80::1", prefix=64, family="ipv6")]))
    assert m.interfaces[0].address == "fe80::1"
    assert m.interfaces[0].prefix == 64
    assert m.interfaces[0].family == "ipv6"


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
