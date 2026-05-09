from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger
from sqlalchemy import text

from assessment_engine.config import web_settings
from assessment_engine.db.models import (  # noqa: F401
    server_disk_io,
    server_inventory,
    server_inventory_history,
    server_metrics,
    server_mount_usage,
    server_net_io,
    task,
)
from assessment_engine.db.models.base import Base
from assessment_engine.db.redis import close_pool
from assessment_engine.db.session import engine
from assessment_engine.web.routers.api import api_router
from assessment_engine.web.routers.discovery import discovery_router
from assessment_engine.web.routers.exports import exports_router
from assessment_engine.web.routers.pages import pages_router
from assessment_engine.web.routers.tasks import tasks_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # dev/staging: 빠른 반복을 위해 자동 schema 생성. 없는 테이블만 만들고 데이터 보존.
    # 완전 초기화가 필요하면 `docker compose down -v` 후 재기동.
    #
    # prod: schema 관리는 Alembic이 담당. 본 블록은 skip — 기동 시 schema가 부재하면
    #       마이그레이션을 먼저 적용해야 함. (트레이드오프 T4)
    if web_settings.app_env == "prod":
        logger.info("app_env=prod — schema bootstrap skipped (Alembic-managed)")
    else:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"))
            await conn.run_sync(Base.metadata.create_all)
            hypertables = (
                "server_metrics",
                "server_disk_io",
                "server_net_io",
                "server_mount_usage",
                "server_inventory_history",
            )
            for table in hypertables:
                await conn.execute(text(
                    f"SELECT create_hypertable('{table}', 'collected_at', if_not_exists => true)"
                ))
        logger.info("app_env={} — schema bootstrap done", web_settings.app_env)

    yield

    await close_pool()


app = FastAPI(title="ZConverter Assessment Portal", lifespan=lifespan)

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

app.include_router(pages_router)
app.include_router(api_router)
app.include_router(discovery_router)
app.include_router(tasks_router)
app.include_router(exports_router)


@app.get("/health")
async def health():
    return {"status": "ok"}