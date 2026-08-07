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
    # log sink 단일 등록 — text(dev) vs json(prod) 분기 (LOG_FORMAT env). 모듈 스코프가 아니라 여기서
    # 부른다 — import 만으로 설정을 읽으면 값 없이는 import 조차 못 한다(consumer·worker 동일).
    setup_logging(get_web_settings().log_format, get_web_settings().log_level)
    # schema 관리는 모든 환경에서 Alembic — docker-compose `migrate` 서비스(init-container 패턴)가
    # postgres healthy 후 `alembic upgrade head` 1회 실행 후 종료. 본 lifespan은 schema 가정만 함.
    # web을 포함한 모든 앱 서비스는 `depends_on: migrate (service_completed_successfully)`로 그 뒤에 기동.
    logger.info("app_env={} — schema is Alembic-managed (entrypoint applied upgrade)", get_web_settings().app_env)
    # dev 한정 정적 자원 캐시 무효화 신호 — 미들웨어가 매 요청 asset_v 재발급(F4: app_env 판정은 lifespan 에서만).
    app.state.dev_assets = get_web_settings().app_env == "dev"

    # task.install 발행용 broker connection — consumer 와 동일 인자로 declare 의무 (rabbitmq.md 토폴로지).
    # exchange type mismatch 시 PRECONDITION_FAILED. DIRECT exchange 컨벤션.
    broker_conn = await aio_pika.connect_robust(get_diagnostic_settings().broker_url, timeout=10)
    broker_channel = await broker_conn.channel()

    # 원격 작업 발행용 exchange. 동일 인자 재선언은 idempotent — consumer 가 먼저 declare 해도 안전.
    # agent.tasks.<agent_id> 머신별 큐는 task.install 발행 시점에 TaskService 가 동적 declare.
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

    # ZDM 메타 fetch 용 httpx async client — connect 5s, total 120s (44MB GET 가정).
    # 단일 client 인스턴스를 TCP 재사용 위해 lifespan 에서 생성·shutdown 에서 close.
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

    # 비동기 보고서 생성·install task reaper 는 전용 워커 프로세스(assessment_engine.worker)가 담당 —
    # web 은 HTTP 요청만 처리(발행은 pending job enqueue 후 즉시 job_id 반환, 생성은 워커가 claim).
    yield

    await http_client.aclose()
    await broker_conn.close()
    await dispose_engine()
    await close_pool()


app = FastAPI(title="ZConverter Assessment Portal", lifespan=lifespan)


class DisableHtmlCache:
    """SSR(text/html) + dev 한정 static asset 에 `Cache-Control: no-store` 를 얹는 ASGI 미들웨어.

    HTML: 진단 발행 -> 결과 페이지 -> 뒤로가기 시점에 브라우저 HTTP cache·BFCache 로 list 페이지가
    stale HTML 그대로 복원되는 회귀 회피.

    Static (JS/CSS): dev 환경 한정. hot reload 후 클라이언트가 즉시 새 JS 받게 강제.
    prod 는 cdn·long-cache 운영을 위해 본 분기 비활성.

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
        # dev — 매 요청 asset_v 재발급: 정적 자원 URL(`?v=`)이 매번 바뀌어 브라우저 disk cache·304 까지 회피.
        if dev:
            env_globals["asset_v"] = format(int(time.time() * 1000), "x")
        is_static = scope.get("path", "").startswith("/static/")

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                # ASGI 규약상 헤더 이름은 소문자로 온다. raw 리스트에 append 하면 Cache-Control 이
                # 중복으로 나가므로 반드시 대입(replace)으로 얹는다.
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
