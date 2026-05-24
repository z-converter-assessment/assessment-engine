"""discovery SSH 도달성 probe — probe_ssh endpoint + ProbeRequest 검증 단위 테스트.

외부 의존(TCP connect)은 asyncio.open_connection mock — 본 repo 내부 모듈 mock 0 (외부 의존만).
asyncio_mode=auto (pyproject) — async def 자동 실행.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from assessment_engine.web.routers.discovery import ProbeRequest, probe_ssh


def _mock_conn(banner: bytes):
    """asyncio.open_connection 반환 (reader, writer) mock. reader.read -> banner, writer.close sync."""
    reader = MagicMock()
    reader.read = AsyncMock(return_value=banner)
    writer = MagicMock()  # close()는 sync MagicMock (호출만)
    writer.wait_closed = AsyncMock(return_value=None)
    return reader, writer


# ─── probe_ssh — 도달 성공 ─────────────────────────────────────────────────


async def test_probe_ssh_reachable_with_banner():
    reader, writer = _mock_conn(b"SSH-2.0-OpenSSH_8.7\r\n")
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        resp = await probe_ssh(ProbeRequest(target="host.docker.internal", port=22))
    assert resp.reachable is True
    assert resp.banner == "SSH-2.0-OpenSSH_8.7"
    assert resp.error is None


async def test_probe_ssh_reachable_no_banner():
    """포트는 열렸으나 SSH banner 미수신 (non-SSH·포트 노킹) — reachable 유지, banner None."""
    reader, writer = _mock_conn(b"")
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        resp = await probe_ssh(ProbeRequest(target="h", port=22))
    assert resp.reachable is True
    assert resp.banner is None


async def test_probe_ssh_banner_read_error_still_reachable():
    """connect 성공 = reachable. banner read 실패(OSError)해도 reachable 유지."""
    reader = MagicMock()
    reader.read = AsyncMock(side_effect=OSError())
    writer = MagicMock()
    writer.wait_closed = AsyncMock(return_value=None)
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        resp = await probe_ssh(ProbeRequest(target="h", port=22))
    assert resp.reachable is True
    assert resp.banner is None


# ─── probe_ssh — 실패 분류 ─────────────────────────────────────────────────


async def test_probe_ssh_connection_refused():
    with patch("asyncio.open_connection", AsyncMock(side_effect=ConnectionRefusedError())):
        resp = await probe_ssh(ProbeRequest(target="h", port=22))
    assert resp.reachable is False
    assert resp.banner is None
    assert "refused" in (resp.error or "")


async def test_probe_ssh_timeout():
    with patch("asyncio.open_connection", AsyncMock(side_effect=TimeoutError())):
        resp = await probe_ssh(ProbeRequest(target="h", port=22))
    assert resp.reachable is False
    assert "timeout" in (resp.error or "")


async def test_probe_ssh_unreachable_oserror():
    """DNS 실패·no route to host — OSError -> host unreachable."""
    with patch("asyncio.open_connection", AsyncMock(side_effect=OSError("no route"))):
        resp = await probe_ssh(ProbeRequest(target="h", port=22))
    assert resp.reachable is False
    assert "unreachable" in (resp.error or "")


# ─── ProbeRequest 검증 (진입점 단일 검증 #F3) ──────────────────────────────


def test_probe_request_default_port_is_22():
    assert ProbeRequest(target="h").port == 22


@pytest.mark.parametrize(
    "target",
    [
        "192.168.0.42",  # IPv4
        "host.docker.internal",  # hostname
        "engine.internal",  # FQDN
    ],
)
def test_probe_request_accepts_ip_and_hostname(target):
    assert ProbeRequest(target=target, port=52222).target == target


@pytest.mark.parametrize(
    "target",
    [
        "host;rm -rf",  # shell metachar
        "has space",  # 공백
        "",  # 빈 문자열 (min_length=1)
    ],
)
def test_probe_request_rejects_invalid_target(target):
    with pytest.raises(ValueError):
        ProbeRequest(target=target, port=22)


@pytest.mark.parametrize("port", [0, 65536, -1])
def test_probe_request_rejects_out_of_range_port(port):
    with pytest.raises(ValueError):
        ProbeRequest(target="h", port=port)
