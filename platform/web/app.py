from contextlib import asynccontextmanager
from fastapi import FastAPI

from db.models.base import Base
from db.session import engine
from db.models import server_entity, metric_snapshot  # noqa: F401
from web.api.router import router


@asynccontextmanager
async def lifespan(application: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="ZConverter Assessment Portal", lifespan=lifespan)

app.include_router(router)


@app.get("/health")
async def health():
    return {"status": "ok"}