from contextlib import asynccontextmanager
from pathlib import Path

import aio_pika
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger
from prometheus_fastapi_instrumentator import Instrumentator

from assessment_engine.db.redis import close_pool
from assessment_engine.log_config import setup_logging
from assessment_engine.web.routers.api import api_router
from assessment_engine.web.routers.diagnostic_results import diagnostic_results_router
from assessment_engine.web.routers.diagnostics import diagnostics_router
from assessment_engine.web.routers.discovery import discovery_router
from assessment_engine.web.routers.exports import exports_router
from assessment_engine.web.routers.pages import pages_router
from assessment_engine.web.routers.payloads import payloads_router
from assessment_engine.web.routers.reports import reports_router
from assessment_engine.web.routers.tasks import tasks_router
from assessment_engine.web.settings import diagnostic_settings, web_settings

# Composition Root에서 log sink 단일 등록 — text(dev) vs json(prod) 분기 (LOG_FORMAT env).
setup_logging(web_settings.log_format)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # schema 관리는 모든 환경에서 Alembic — docker-compose `migrate` 서비스(init-container 패턴)가
    # postgres healthy 후 `alembic upgrade head` 1회 실행 후 종료. 본 lifespan은 schema 가정만 함.
    # web을 포함한 모든 앱 서비스는 `depends_on: migrate (service_completed_successfully)`로 그 뒤에 기동 (ADR 0005).
    logger.info("app_env={} — schema is Alembic-managed (entrypoint applied upgrade)", web_settings.app_env)

    # 진단 broker connection (ADR 0004) — consumer/worker와 동일 인자로 declare 의무 (#B3).
    # exchange type·DLX·큐 인자 mismatch 시 PRECONDITION_FAILED. DIRECT exchange + {exchange}.dlx 컨벤션.
    broker_conn = await aio_pika.connect_robust(diagnostic_settings.broker_url, timeout=10)
    broker_channel = await broker_conn.channel()
    dlx_name = f"{diagnostic_settings.rabbitmq_exchange}.dlx"
    dlx = await broker_channel.declare_exchange(
        dlx_name, aio_pika.ExchangeType.DIRECT, durable=True,
    )
    exchange = await broker_channel.declare_exchange(
        diagnostic_settings.rabbitmq_exchange,
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )
    routing_key = diagnostic_settings.diagnostic_routing_key
    dlq = await broker_channel.declare_queue(f"{routing_key}.dead", durable=True)
    await dlq.bind(dlx, routing_key=routing_key)
    queue = await broker_channel.declare_queue(
        routing_key,
        durable=True,
        arguments={
            "x-dead-letter-exchange":    dlx_name,
            "x-dead-letter-routing-key": routing_key,
            "x-message-ttl":             diagnostic_settings.diagnostic_queue_ttl_ms,
            "x-max-length":              diagnostic_settings.diagnostic_queue_max_len,
        },
    )
    await queue.bind(exchange, routing_key=routing_key)

    # 원격 작업 발행용 exchange. 동일 인자 재선언은 idempotent — consumer 가 먼저 declare 해도 안전.
    # agent.tasks.<machine_id> 머신별 큐는 task.install 발행 시점에 TaskService 가 동적 declare.
    await broker_channel.declare_exchange(
        diagnostic_settings.rabbitmq_task_exchange,
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )

    app.state.broker_conn = broker_conn
    app.state.broker_channel = broker_channel
    logger.info(
        "broker initialized — diagnostic_exchange={} task_exchange={}",
        diagnostic_settings.rabbitmq_exchange,
        diagnostic_settings.rabbitmq_task_exchange,
    )

    yield

    await broker_conn.close()
    await close_pool()


app = FastAPI(title="ZConverter Assessment Portal", lifespan=lifespan)

# Prometheus 계측 — HTTP request count·latency·error rate 자동.
# `/metrics` endpoint를 expose해 외부 Prometheus(인프라 책임)가 polling 수집.
# instrument() 호출 시점에 middleware 등록 → 모든 라우터에 자동 적용. expose()는 endpoint 등록.
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


@app.middleware("http")
async def disable_html_cache(request, call_next):
    """SSR(text/html) + dev 한정 static asset 에 `Cache-Control: no-store` 적용.

    HTML: 진단 발행 -> 결과 페이지 -> 뒤로가기 시점에 브라우저 HTTP cache·BFCache 로
    list 페이지가 stale HTML 그대로 복원되는 회귀 회피.

    Static (JS/CSS): dev 환경 한정. `?v={{ asset_v }}` query bust 가 있으나 브라우저가 disk
    cache hit 우선시하는 경우 옛 JS 가 잔존 — dev 에서 코드 hot reload 후 클라이언트도 즉시
    새 JS 받게 강제. prod 는 cdn·long-cache 운영을 위해 본 분기 비활성.
    """
    response = await call_next(request)
    ct = response.headers.get("content-type", "")
    if ct.startswith("text/html") or web_settings.app_env == "dev" and request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

app.include_router(pages_router)
app.include_router(api_router)
app.include_router(discovery_router)
app.include_router(tasks_router)
app.include_router(diagnostics_router)
app.include_router(diagnostic_results_router)
app.include_router(reports_router)
app.include_router(exports_router)
app.include_router(payloads_router)


@app.get("/health")
async def health():
    return {"status": "ok"}