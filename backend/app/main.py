from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.broker import broker
from app.models.base import Base
from app.session import engine
from app.models import server_entity, metric_snapshot  # noqa: F401
from app.api.ingestion import router as ingestion_router
from app.api.query import router as query_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # FastAPI - RabbitMQ 간 소켓
    await broker.startup()
    yield
    await broker.shutdown()


app = FastAPI(title="ZConverter Assessment API", lifespan=lifespan)

app.include_router(ingestion_router)
app.include_router(query_router)


@app.get("/health")
async def health():
    return {"status": "ok"}