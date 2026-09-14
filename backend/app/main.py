import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.config import settings
from app.db import Base, engine
from app.poller import poll_for_externally_filed_bugs
from app.routers import api, auth, github, webhooks, ws

# Paths callable without a session: the login endpoint itself, GitHub/Slack's
# own servers (they don't carry this app's session cookie), and the container
# healthcheck.
_PUBLIC_PATHS = ("/api/auth/", "/api/webhooks/", "/api/slack/interactions", "/healthz")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ws.set_main_loop(asyncio.get_running_loop())
    poll_task = asyncio.create_task(poll_for_externally_filed_bugs())
    yield
    poll_task.cancel()


app = FastAPI(title="WhipGuard", lifespan=lifespan)

# Starlette wraps middleware LIFO -- the LAST one added becomes the OUTERMOST
# layer and runs FIRST on a request. SessionMiddleware has to be added after
# (and therefore run before, i.e. outside) require_session below, or
# request.session doesn't exist yet when require_session checks it -- found by
# actually running this once and reading the 500: "SessionMiddleware must be
# installed to access request.session", raised from inside require_session
# itself despite SessionMiddleware being registered, just in the wrong order.


@app.middleware("http")
async def require_session(request: Request, call_next):
    if request.url.path.startswith(_PUBLIC_PATHS) or not request.url.path.startswith("/api/"):
        return await call_next(request)
    if not request.session.get("authenticated"):
        return JSONResponse({"detail": "not authenticated"}, status_code=401)
    return await call_next(request)


app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://whip-guard.zakarias.in", "http://localhost:3300"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth.router)
app.include_router(api.router)
app.include_router(github.router)
app.include_router(webhooks.router)
app.include_router(ws.router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
