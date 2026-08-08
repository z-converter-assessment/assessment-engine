from typing import TYPE_CHECKING, Any

import pytest

from assessment_engine.web.main import app
from assessment_engine.web.templating.setup import ENGINE_VERSION
from tests.http.digest import html_digest
from tests.http.seed import ANCHOR, public_id

if TYPE_CHECKING:
    from httpx import AsyncClient

_ANCHOR_Q = ANCHOR.isoformat().replace("+00:00", "Z")
_SEED_ID = public_id(1)

EXCLUDED = {
    ("POST", "/api/tasks/install"),
    ("POST", "/reports/environment/emit"),
    ("POST", "/reports/servers/emit"),
    ("POST", "/api/exports/inventory"),
    ("GET", "/openapi.json"),
}

PARAMS: dict[str, str] = {
    "/api/servers/{server_id}/metrics/chart": "?metric_type=cpu.usage_percent&time_range=24h",
    "/api/servers/environment/metrics-chart": "?metric_type=cpu.usage_percent&time_range=24h",
    "/api/host-search": "?q=host",
    "/api/tasks": f"?server_public_id={_SEED_ID}",
    "/api/assessment": f"?end={_ANCHOR_Q}",
    "/api/right-sizing": f"?end={_ANCHOR_Q}",
    "/reports/environment": f"?anchor_at={_ANCHOR_Q}",
    "/reports/servers": f"?ids={_SEED_ID}&anchor_at={_ANCHOR_Q}",
    "/servers/{server_id}/report": f"?anchor_at={_ANCHOR_Q}",
    "/environment/assessment": f"?anchor_at={_ANCHOR_Q}",
}


def _endpoints() -> list[tuple[str, str]]:
    spec: dict[str, Any] = app.openapi()
    found: list[tuple[str, str]] = []
    for path, ops in spec["paths"].items():
        found.extend((method.upper(), path) for method in ops if method.upper() in {"GET", "POST"})
    return sorted(found)


def _url(path: str) -> str:
    return path.replace("{server_id}", _SEED_ID).replace("{task_id}", _SEED_ID).replace(
        "{job_id}", _SEED_ID
    ) + PARAMS.get(path, "")


def _slug(method: str, path: str) -> str:
    body = path.strip("/").replace("/", "_").replace("{", "").replace("}", "") or "root"
    return f"{method.lower()}_{body}"


@pytest.mark.parametrize(("method", "path"), [p for p in _endpoints() if p not in EXCLUDED])
async def test_endpoint_snapshot(client: AsyncClient, snapshot: Any, method: str, path: str) -> None:
    resp = await client.request(method, _url(path))
    content_type = resp.headers.get("content-type", "")
    captured: dict[str, Any] = {
        "status": resp.status_code,
        "content_type": content_type.split(";")[0],
        "cache_control": resp.headers.get("cache-control"),
    }
    if content_type.startswith("text/html"):
        captured["digest"] = html_digest(resp.text)
    else:
        captured["body"] = resp.text
    snapshot(_slug(method, path), captured)


@pytest.mark.parametrize(
    ("name", "url", "expected"),
    [
        ("not_found_server", "/servers/00000000-0000-4000-8000-000000000999", 404),
        ("invalid_uuid", "/servers/not-a-uuid", 422),
        ("unknown_metric_type", "/api/servers/environment/metrics-chart?metric_type=nope", 422),
    ],
)
async def test_error_paths(client: AsyncClient, snapshot: Any, name: str, url: str, expected: int) -> None:
    resp = await client.get(url)
    assert resp.status_code == expected, f"{url} -> {resp.status_code}"
    snapshot(f"error_{name}", {"status": resp.status_code, "body": resp.text[:2000]})


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("realtime", "/environment/realtime?fragment=nonsense"),
        ("assessment", "/environment/assessment?fragment=nonsense"),
        ("servers", "/servers?fragment=nonsense"),
    ],
)
async def test_unknown_fragment_falls_back_to_full_page(client: AsyncClient, name: str, url: str) -> None:
    resp = await client.get(url)

    assert resp.status_code == 200, name
    assert "<html" in resp.text.lower(), name


async def test_engine_version_is_rendered(client: AsyncClient) -> None:
    resp = await client.get("/")

    assert f"v{ENGINE_VERSION}" in resp.text
