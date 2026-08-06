"""전 endpoint 응답을 스냅샷과 대조한다 — 리팩토링이 화면·API 를 바꾸지 않았다는 증거.

여기서 단언을 손으로 쓰지 않는 이유는 목적이 "지금 옳은가" 가 아니라 "어제와 같은가" 이기 때문이다.
옳은지는 기존 단위·통합 테스트가 본다.

엔드포인트 목록은 `app.openapi()` 에서 뽑는다. FastAPI 0.141 은 include 한 라우터를 `app.routes` 로
평탄화하지 않고 `_IncludedRouter` 로 감싸므로, 라우트를 순회하면 `/health` 하나만 보인다.
"""

from typing import TYPE_CHECKING, Any

import pytest

from assessment_engine.web.main import app
from tests.http.digest import html_digest
from tests.http.seed import ANCHOR, public_id

if TYPE_CHECKING:
    from httpx import AsyncClient

_ANCHOR_Q = ANCHOR.isoformat().replace("+00:00", "Z")
_SEED_ID = public_id(1)

# lifespan 을 돌리지 않으므로 `app.state.broker_channel`·`http_client` 에 기대는 경로는 뺀다.
# 발행 2건은 부수효과(job enqueue)가 있어 characterization 대상이 아니다.
EXCLUDED = {
    ("POST", "/api/tasks/install"),
    ("POST", "/reports/environment/emit"),
    ("POST", "/reports/servers/emit"),
    ("POST", "/api/exports/inventory"),
    ("GET", "/openapi.json"),
}

# path 파라미터와 시각 앵커를 채운다. 앵커가 없는 endpoint 는 시각 파생이 없는 것들이다.
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
    """오류 분기도 계약이다 — 상태코드와 detail 문구가 바뀌면 소비자가 깨진다."""
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
    """허용값 밖 `?fragment=` 는 422 가 아니라 full page 200 이다.

    OpenAPI 스키마는 enum 으로 좁게 선언하지만 서버는 그보다 넓게 받는다 — 이미 배포된 URL 을
    422 로 바꾸지 않기로 한 결정이고, 그 결정이 실제로 서 있는지는 여기서만 보인다.
    """
    resp = await client.get(url)

    assert resp.status_code == 200, name
    assert "<html" in resp.text.lower(), name
