"""InventoryInput.ip_internal CIDR 수용 회귀 가드 (agent payload v3.4 — #B).

agent v3.4+ 가 ip_internal 을 CIDR 표기("10.0.1.15/24")로 발행한다. validate_ip_list 가 옛 ip_address
(bare IP 만 허용) 로 남아 있으면 CIDR 입력이 ValidationError -> inventory 메시지가 통째로 DLQ.
본 테스트는 (a) CIDR 수용 (b) bare IP 하위호환(옛 agent) (c) 형식 불량 reject 를 고정한다.
"""

import pytest
from pydantic import ValidationError

from assessment_engine.consumer.schemas import InventoryInput


def _inventory(ip_internal: list[str], ip_external=None) -> dict:
    return {
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
        "ip_internal": ip_internal,
        "ip_external": ip_external,
        "mac_addresses": ["02:42:ac:11:00:03"],
        "disks": [],
        "mounts": [],
        "services": [],
        "listen_ports": [],
    }


def test_cidr_ip_internal_accepted():
    m = InventoryInput.model_validate(_inventory(["10.0.1.15/24", "172.16.0.3/16"]))
    # raw 문자열 그대로 보존 (변환·정규화 0 — repository ARRAY(Text) 저장).
    assert m.ip_internal == ["10.0.1.15/24", "172.16.0.3/16"]


def test_bare_ip_internal_backward_compatible():
    # 옛 agent (v3.3 이하) bare IP 도 계속 통과.
    m = InventoryInput.model_validate(_inventory(["10.0.1.15"]))
    assert m.ip_internal == ["10.0.1.15"]


def test_ip_external_bare_accepted():
    m = InventoryInput.model_validate(_inventory(["10.0.1.15/24"], ip_external=["54.123.45.67"]))
    assert m.ip_external == ["54.123.45.67"]


def test_malformed_ip_rejected():
    with pytest.raises(ValidationError):
        InventoryInput.model_validate(_inventory(["not-an-ip"]))


def test_ipv6_cidr_accepted():
    # Windows agent 는 IPv6 CIDR(fe80::/64 등)도 발행 — 형식 검증 통과(소비 단계에서 토폴로지가 IPv4만 선별).
    m = InventoryInput.model_validate(_inventory(["fe80::1/64"]))
    assert m.ip_internal == ["fe80::1/64"]
