import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.calibration import run_calibration_loop
from app.config import settings
from app.db import Base, async_session, engine
from app.enums import UserRole, UserStatus
from app.models import User
from app.poller import poll_for_externally_filed_bugs
from app.routers import admin, api, auth, email_actions, github, human_input, slack_connect, webhooks, ws
from app.security import decode_access_token, hash_password
from app.stuck_run_sweeper import sweep_stuck_runs

logger = logging.getLogger("whipguard.main")

# Paths callable without a valid access token: auth endpoints themselves,
# GitHub/Slack's own servers (they don't carry this app's cookies), a
# magic-link clicked straight from an inbox (its own signed/expiring token
# IS the credential -- see email_client.verify_action_token), and the
# healthcheck.
_PUBLIC_PATHS = ("/api/auth/", "/api/webhooks/", "/api/slack/interactions", "/api/email/action", "/healthz")


async def _seed_bootstrap_admin() -> None:
    """First-ever boot after the User/RefreshToken migration: without this,
    a deployment with zero User rows has no way to log in at all -- signup
    exists, but signing up creates the FIRST admin only if the DB already
    has none, so this just makes that same guarantee hold at container
    start too, using the same ADMIN_PASSWORD/NOTIFY_EMAIL operators already
    had configured, not a new credential to distribute."""
    if not settings.notify_email:
        return
    async with async_session() as db:
        any_user = (await db.execute(select(User.id).limit(1))).scalars().first()
        if any_user is not None:
            return
        db.add(
            User(
                email=settings.notify_email.strip().lower(),
                password_hash=hash_password(settings.admin_password),
                role=UserRole.ADMIN,
                status=UserStatus.ACTIVE,
            )
        )
        await db.commit()
        logger.info("bootstrapped initial admin account for %s", settings.notify_email)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _seed_bootstrap_admin()
    ws.set_main_loop(asyncio.get_running_loop())
    poll_task = asyncio.create_task(poll_for_externally_filed_bugs())
    sweep_task = asyncio.create_task(sweep_stuck_runs())
    calibration_task = asyncio.create_task(run_calibration_loop())
    yield
    poll_task.cancel()
    sweep_task.cancel()
    calibration_task.cancel()


app = FastAPI(title="WhipGuard", lifespan=lifespan)


@app.middleware("http")
async def require_session(request: Request, call_next):
    if request.url.path.startswith(_PUBLIC_PATHS) or not request.url.path.startswith("/api/"):
        return await call_next(request)

    token = request.cookies.get("access_token")
    decoded = decode_access_token(token) if token else None
    if not decoded:
        # Distinguishable from a generic 403 so the frontend knows a silent
        # POST /api/auth/refresh is worth trying before it gives up and
        # redirects to /login -- see frontend/lib/api.ts.
        return JSONResponse({"detail": "not authenticated", "code": "token_expired"}, status_code=401)

    request.state.user_id = decoded["sub"]
    request.state.role = decoded["role"]
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://whip-guard.zakarias.in", "http://localhost:3300"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(api.router)
app.include_router(email_actions.router)
app.include_router(github.router)
app.include_router(human_input.router)
app.include_router(slack_connect.router)
app.include_router(webhooks.router)
app.include_router(ws.router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
