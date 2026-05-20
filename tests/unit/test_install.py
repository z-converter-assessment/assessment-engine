"""install 발행 헬퍼 + Pydantic 검증 단위 테스트.

bundle/script 빌드는 self-host endpoint 제거(`web/routers/payloads.py` 삭제) 이후 사라짐 —
download 정보는 agent contract 따라 운영자 입력 ZDM host + ZDM_PACKAGE_* env 로 조립.
"""
import pytest
from pydantic import ValidationError

from assessment_engine.web.routers.tasks import InstallRequest
from assessment_engine.web.services.task_service import _extract_zdm_host


@pytest.mark.parametrize("raw, expected", [
    ("192.168.3.94",                       "192.168.3.94"),
    ("192.168.3.94:8080",                  "192.168.3.94:8080"),
    ("zdm.internal",                       "zdm.internal"),
    ("http://192.168.3.94",                "192.168.3.94"),
    ("http://192.168.3.94/",               "192.168.3.94"),
    ("https://zdm.example.com",            "zdm.example.com"),
    ("HTTP://Zdm.Example.Com",             "Zdm.Example.Com"),
    ("http://host.lima.internal:8000/p",   "host.lima.internal:8000"),
])
def test_extract_zdm_host(raw, expected):
    assert _extract_zdm_host(raw) == expected


# InstallRequest Pydantic 검증

def test_install_request_minimal_zdm_none():
    """zdm_ip / zdm_user 미지정 시 None — router fallback 흐름."""
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


@pytest.mark.parametrize("ip", [
    "192.168.3.94",
    "10.0.0.1",
    "127.0.0.1",
    "::1",            # IPv6 loopback
    "2001:db8::1",    # IPv6
])
def test_install_request_accepts_valid_ips(ip):
    req = InstallRequest(
        target_public_ids=["00000000-0000-0000-0000-000000000001"],
        zdm_ip=ip,
        zdm_user="admin@zconverter.com",
    )
    assert req.zdm_ip == ip


@pytest.mark.parametrize("bad_value", [
    "192.168.3.94 ; rm -rf /",  # shell injection — 공백·세미콜론 hostname/URL 둘 다 fail
    "host name with space",     # 공백 hostname invalid
    "host\tname\ttab",          # 제어문자
    "http://host ; rm",         # URL prefix 라도 공백 포함
])
def test_install_request_rejects_invalid_zdm_target(bad_value):
    """IP/hostname/URL 셋 다 fail 인 비정상 형식만 차단 (validator 신규 의도)."""
    with pytest.raises(ValidationError):
        InstallRequest(
            target_public_ids=["00000000-0000-0000-0000-000000000001"],
            zdm_ip=bad_value,
            zdm_user="admin@zconverter.com",
        )


@pytest.mark.parametrize("ok_value", [
    "192.168.3.94",                       # IP
    "zdm.internal",                       # hostname
    "host.lima.internal",                 # 다중 label hostname
    "http://host.lima.internal:8000/p",   # HTTP URL
    "https://zdm.example.com",            # HTTPS URL
])
def test_install_request_accepts_ip_hostname_url(ok_value):
    """IP/hostname/HTTP(S) URL 셋 다 허용 — 운영 환경마다 ZDM 형태 다름."""
    req = InstallRequest(
        target_public_ids=["00000000-0000-0000-0000-000000000001"],
        zdm_ip=ok_value,
        zdm_user="admin@zconverter.com",
    )
    assert req.zdm_ip == ok_value


def test_install_request_empty_ip_becomes_none():
    """빈 문자열은 None 으로 정규화 — router 가 settings default 로 fallback."""
    req = InstallRequest(
        target_public_ids=["00000000-0000-0000-0000-000000000001"],
        zdm_ip="",
        zdm_user="admin@zconverter.com",
    )
    assert req.zdm_ip is None


@pytest.mark.parametrize("bad_user", [
    "no-at-sign",
    "missing@domain",
    "@no-local.com",
    "user with space@example.com",
])
def test_install_request_rejects_invalid_emails(bad_user):
    with pytest.raises(ValidationError):
        InstallRequest(
            target_public_ids=["00000000-0000-0000-0000-000000000001"],
            zdm_ip="192.168.3.94",
            zdm_user=bad_user,
        )


def test_install_request_requires_at_least_one_target():
    with pytest.raises(ValidationError):
        InstallRequest(target_public_ids=[])
