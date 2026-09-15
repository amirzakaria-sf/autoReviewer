"""Admin-only surfaces: user approval queue, role management, and real
usage numbers -- every figure here traces to an actual CouncilRun row
(app/council_runs.py) or a real count query, never an estimate presented as
a measurement (plan.md's own "never fabricate a capability" principle,
applied to metrics instead of features).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import require_admin
from app.enums import AccessRequestStatus, UserRole, UserStatus
from app.integrations import email_client
from app.models import AccessRequest, AuditLog, Fix, Issue, Repo, User
from app.security import sign_invite_token

logger = logging.getLogger("whipguard.admin")

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


def _user_dict(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "role": user.role.value,
        "status": user.status.value,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def _access_request_dict(req: AccessRequest) -> dict:
    return {
        "id": str(req.id),
        "name": req.name,
        "email": req.email,
        "reason": req.reason,
        "status": req.status.value,
        "created_at": req.created_at.isoformat() if req.created_at else None,
        "decided_at": req.decided_at.isoformat() if req.decided_at else None,
        "decision_reason": req.decision_reason,
        "invite_consumed": req.invite_consumed_at is not None,
    }


@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db)):
    users = (await db.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
    return [_user_dict(u) for u in users]


@router.post("/users/{user_id}/role")
async def set_user_role(user_id: uuid.UUID, body: dict, db: AsyncSession = Depends(get_db)):
    role = body.get("role")
    if role not in ("admin", "member"):
        raise HTTPException(400, "role must be 'admin' or 'member'")
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "user not found")
    user.role = UserRole(role)
    await db.commit()
    return {"ok": True, "user": _user_dict(user)}


@router.post("/users/{user_id}/deactivate")
async def deactivate_user(user_id: uuid.UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if user_id == admin.id:
        raise HTTPException(400, "cannot deactivate your own account")
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "user not found")
    user.status = UserStatus.DEACTIVATED
    await db.commit()
    return {"ok": True, "user": _user_dict(user)}


@router.post("/users/{user_id}/reactivate")
async def reactivate_user(user_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "user not found")
    user.status = UserStatus.ACTIVE
    await db.commit()
    return {"ok": True, "user": _user_dict(user)}


@router.get("/access-requests")
async def list_access_requests(db: AsyncSession = Depends(get_db)):
    requests = (await db.execute(select(AccessRequest).order_by(AccessRequest.created_at.desc()))).scalars().all()
    return [_access_request_dict(r) for r in requests]


@router.post("/access-requests/{request_id}/approve")
async def approve_access_request(request_id: uuid.UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    access_request = await db.get(AccessRequest, request_id)
    if not access_request:
        raise HTTPException(404, "access request not found")
    if access_request.status != AccessRequestStatus.PENDING:
        raise HTTPException(409, f"already {access_request.status.value}")

    access_request.status = AccessRequestStatus.APPROVED
    access_request.decided_by = admin.id
    access_request.decided_at = datetime.now(timezone.utc)
    await db.commit()

    invite_token = sign_invite_token(access_request.id)
    if email_client.smtp_configured():
        try:
            subject, html, text_body = email_client.build_invite_email(access_request.name, invite_token)
            await asyncio.to_thread(email_client.send_email, access_request.email, subject, html, text_body)
        except Exception:
            logger.exception("failed to send invite email for access request %s", access_request.id)

    return {"ok": True, "request": _access_request_dict(access_request)}


@router.post("/access-requests/{request_id}/reject")
async def reject_access_request(request_id: uuid.UUID, body: dict, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    access_request = await db.get(AccessRequest, request_id)
    if not access_request:
        raise HTTPException(404, "access request not found")
    if access_request.status != AccessRequestStatus.PENDING:
        raise HTTPException(409, f"already {access_request.status.value}")

    reason = str(body.get("reason", "")).strip()
    access_request.status = AccessRequestStatus.REJECTED
    access_request.decided_by = admin.id
    access_request.decided_at = datetime.now(timezone.utc)
    access_request.decision_reason = reason or None
    await db.commit()

    if email_client.smtp_configured():
        try:
            subject, html, text_body = email_client.build_access_declined_email(access_request.name, reason)
            await asyncio.to_thread(email_client.send_email, access_request.email, subject, html, text_body)
        except Exception:
            logger.exception("failed to send decline email for access request %s", access_request.id)

    return {"ok": True, "request": _access_request_dict(access_request)}


@router.get("/overview")
async def admin_overview(db: AsyncSession = Depends(get_db)):
    user_counts = dict(
        (await db.execute(select(User.status, func.count()).group_by(User.status))).all()
    )
    request_counts = dict(
        (await db.execute(select(AccessRequest.status, func.count()).group_by(AccessRequest.status))).all()
    )
    repo_count = (await db.execute(select(func.count()).select_from(Repo))).scalar_one()
    issue_count = (await db.execute(select(func.count()).select_from(Issue))).scalar_one()
    fix_count = (await db.execute(select(func.count()).select_from(Fix))).scalar_one()

    return {
        "users": {
            "active": user_counts.get(UserStatus.ACTIVE, 0),
            "deactivated": user_counts.get(UserStatus.DEACTIVATED, 0),
        },
        "access_requests": {
            "pending": request_counts.get(AccessRequestStatus.PENDING, 0),
            "approved": request_counts.get(AccessRequestStatus.APPROVED, 0),
            "rejected": request_counts.get(AccessRequestStatus.REJECTED, 0),
        },
        "repos": repo_count,
        "issues": issue_count,
        "fixes": fix_count,
    }


@router.get("/usage")
async def usage_stats(db: AsyncSession = Depends(get_db)):
    """Real numbers from council_runs -- every jury/worker model call any
    graph makes (app/council_runs.record_council_run, wired into
    azure_client._chat). No cost-in-dollars figure: this app doesn't know
    which Azure pricing tier applies to each deployment, and a guessed
    dollar figure presented as a measurement is worse than no figure."""
    since = datetime.now(timezone.utc) - timedelta(days=30)

    totals = (
        await db.execute(
            text(
                """
                SELECT count(*) AS calls,
                       coalesce(sum(input_tokens), 0) AS input_tokens,
                       coalesce(sum(cached_input_tokens), 0) AS cached_input_tokens,
                       coalesce(sum(output_tokens), 0) AS output_tokens,
                       coalesce(avg(latency_ms), 0) AS avg_latency_ms
                FROM council_runs
                WHERE created_at >= :since
                """
            ),
            {"since": since},
        )
    ).mappings().first()

    by_role = (
        await db.execute(
            text(
                """
                SELECT role,
                       count(*) AS calls,
                       coalesce(sum(input_tokens), 0) AS input_tokens,
                       coalesce(sum(output_tokens), 0) AS output_tokens,
                       coalesce(avg(latency_ms), 0) AS avg_latency_ms
                FROM council_runs
                WHERE created_at >= :since
                GROUP BY role
                ORDER BY calls DESC
                """
            ),
            {"since": since},
        )
    ).mappings().all()

    by_day = (
        await db.execute(
            text(
                """
                SELECT date_trunc('day', created_at) AS day,
                       count(*) AS calls,
                       coalesce(sum(input_tokens + output_tokens), 0) AS total_tokens
                FROM council_runs
                WHERE created_at >= :since
                GROUP BY 1
                ORDER BY 1
                """
            ),
            {"since": since},
        )
    ).mappings().all()

    return {
        "window_days": 30,
        "totals": dict(totals) if totals else {},
        "by_role": [dict(r) for r in by_role],
        "by_day": [{"day": r["day"].date().isoformat(), "calls": r["calls"], "total_tokens": r["total_tokens"]} for r in by_day],
    }


DEPLOY_LOG_PATH = Path(settings.workspace_root) / "deploy.log"


@router.post("/redeploy")
async def trigger_redeploy(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Rebuilds and recreates backend+frontend from the source already on
    disk (see deploy.sh for exactly what that means and its one real risk:
    the backend recreating itself out from under the request that triggered
    it). Launched detached so it outlives this request regardless of what
    happens to this process next."""
    if not settings.repo_root:
        raise HTTPException(400, "REPO_ROOT is not configured on this container")

    script = Path(settings.repo_root) / "deploy.sh"
    if not script.exists():
        raise HTTPException(400, f"deploy.sh not found at {script}")

    db.add(
        AuditLog(
            actor_user_id=str(admin.id),
            actor_surface="admin_dashboard",
            action="redeploy_triggered",
            target_type="deployment",
            target_id="whipguard",
            metadata_json={"by": admin.email},
        )
    )
    await db.commit()

    try:
        subprocess.Popen(
            ["bash", str(script)],
            cwd=settings.repo_root,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        logger.exception("failed to launch deploy.sh")
        raise HTTPException(500, "could not start the redeploy script")

    return {
        "ok": True,
        "message": "Redeploy started. The backend may briefly disconnect while it restarts -- poll /api/admin/redeploy/status.",
    }


@router.get("/redeploy/status")
async def redeploy_status():
    """Getting ANY response from this at all already proves the backend is
    reachable -- during the brief window where the sibling container
    (deploy.sh) is swapping the backend for a freshly-built one, this
    endpoint simply won't answer, which the frontend's poll loop reads as
    "still swapping," not as an error to surface.

    Keys off the HANDOFF line, not deploy.sh's own final "finished OK" --
    when deploy.sh is launched detached (this trigger, not a human running
    it from their own CLI), the copy of the script doing the handoff is
    running INSIDE the old container, which gets torn down moments later;
    it never survives to print a final line, health-check loop included.
    The handoff line is the last thing guaranteed to be written by the
    process that's still alive to write it -- build and the frontend swap
    already succeeded by the time it's reached (set -euo pipefail would
    have aborted the script, and the log, before then otherwise)."""
    if not DEPLOY_LOG_PATH.exists():
        return {"running": False, "log": None}
    log_text = DEPLOY_LOG_PATH.read_text()[-8000:]
    dispatched = "handing the backend swap" in log_text or "deploy finished OK" in log_text
    return {"running": not dispatched, "succeeded": dispatched, "log": log_text}


_LATEST_EVAL_REPORT: dict | None = None
_EVAL_RUNNING = False


@router.post("/eval/run")
async def trigger_eval(admin: User = Depends(require_admin)):
    """Detection-only pass (plan.md §13.4): mechanical detectors against
    controlled worktrees, checked against a fixed answer key. No model
    calls, so no cost -- see app/eval_harness.py's own docstring for why a
    blended score is refused. Runs as a background task since the sandboxed
    categories (ui/backend/accessibility) each do a real npm install."""
    global _EVAL_RUNNING
    if _EVAL_RUNNING:
        raise HTTPException(409, "an eval run is already in progress")

    from app.config import settings
    from app.eval_harness import run_detection_eval

    async def _run():
        global _LATEST_EVAL_REPORT, _EVAL_RUNNING
        _EVAL_RUNNING = True
        try:
            report = await asyncio.to_thread(run_detection_eval, settings.fixture_repo)
            _LATEST_EVAL_REPORT = report.to_dict()
        except Exception:
            logger.exception("eval run failed")
            _LATEST_EVAL_REPORT = {"error": "eval run raised -- see backend logs"}
        finally:
            _EVAL_RUNNING = False

    asyncio.create_task(_run())
    return {"ok": True, "message": "Eval run started -- poll GET /api/admin/eval/latest."}


@router.get("/eval/latest")
async def latest_eval():
    return {"running": _EVAL_RUNNING, "report": _LATEST_EVAL_REPORT}
