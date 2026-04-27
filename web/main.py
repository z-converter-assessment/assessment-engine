from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text

from db.models.base import Base
from db.session import engine
from db.models import server_inventory, server_metrics  # noqa: F401
from db.models import server_storage_detail, server_network_detail  # noqa: F401
from db.redis import close_pool
from web.routers.pages import pages_router
from web.routers.api import api_router


@asynccontextmanager
async def lifespan(a: FastAPI):
    # [개발 전용] 기동할 때마다 스키마를 전부 날리고 새로 만든다.
    # TimescaleDB hypertable의 내부 청크 의존성 때문에 drop_all 대신 DROP SCHEMA CASCADE 사용.
    #
    # [프로덕션] 이 블록 전체를 제거하고 Alembic 마이그레이션으로 대체한다.
    #   - 최초 배포: `alembic upgrade head` (CI/CD 파이프라인 또는 배포 스크립트에서 실행)
    #   - CREATE EXTENSION은 최초 1회 Alembic 마이그레이션 파일에 수동으로 작성
    #   - create_hypertable도 server_metrics 생성 마이그레이션 파일에 수동으로 작성
    #   - 이후 스키마 변경은 `alembic revision --autogenerate` → 검토 → `alembic upgrade head`
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "SELECT create_hypertable('server_metrics', 'collected_at')"
        ))
    yield
    await close_pool()


app = FastAPI(title="ZConverter Assessment Portal", lifespan=lifespan)

app.include_router(pages_router)
app.include_router(api_router)


@app.get("/health")
async def health():
    return {"status": "ok"}