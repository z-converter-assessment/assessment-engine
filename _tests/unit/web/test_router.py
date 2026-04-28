"""
라우터 행동 테스트.
- SSR: 서비스 None → 404, 값 있음 → 200 + text/html
- API: 서비스 None → 404, 값 있음 → 200 + JSON
- 서비스 메서드에 올바른 인자가 전달되는지
템플릿 렌더링은 서비스 모킹 후 TemplateResponse를 패치해 검증한다.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest_asyncio
from fastapi.responses import HTMLResponse

from web.deps import get_service
from web.main import app


def _mock_service() -> AsyncMock:
    s = AsyncMock()
    s.list_servers = AsyncMock(return_value=[])
    s.get_server = AsyncMock(return_value=None)
    s.get_storage = AsyncMock(return_value=None)
    s.get_network = AsyncMock(return_value=None)
    s.get_collection_status = AsyncMock(return_value=[])
    s.get_latest_metric = AsyncMock(return_value=None)
    s.get_metric_snapshots = AsyncMock(return_value=[])
    s.get_metric_chart = AsyncMock(return_value=[])
    return s


def _html() -> HTMLResponse:
    return HTMLResponse("<html></html>")


def _override(service: AsyncMock) -> None:
    app.dependency_overrides[get_service] = lambda: service


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealth:
    async def test_returns_ok(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /servers/  (SSR)
# ---------------------------------------------------------------------------

class TestListServersPage:
    async def test_returns_200_html(self, client):
        _override(_mock_service())
        with patch("web.routers.pages.templates.TemplateResponse", return_value=_html()):
            response = await client.get("/servers/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    async def test_default_pagination_params(self, client):
        service = _mock_service()
        _override(service)
        with patch("web.routers.pages.templates.TemplateResponse", return_value=_html()):
            await client.get("/servers/")
        service.list_servers.assert_called_once_with(1, 20, None, None)

    async def test_passes_query_params_to_service(self, client):
        service = _mock_service()
        _override(service)
        with patch("web.routers.pages.templates.TemplateResponse", return_value=_html()):
            await client.get("/servers/?page=2&limit=10&search=web&is_online=true")
        service.list_servers.assert_called_once_with(2, 10, "web", True)


# ---------------------------------------------------------------------------
# GET /servers/{server_id}  (SSR)
# ---------------------------------------------------------------------------

class TestGetServerPage:
    async def test_not_found_returns_404(self, client):
        _override(_mock_service())
        response = await client.get("/servers/9999")
        assert response.status_code == 404

    async def test_found_returns_200_html(self, client):
        service = _mock_service()
        service.get_server = AsyncMock(return_value=MagicMock())
        _override(service)
        with patch("web.routers.pages.templates.TemplateResponse", return_value=_html()):
            response = await client.get("/servers/1")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    async def test_passes_server_id_to_service(self, client):
        service = _mock_service()
        service.get_server = AsyncMock(return_value=MagicMock())
        _override(service)
        with patch("web.routers.pages.templates.TemplateResponse", return_value=_html()):
            await client.get("/servers/42")
        service.get_server.assert_called_once_with(42)


# ---------------------------------------------------------------------------
# GET /servers/{server_id}/storage  (SSR)
# ---------------------------------------------------------------------------

class TestGetStoragePage:
    async def test_not_found_returns_404(self, client):
        _override(_mock_service())
        response = await client.get("/servers/1/storage")
        assert response.status_code == 404

    async def test_found_returns_200_html(self, client):
        service = _mock_service()
        service.get_storage = AsyncMock(return_value=MagicMock())
        _override(service)
        with patch("web.routers.pages.templates.TemplateResponse", return_value=_html()):
            response = await client.get("/servers/1/storage")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


# ---------------------------------------------------------------------------
# GET /servers/{server_id}/network  (SSR)
# ---------------------------------------------------------------------------

class TestGetNetworkPage:
    async def test_not_found_returns_404(self, client):
        _override(_mock_service())
        response = await client.get("/servers/1/network")
        assert response.status_code == 404

    async def test_found_returns_200_html(self, client):
        service = _mock_service()
        service.get_network = AsyncMock(return_value=MagicMock())
        _override(service)
        with patch("web.routers.pages.templates.TemplateResponse", return_value=_html()):
            response = await client.get("/servers/1/network")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


# ---------------------------------------------------------------------------
# GET /api/v1/servers/{server_id}/collection-status  (API)
# ---------------------------------------------------------------------------

class TestCollectionStatusApi:
    async def test_empty_returns_200_json(self, client):
        _override(_mock_service())
        response = await client.get("/api/v1/servers/1/collection-status")
        assert response.status_code == 200
        assert response.json() == []

    async def test_passes_server_id(self, client):
        service = _mock_service()
        _override(service)
        await client.get("/api/v1/servers/7/collection-status")
        service.get_collection_status.assert_called_once_with(7)


# ---------------------------------------------------------------------------
# GET /api/v1/servers/{server_id}/metrics/latest  (API)
# ---------------------------------------------------------------------------

class TestLatestMetricApi:
    async def test_not_found_returns_404(self, client):
        _override(_mock_service())
        response = await client.get("/api/v1/servers/1/metrics/latest")
        assert response.status_code == 404

    async def test_found_returns_200(self, client):
        service = _mock_service()
        service.get_latest_metric = AsyncMock(return_value=MagicMock())
        _override(service)
        response = await client.get("/api/v1/servers/1/metrics/latest")
        assert response.status_code == 200

    async def test_passes_server_id(self, client):
        service = _mock_service()
        _override(service)
        await client.get("/api/v1/servers/3/metrics/latest")
        service.get_latest_metric.assert_called_once_with(3)


# ---------------------------------------------------------------------------
# GET /api/v1/servers/{server_id}/metrics/snapshots  (API)
# ---------------------------------------------------------------------------

class TestMetricSnapshotsApi:
    async def test_returns_200_empty_list(self, client):
        _override(_mock_service())
        response = await client.get("/api/v1/servers/1/metrics/snapshots")
        assert response.status_code == 200
        assert response.json() == []

    async def test_default_params(self, client):
        service = _mock_service()
        _override(service)
        await client.get("/api/v1/servers/1/metrics/snapshots")
        service.get_metric_snapshots.assert_called_once_with(1, None, 10)

    async def test_passes_limit(self, client):
        service = _mock_service()
        _override(service)
        await client.get("/api/v1/servers/1/metrics/snapshots?limit=5")
        service.get_metric_snapshots.assert_called_once_with(1, None, 5)


# ---------------------------------------------------------------------------
# GET /api/v1/servers/{server_id}/metrics/chart  (API)
# ---------------------------------------------------------------------------

class TestMetricChartApi:
    async def test_missing_metric_type_returns_422(self, client):
        _override(_mock_service())
        response = await client.get("/api/v1/servers/1/metrics/chart")
        assert response.status_code == 422

    async def test_returns_200_empty_list(self, client):
        _override(_mock_service())
        response = await client.get(
            "/api/v1/servers/1/metrics/chart?metric_type=cpu.usage_percent"
        )
        assert response.status_code == 200
        assert response.json() == []

    async def test_default_time_params(self, client):
        service = _mock_service()
        _override(service)
        await client.get("/api/v1/servers/1/metrics/chart?metric_type=cpu.usage_percent")
        service.get_metric_chart.assert_called_once_with(
            1, "cpu.usage_percent", None, "1h", "5m", "avg"
        )

    async def test_passes_all_params(self, client):
        service = _mock_service()
        _override(service)
        await client.get(
            "/api/v1/servers/1/metrics/chart"
            "?metric_type=cpu.usage_percent&dimension=cpu0&time_range=6h&bucket=1h&agg=max"
        )
        service.get_metric_chart.assert_called_once_with(
            1, "cpu.usage_percent", "cpu0", "6h", "1h", "max"
        )