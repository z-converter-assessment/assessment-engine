from contextlib import asynccontextmanager
from pathlib import Path

import aio_pika
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger

from assessment_engine.config import diagnostic_settings, web_settings
from assessment_engine.db.redis import close_pool
from assessment_engine.web.routers.api import api_router
from assessment_engine.web.routers.diagnostic_results import diagnostic_results_router
from assessment_engine.web.routers.diagnostics import diagnostics_router
from assessment_engine.web.routers.discovery import discovery_router
from assessment_engine.web.routers.exports import exports_router
from assessment_engine.web.routers.pages import pages_router
from assessment_engine.web.routers.tasks import tasks_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # schema 관리는 모든 환경에서 Alembic — docker-compose `migrate` 서비스(init-container 패턴)가
    # postgres healthy 후 `alembic upgrade head` 1회 실행 후 종료. 본 lifespan은 schema 가정만 함.
    # web을 포함한 모든 앱 서비스는 `depends_on: migrate (service_completed_successfully)`로 그 뒤에 기동 (ADR 0005).
    logger.info("app_env={} — schema is Alembic-managed (entrypoint applied upgrade)", web_settings.app_env)

    # AI 진단 broker connection (ADR 0004) — consumer/worker와 동일 인자로 declare 의무 (#B3).
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

    app.state.broker_conn = broker_conn
    app.state.broker_channel = broker_channel
    logger.info("diagnostic broker initialized — exchange={} routing_key={}",
                diagnostic_settings.rabbitmq_exchange, routing_key)

    yield

    await broker_conn.close()
    await close_pool()


app = FastAPI(title="ZConverter Assessment Portal", lifespan=lifespan)

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

app.include_router(pages_router)
app.include_router(api_router)
app.include_router(discovery_router)
app.include_router(tasks_router)
app.include_router(diagnostics_router)
app.include_router(diagnostic_results_router)
app.include_router(exports_router)


@app.get("/health")
async def health():
    return {"status": "ok"}