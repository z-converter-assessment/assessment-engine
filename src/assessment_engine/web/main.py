"""web 프로세스 조립 — FastAPI app · lifespan 외부 자원 · 라우터 등록 (#F4 Composition Root)."""

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import aio_pika
import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.datastructures import MutableHeaders

from assessment_engine.cache.redis import close_pool
from assessment_engine.db.session import dispose_engine
from assessment_engine.log_config import setup_logging
from assessment_engine.web.routers.api import api_router, fleet_router
from assessment_engine.web.routers.assessment import assessment_router
from assessment_engine.web.routers.exports import exports_router
from assessment_engine.web.routers.pages import pages_router
from assessment_engine.web.routers.reports import reference_router, reports_router
from assessment_engine.web.routers.right_sizing import right_sizing_router
from assessment_engine.web.routers.tasks import tasks_router
from assessment_engine.web.settings import get_diagnostic_settings, get_web_settings
from assessment_engine.web.templating.setup import env_globals

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


@asynccontextmanager
async def lifespan(app: FastAPI):

    setup_logging(get_web_settings().log_format, get_web_settings().log_level)
    # schema 는 compose `migrate` 서비스가 이미 올려둔 상태를 전제한다 (docs/guides/migrate.md).
    logger.info("app_env={} — schema is Alembic-managed (entrypoint applied upgrade)", get_web_settings().app_env)

    app.state.dev_assets = get_web_settings().app_env == "dev"

    broker_conn = await aio_pika.connect_robust(get_diagnostic_settings().broker_url, timeout=10)
    broker_channel = await broker_conn.channel()

    # consumer 와 같은 인자로 declare 해야 한다 — 재선언 자체는 idempotent 지만 인자가 어긋나면

    await broker_channel.declare_exchange(
        get_diagnostic_settings().rabbitmq_task_exchange,
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )

    app.state.broker_conn = broker_conn
    app.state.broker_channel = broker_channel
    logger.info(
        "broker initialized — task_exchange={}",
        get_diagnostic_settings().rabbitmq_task_exchange,
    )

    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=get_web_settings().zdm_meta_connect_timeout_sec,
            read=get_web_settings().zdm_meta_total_timeout_sec,
            write=get_web_settings().zdm_meta_total_timeout_sec,
            pool=get_web_settings().zdm_meta_connect_timeout_sec,
        ),
        follow_redirects=False,
    )
    app.state.http_client = http_client

    yield

    await http_client.aclose()
    await broker_conn.close()
    await dispose_engine()
    await close_pool()


app = FastAPI(title="ZConverter Assessment Portal", lifespan=lifespan)


class DisableHtmlCache:
    """SSR(text/html) + dev 한정 static asset 에 `Cache-Control: no-store` 를 얹는 ASGI 미들웨어.

    HTML 은 진단 발행 -> 결과 -> 뒤로가기에서 브라우저 HTTP cache·BFCache 가 stale 한 list 페이지를
    되살리는 회귀를 막는다. dev 의 JS/CSS 는 hot reload 직후 새 파일을 받게 하는 용도라 prod 에서는
    cdn·long-cache 를 위해 꺼진다.

    `BaseHTTPMiddleware`(@app.middleware) 를 쓰지 않는다 — 그쪽은 응답을 별도 task 로 감싸 스트리밍
    응답과 contextvar 전파에 제약이 생긴다. 헤더 한 줄을 얹는 데 그 비용을 낼 이유가 없다.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        dev = getattr(app.state, "dev_assets", False)
        # asset_v 를 매 요청 재발급해 `?v=` 가 바뀐다 — 브라우저 disk cache·304 까지 피한다.
        if dev:
            env_globals["asset_v"] = format(int(time.time() * 1000), "x")
        is_static = scope.get("path", "").startswith("/static/")

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                # raw 리스트에 append 하면 Cache-Control 이 중복으로 나간다 — 대입(replace)으로 얹는다.
                content_type = headers.get("content-type", "")
                if content_type.startswith("text/html") or (dev and is_static):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_header)


app.add_middleware(DisableHtmlCache)

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

app.include_router(pages_router)
app.include_router(api_router)
app.include_router(fleet_router)
app.include_router(tasks_router)
app.include_router(reports_router)
app.include_router(reference_router)
app.include_router(exports_router)
app.include_router(right_sizing_router)
app.include_router(assessment_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
