import pytest
from pydantic import ValidationError

from assessment_engine.web.routers.tasks import InstallRequest
from assessment_engine.web.services.task_service import _extract_zdm_host


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("192.168.3.94", "192.168.3.94"),
        ("192.168.3.94:8080", "192.168.3.94:8080"),
        ("zdm.internal", "zdm.internal"),
        ("http://192.168.3.94", "192.168.3.94"),
        ("http://192.168.3.94/", "192.168.3.94"),
        ("https://zdm.example.com", "zdm.example.com"),
        ("HTTP://Zdm.Example.Com", "Zdm.Example.Com"),
        ("http://zdm.example.com:8000/p", "zdm.example.com:8000"),
    ],
)
def test_extract_zdm_host(raw: str, expected: str):
    assert _extract_zdm_host(raw) == expected


def test_install_request_minimal_zdm_none():
    req = InstallRequest(target_public_ids=["00000000-0000-0000-0000-000000000001"])
    assert req.zdm_ip is None
    assert req.zdm_user is None


def test_install_request_with_valid_zdm():
    req = InstallRequest(
        target_public_ids=["00000000-0000-0000-0000-000000000001"],
        zdm_ip="192.168.3.94",
        zdm_user="admin@zconverter.com",
    )
    assert req.zdm_ip == "192.168.3.94"
    assert req.zdm_user == "admin@zconverter.com"


@pytest.mark.parametrize(
    "ip",
    [
        "192.168.3.94",
        "10.0.0.1",
        "127.0.0.1",
    ],
)
def test_install_request_accepts_valid_ips(ip: str):
    req = InstallRequest(
        target_public_ids=["00000000-0000-0000-0000-000000000001"],
        zdm_ip=ip,
        zdm_user="admin@zconverter.com",
    )
    assert req.zdm_ip == ip


@pytest.mark.parametrize(
    "bad_value",
    [
        "192.168.3.94 ; rm -rf /",
        "host name with space",
        "host\tname\ttab",
        "http://host ; rm",
    ],
)
def test_install_request_rejects_invalid_zdm_target(bad_value: str):
    with pytest.raises(ValidationError):
        InstallRequest(
            target_public_ids=["00000000-0000-0000-0000-000000000001"],
            zdm_ip=bad_value,
            zdm_user="admin@zconverter.com",
        )


@pytest.mark.parametrize(
    "ok_value",
    [
        "192.168.3.94",
        "zdm.internal",
        "zdm.example.com",
        "http://zdm.example.com:8000/p",
        "https://zdm.example.com",
    ],
)
def test_install_request_accepts_ip_hostname_url(ok_value: str):
    req = InstallRequest(
        target_public_ids=["00000000-0000-0000-0000-000000000001"],
        zdm_ip=ok_value,
        zdm_user="admin@zconverter.com",
    )
    assert req.zdm_ip == ok_value


def test_install_request_empty_ip_becomes_none():
    req = InstallRequest(
        target_public_ids=["00000000-0000-0000-0000-000000000001"],
        zdm_ip="",
        zdm_user="admin@zconverter.com",
    )
    assert req.zdm_ip is None


@pytest.mark.parametrize(
    "bad_user",
    [
        "no-at-sign",
        "missing@domain",
        "@no-local.com",
        "user with space@example.com",
    ],
)
def test_install_request_rejects_invalid_emails(bad_user: str):
    with pytest.raises(ValidationError):
        InstallRequest(
            target_public_ids=["00000000-0000-0000-0000-000000000001"],
            zdm_ip="192.168.3.94",
            zdm_user=bad_user,
        )


def test_install_request_requires_at_least_one_target():
    with pytest.raises(ValidationError):
        InstallRequest(target_public_ids=[])
