from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text

from db.models.base import Base
from db.session import engine, AsyncSessionLocal
from db.models import server_inventory, server_metrics  # noqa: F401
from db.models import server_disk_io, server_net_io, server_mount_usage  # noqa: F401
from db.redis import close_pool
from web.routers.pages import pages_router
from web.routers.api import api_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 재시작 시 데이터를 보존한다 — 없는 테이블만 생성.
    # 완전 초기화가 필요하면 `docker compose down -v` 후 재기동.
    #
    # [프로덕션] 이 블록을 제거하고 Alembic 마이그레이션으로 대체한다.
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        for table in ("server_metrics", "server_disk_io", "server_net_io", "server_mount_usage"):
            await conn.execute(text(
                f"SELECT create_hypertable('{table}', 'collected_at', if_not_exists => true)"
            ))

    yield

    await close_pool()


app = FastAPI(title="ZConverter Assessment Portal", lifespan=lifespan)

app.include_router(pages_router)
app.include_router(api_router)


@app.get("/health")
async def health():
    return {"status": "ok"}