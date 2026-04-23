import pytest
from pydantic import ValidationError

from consumer.schemas import ServerMetricInput

VALID = {
    "hostname": "server01",
    "nproc": 4,
    "mem_total_mb": 16384,
    "disks": [{"name": "vda", "size": "30G"}],
    "ip": {"internal": ["10.0.0.1"], "external": ["1.2.3.4"]},
}


def test_valid_payload_parses():
    m = ServerMetricInput(**VALID)
    assert m.hostname == "server01"
    assert m.nproc == 4
    assert m.mem_total_mb == 16384
    assert m.disks[0].name == "vda"
    assert str(m.ip.internal[0]) == "10.0.0.1"
    assert str(m.ip.external[0]) == "1.2.3.4"


def test_empty_hostname_fails():
    with pytest.raises(ValidationError):
        ServerMetricInput(**{**VALID, "hostname": ""})


def test_hostname_too_long_fails():
    with pytest.raises(ValidationError):
        ServerMetricInput(**{**VALID, "hostname": "a" * 256})


def test_nproc_zero_fails():
    with pytest.raises(ValidationError):
        ServerMetricInput(**{**VALID, "nproc": 0})


def test_nproc_negative_fails():
    with pytest.raises(ValidationError):
        ServerMetricInput(**{**VALID, "nproc": -1})


def test_mem_total_mb_zero_fails():
    with pytest.raises(ValidationError):
        ServerMetricInput(**{**VALID, "mem_total_mb": 0})


@pytest.mark.parametrize("size", ["30G", "100M", "512B", "1G"])
def test_disk_size_valid_patterns(size):
    m = ServerMetricInput(**{**VALID, "disks": [{"name": "vda", "size": size}]})
    assert m.disks[0].size == size


@pytest.mark.parametrize("size", ["30GB", "100mb", "1TB", "abc", "G30", ""])
def test_disk_size_invalid_patterns_fail(size):
    with pytest.raises(ValidationError):
        ServerMetricInput(**{**VALID, "disks": [{"name": "vda", "size": size}]})


def test_disk_name_empty_fails():
    with pytest.raises(ValidationError):
        ServerMetricInput(**{**VALID, "disks": [{"name": "", "size": "30G"}]})


def test_invalid_internal_ip_fails():
    bad = {**VALID, "ip": {"internal": ["not-an-ip"], "external": ["1.2.3.4"]}}
    with pytest.raises(ValidationError):
        ServerMetricInput(**bad)


def test_invalid_external_ip_fails():
    bad = {**VALID, "ip": {"internal": ["10.0.0.1"], "external": ["999.999.999.999"]}}
    with pytest.raises(ValidationError):
        ServerMetricInput(**bad)


def test_empty_disk_list_allowed():
    m = ServerMetricInput(**{**VALID, "disks": []})
    assert m.disks == []


def test_empty_ip_lists_allowed():
    m = ServerMetricInput(**{**VALID, "ip": {"internal": [], "external": []}})
    assert m.ip.internal == []
    assert m.ip.external == []