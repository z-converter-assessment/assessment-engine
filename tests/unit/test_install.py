"""install OS 분기 + ZDM 인자 + bundle 구조 단위 테스트.

본 변경 핵심 로직(외부 의존 없는 pure function · Pydantic 검증 · tar bundle 빌드) 검증.
DB / broker 의존 흐름(create_install_tasks 전체)은 integration 범위.
"""
import hashlib
import io
import tarfile

import pytest
from pydantic import ValidationError

from assessment_engine.web.routers.payloads import (
    INSTALL_BUNDLE_SHA256,
    INSTALL_BUNDLE_SIZE,
    INSTALL_SCRIPT_LINUX,
    INSTALL_SCRIPT_WINDOWS,
    _build_install_bundle,
)
from assessment_engine.web.routers.tasks import InstallRequest
from assessment_engine.web.services.task_service import (
    _is_windows,
    _select_install_script,
)

# OS 판단 helper

@pytest.mark.parametrize("os_id, expected", [
    ("windows", True),
    ("Windows", True),
    ("WINDOWS", True),
    ("windows_server", True),
    ("win32nt", True),
    ("win", True),
    ("ubuntu", False),
    ("centos", False),
    ("rhel", False),
    ("debian", False),
    ("alpine", False),
    ("", False),
    (None, False),
])
def test_is_windows(os_id, expected):
    assert _is_windows(os_id) is expected


@pytest.mark.parametrize("os_id, expected_script", [
    ("windows", INSTALL_SCRIPT_WINDOWS),
    ("Windows_Server_2022", INSTALL_SCRIPT_WINDOWS),
    ("ubuntu", INSTALL_SCRIPT_LINUX),
    ("centos", INSTALL_SCRIPT_LINUX),
    (None, INSTALL_SCRIPT_LINUX),  # null/미지정은 Linux default
    ("", INSTALL_SCRIPT_LINUX),
])
def test_select_install_script(os_id, expected_script):
    assert _select_install_script(os_id) == expected_script


# install bundle 빌드

def test_bundle_contains_both_scripts():
    """bundle tar 안에 install.sh + install.ps1 두 파일 모두 포함."""
    bundle = _build_install_bundle()
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as tar:
        names = set(tar.getnames())
    assert names == {INSTALL_SCRIPT_LINUX, INSTALL_SCRIPT_WINDOWS}


def test_bundle_script_contents_have_zdm_args():
    """두 스크립트 본문이 -s ZDM_IP -u ZDM_USER 인자 처리 코드 포함."""
    bundle = _build_install_bundle()
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as tar:
        linux_body = tar.extractfile(INSTALL_SCRIPT_LINUX).read().decode("utf-8")
        windows_body = tar.extractfile(INSTALL_SCRIPT_WINDOWS).read().decode("utf-8")

    # Linux: -s / -u case 처리 + ZDM_IP 변수 + ZConverter_CloudSource_Setup_Linux.tar.gz fetch
    assert "-s) ZDM_IP=" in linux_body
    assert "-u) ZDM_USER=" in linux_body
    assert "ZConverter_CloudSource_Setup_Linux.tar.gz" in linux_body

    # Windows: param block + ZDM_IP 할당 + Setup_Windows.exe fetch + Start-Process
    assert "param(" in windows_body
    assert "$ZDM_IP" in windows_body
    assert "ZConverter_CloudSource_Setup_Windows.exe" in windows_body
    assert "Start-Process" in windows_body


def test_bundle_script_modes():
    """Linux sh는 실행 권한 (0o755), Windows ps1은 일반 파일 (0o644)."""
    bundle = _build_install_bundle()
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as tar:
        members = {m.name: m for m in tar.getmembers()}
    assert members[INSTALL_SCRIPT_LINUX].mode == 0o755
    assert members[INSTALL_SCRIPT_WINDOWS].mode == 0o644


def test_bundle_sha256_deterministic():
    """모듈 캐시값과 즉시 재빌드 sha256 일치 — mtime=0 고정으로 같은 코드면 같은 bytes."""
    rebuilt = _build_install_bundle()
    assert hashlib.sha256(rebuilt).hexdigest() == INSTALL_BUNDLE_SHA256
    assert len(rebuilt) == INSTALL_BUNDLE_SIZE


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
