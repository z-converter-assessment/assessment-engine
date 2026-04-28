from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from web.deps import get_service
from web.main import app
from web.view_models import (
    NetworkDetailResponse,
    ServerDetailResponse,
    ServerListItem,
    StorageDetailResponse,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _make_service() -> MagicMock:
    svc = MagicMock()
    svc.list_servers = AsyncMock(return_value=[])
    svc.get_server = AsyncMock(return_value=None)
    svc.get_storage = AsyncMock(return_value=None)
    svc.get_network = AsyncMock(return_value=None)
    return svc


def _server_detail() -> ServerDetailResponse:
    return ServerDetailResponse(
        id=1, machine_id="mid", hostname="host-01", agent_version="1.0",
        os_id="ubuntu", os_version="22.04", os_codename=None, kernel_version=None,
        cpu_cores=4, cpu_model=None, mem_total_gb=8.0, swap_total_gb=None,
        boot_time=None, ip_internal=[], ip_external=None, disks=[], last_seen_at=_NOW,
    )


def _client(svc: MagicMock) -> TestClient:
    app.dependency_overrides[get_service] = lambda: svc
    return TestClient(app, raise_server_exceptions=True)


class TestHealth:
    def test_returns_ok(self):
        svc = _make_service()
        client = _client(svc)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestListServersPage:
    def test_returns_200_html(self):
        svc = _make_service()
        client = _client(svc)
        resp = client.get("/servers/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_default_params_passed_to_service(self):
        svc = _make_service()
        client = _client(svc)
        client.get("/servers/")
        svc.list_servers.assert_called_once_with(1, 20, None, None)

    def test_search_and_filter_params_forwarded(self):
        svc = _make_service()
        client = _client(svc)
        client.get("/servers/?search=web&is_online=true&page=2&limit=5")
        svc.list_servers.assert_called_once_with(2, 5, "web", True)


class TestGetServerPage:
    def test_not_found_returns_404(self):
        svc = _make_service()
        client = _client(svc)
        resp = client.get("/servers/999")
        assert resp.status_code == 404

    def test_found_returns_200_html(self):
        svc = _make_service()
        svc.get_server = AsyncMock(return_value=_server_detail())
        client = _client(svc)
        resp = client.get("/servers/1")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_server_id_passed_to_service(self):
        svc = _make_service()
        svc.get_server = AsyncMock(return_value=_server_detail())
        client = _client(svc)
        client.get("/servers/42")
        svc.get_server.assert_called_once_with(42)


class TestGetStoragePage:
    def test_not_found_returns_404(self):
        svc = _make_service()
        client = _client(svc)
        resp = client.get("/servers/1/storage")
        assert resp.status_code == 404

    def test_found_returns_200_html(self):
        svc = _make_service()
        svc.get_storage = AsyncMock(return_value=StorageDetailResponse(
            server_id=1, hostname="h", disks=[], mounts=[],
        ))
        client = _client(svc)
        resp = client.get("/servers/1/storage")
        assert resp.status_code == 200


class TestGetNetworkPage:
    def test_not_found_returns_404(self):
        svc = _make_service()
        client = _client(svc)
        resp = client.get("/servers/1/network")
        assert resp.status_code == 404

    def test_found_returns_200_html(self):
        svc = _make_service()
        svc.get_network = AsyncMock(return_value=NetworkDetailResponse(
            server_id=1, hostname="h", ip_internal=[], ip_external=None, interfaces=[],
        ))
        client = _client(svc)
        resp = client.get("/servers/1/network")
        assert resp.status_code == 200


class TestGetChartPage:
    def test_not_found_returns_404(self):
        svc = _make_service()
        client = _client(svc)
        resp = client.get("/servers/1/chart")
        assert resp.status_code == 404

    def test_found_returns_200_html(self):
        svc = _make_service()
        svc.get_server = AsyncMock(return_value=_server_detail())
        client = _client(svc)
        resp = client.get("/servers/1/chart")
        assert resp.status_code == 200