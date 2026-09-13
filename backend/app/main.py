import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.db import Base, engine
from app.poller import poll_for_externally_filed_bugs
from app.routers import api, webhooks, ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    poll_task = asyncio.create_task(poll_for_externally_filed_bugs())
    yield
    poll_task.cancel()


app = FastAPI(title="WhipGuard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api.router)
app.include_router(webhooks.router)
app.include_router(ws.router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
