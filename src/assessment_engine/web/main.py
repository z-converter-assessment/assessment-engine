import time
from contextlib import asynccontextmanager
from pathlib import Path

import aio_pika
import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger

from assessment_engine.cache.redis import close_pool, get_redis
from assessment_engine.db.repositories.collect_repository import CollectRepository
from assessment_engine.db.repositories.diagnostic_repository import DiagnosticRepository
from assessment_engine.db.repositories.query.query_repository import QueryRepository
from assessment_engine.db.session import AsyncSessionLocal
from assessment_engine.log_config import setup_logging
from assessment_engine.web.report_worker import lifespan_worker
from assessment_engine.web.routers.api import api_router
from assessment_engine.web.routers.exports import exports_router
from assessment_engine.web.routers.pages import pages_router
from assessment_engine.web.routers.reports import reference_router, reports_router
from assessment_engine.web.routers.right_sizing import right_sizing_router
from assessment_engine.web.routers.tasks import tasks_router
from assessment_engine.web.services.diagnostic_service import DiagnosticService
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.web.settings import diagnostic_settings, web_settings
from assessment_engine.web.task_reaper import lifespan_task_reaper
from assessment_engine.web.templating import templates


@asynccontextmanager
async def _query_service_factory():
    """워커용 QueryService 팩토리 — job 마다 독립 세션(생성 쿼리 트랜잭션 분리). composition root."""
    async with AsyncSessionLocal() as session:
        yield QueryService(QueryRepository(session), get_redis())


# Composition Root에서 log sink 단일 등록 — text(dev) vs json(prod) 분기 (LOG_FORMAT env).
setup_logging(web_settings.log_format)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # schema 관리는 모든 환경에서 Alembic — docker-compose `migrate` 서비스(init-container 패턴)가
    # postgres healthy 후 `alembic upgrade head` 1회 실행 후 종료. 본 lifespan은 schema 가정만 함.
    # web을 포함한 모든 앱 서비스는 `depends_on: migrate (service_completed_successfully)`로 그 뒤에 기동 (ADR 0005).
    logger.info("app_env={} — schema is Alembic-managed (entrypoint applied upgrade)", web_settings.app_env)
    # dev 한정 정적 자원 캐시 무효화 신호 — 미들웨어가 매 요청 asset_v 재발급(F4: app_env 판정은 lifespan 에서만).
    app.state.dev_assets = web_settings.app_env == "dev"

    # task.install 발행용 broker connection — consumer 와 동일 인자로 declare 의무 (rabbitmq.md 토폴로지).
    # exchange type mismatch 시 PRECONDITION_FAILED. DIRECT exchange 컨벤션.
    broker_conn = await aio_pika.connect_robust(diagnostic_settings.broker_url, timeout=10)
    broker_channel = await broker_conn.channel()

    # 원격 작업 발행용 exchange. 동일 인자 재선언은 idempotent — consumer 가 먼저 declare 해도 안전.
    # agent.tasks.<agent_id> 머신별 큐는 task.install 발행 시점에 TaskService 가 동적 declare.
    await broker_channel.declare_exchange(
        diagnostic_settings.rabbitmq_task_exchange,
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )

    app.state.broker_conn = broker_conn
    app.state.broker_channel = broker_channel
    logger.info(
        "broker initialized — task_exchange={}",
        diagnostic_settings.rabbitmq_task_exchange,
    )

    # ZDM 메타 fetch 용 httpx async client — connect 5s, total 120s (44MB GET 가정).
    # 단일 client 인스턴스를 TCP 재사용 위해 lifespan 에서 생성·shutdown 에서 close.
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=web_settings.zdm_meta_connect_timeout_sec,
            read=web_settings.zdm_meta_total_timeout_sec,
            write=web_settings.zdm_meta_total_timeout_sec,
            pool=web_settings.zdm_meta_connect_timeout_sec,
        ),
        follow_redirects=False,
    )
    app.state.http_client = http_client

    # 비동기 보고서 생성 워커 — pending job 을 claim 해 백그라운드 생성(emit 는 즉시 job_id 반환).
    # graceful(F11): lifespan 이탈 시 새 claim 중단 + 진행 중 1건 drain, 미완은 stale 복구가 회수.
    diag_service = DiagnosticService(
        session_factory=AsyncSessionLocal,
        diagnostic_repo_factory=DiagnosticRepository,
    )
    # install task reaper — deadline 경과 pending 을 emit 무관하게 능동 timeout 전이(F6 관측성).
    async with (
        lifespan_worker(
            diag_service=diag_service,
            query_service_factory=_query_service_factory,
            poll_interval_sec=web_settings.report_worker_poll_interval_sec,
            stale_seconds=web_settings.report_worker_stale_seconds,
            shutdown_timeout_sec=web_settings.report_worker_shutdown_timeout_sec,
        ),
        lifespan_task_reaper(
            session_factory=AsyncSessionLocal,
            collect_repo_factory=CollectRepository,
            interval_sec=web_settings.install_reaper_interval_sec,
            shutdown_timeout_sec=web_settings.install_reaper_shutdown_timeout_sec,
        ),
    ):
        yield

    await http_client.aclose()
    await broker_conn.close()
    await close_pool()


app = FastAPI(title="ZConverter Assessment Portal", lifespan=lifespan)


@app.middleware("http")
async def disable_html_cache(request, call_next):
    """SSR(text/html) + dev 한정 static asset 에 `Cache-Control: no-store` 적용.

    HTML: 진단 발행 -> 결과 페이지 -> 뒤로가기 시점에 브라우저 HTTP cache·BFCache 로
    list 페이지가 stale HTML 그대로 복원되는 회귀 회피.

    Static (JS/CSS): dev 환경 한정. hot reload 후 클라이언트가 즉시 새 JS 받게 강제.
    prod 는 cdn·long-cache 운영을 위해 본 분기 비활성.
    """
    dev = getattr(request.app.state, "dev_assets", False)
    # dev — 매 요청 asset_v 재발급: 정적 자원 URL(`?v=`)이 매번 바뀌어 브라우저 disk cache·304 까지 회피.
    if dev:
        templates.env.globals["asset_v"] = format(int(time.time() * 1000), "x")
    response = await call_next(request)
    ct = response.headers.get("content-type", "")
    if ct.startswith("text/html") or (dev and request.url.path.startswith("/static/")):
        response.headers["Cache-Control"] = "no-store"
    return response


_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

app.include_router(pages_router)
app.include_router(api_router)
app.include_router(tasks_router)
app.include_router(reports_router)
app.include_router(reference_router)
app.include_router(exports_router)
app.include_router(right_sizing_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
