from unittest.mock import AsyncMock

import httpx
import pytest_asyncio

from web.main import app
from web.deps import get_service

# 서비스 목 객체 생성
def _make_service(list_servers=None, get_server=None, get_history=None):
    service = AsyncMock()
    service.list_servers = AsyncMock(return_value=list_servers or [])
    service.get_server = AsyncMock(return_value=get_server)
    service.get_history = AsyncMock(return_value=get_history)
    return service


@pytest_asyncio.fixture
async def client():
    # 실제 HTTP 소켓 없이 FastAPI 앱을 직접 호출하는 가짜 전송 계층, 네트워크를 거치지 않고 ASGI 인터페이스로 요청을 넣어줌
    transport = httpx.ASGITransport(app=app)
    # 비동기 HTTP 클라이언트
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    # FastAPI 앱의 의존성을 제거
    app.dependency_overrides.clear()


def override(service):
    # 의존성 주입
    app.dependency_overrides[get_service] = lambda: service


class TestHealth:
    async def test_returns_ok(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestListServers:
    async def test_returns_200(self, client):
        override(_make_service())
        response = await client.get("/servers/")
        assert response.status_code == 200

    async def test_returns_html(self, client):
        override(_make_service())
        response = await client.get("/servers/")
        assert "text/html" in response.headers["content-type"]


class TestGetServer:
    async def test_found_returns_200(self, client, server_item):
        override(_make_service(get_server=server_item))
        response = await client.get("/servers/" + str(server_item.id))
        assert response.status_code == 200

    async def test_not_found_returns_404(self, client):
        override(_make_service(get_server=None))
        response = await client.get("/servers/9999")
        assert response.status_code == 404

    async def test_returns_html(self, client, server_item):
        override(_make_service(get_server=server_item))
        response = await client.get("/servers/1")
        assert "text/html" in response.headers["content-type"]


class TestGetHistory:
    async def test_found_returns_200(self, client, server_dto, history_item):
        override(_make_service(get_history=(server_dto, [history_item])))
        response = await client.get("/servers/1/history")
        assert response.status_code == 200

    async def test_not_found_returns_404(self, client):
        override(_make_service(get_history=None))
        response = await client.get("/servers/9999/history")
        assert response.status_code == 404

    async def test_returns_html(self, client, server_dto, history_item):
        override(_make_service(get_history=(server_dto, [history_item])))
        response = await client.get("/servers/1/history")
        assert "text/html" in response.headers["content-type"]