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
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import async_session, get_db
from app.deps import require_admin
from app import org_invites, orgs, kill_switch
from app.enums import AccessRequestStatus, OrgRole, Seniority, UserRole, UserStatus
from app.integrations import email_client
from app.models import AccessRequest, AuditLog, Fix, Issue, MemoryTrace, Repo, User
from app.security import sign_invite_token, sign_org_invite_token

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


class ApprovalIn(BaseModel):
    """Which organization to put them in.

    Optional only because the very first account on a deployment is approved
    before any organization exists. Every other approval should name one --
    an account in no organization sees no repositories and no issues, which
    is where this path used to end.
    """

    org_id: str = Field(default="")


@router.post("/access-requests/{request_id}/approve")
async def approve_access_request(
    request_id: uuid.UUID,
    body: ApprovalIn | None = None,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    access_request = await db.get(AccessRequest, request_id)
    if not access_request:
        raise HTTPException(404, "access request not found")
    if access_request.status != AccessRequestStatus.PENDING:
        raise HTTPException(409, f"already {access_request.status.value}")

    org_id = (body.org_id if body else "").strip()
    if org_id:
        from app.models import Organization

        try:
            organization = await db.get(Organization, uuid.UUID(org_id))
        except ValueError as error:
            raise HTTPException(400, "that is not a valid organization id") from error
        if organization is None:
            raise HTTPException(404, "that organization does not exist")
        access_request.org_id = organization.id

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


def _llm_cache_stats() -> dict:
    from app import llm_cache

    return llm_cache.stats()


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
        # A saving that leaves no trace cannot be told apart from a feature
        # nobody reached, so the retrieval/cache layer reports its own use.
        "llm_cache": await asyncio.to_thread(_llm_cache_stats),
        "memory_traces": (
            await db.execute(select(func.count()).select_from(MemoryTrace))
        ).scalar_one(),
        "kill_switch": kill_switch.status(),
    }


@router.get("/kill-switch")
async def get_kill_switch():
    return kill_switch.status()


@router.post("/kill-switch")
async def set_kill_switch(body: dict):
    return kill_switch.set_paused(
        detection=body["detection_paused"] if "detection_paused" in body else None,
        proposals=body["proposals_paused"] if "proposals_paused" in body else None,
    )


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

    # Queued for the privileged worker rather than spawned here. Running
    # `docker compose` needs the host Docker socket, and the whole point of
    # the security split (plan.md §15) is that this web-facing process does
    # not have one -- if it did, an RCE through rendered repo content would
    # reach the host daemon directly. The worker writes the same
    # DEPLOY_LOG_PATH into the shared workspace volume, so /redeploy/status
    # below still reads real progress.
    from app.work_queue import enqueue

    await enqueue("redeploy", {"by": admin.email})

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




@router.post("/eval/run")
async def trigger_eval(admin: User = Depends(require_admin)):
    """Detection-only pass (plan.md §13.4): mechanical detectors against
    controlled worktrees, checked against a fixed answer key. No model
    calls, so no cost -- see app/eval_harness.py's own docstring for why a
    blended score is refused. Runs as a background task since the sandboxed
    categories (ui/backend/accessibility) each do a real npm install."""
    # Queued for the worker: the sandboxed categories each run a real
    # dependency install and a real browser, which is exactly the execution
    # this process no longer does (plan.md §15). The report comes back on the
    # work item itself -- the module-level globals this used to keep it in
    # cannot cross a process boundary.
    from app.work_queue import enqueue

    async with async_session() as db:
        running = (
            await db.execute(
                text("SELECT count(*) FROM work_items WHERE kind = 'run_eval' AND status IN ('queued','running')")
            )
        ).scalar_one()
    if running:
        raise HTTPException(409, "an eval run is already in progress")

    await enqueue("run_eval", {"repo": settings.fixture_repo})
    return {"ok": True, "message": "Eval run queued -- poll GET /api/admin/eval/latest."}


@router.get("/eval/latest")
async def latest_eval():
    async with async_session() as db:
        row = (
            await db.execute(
                text(
                    "SELECT status, result, error FROM work_items WHERE kind = 'run_eval' "
                    "ORDER BY created_at DESC LIMIT 1"
                )
            )
        ).first()
    if row is None:
        return {"running": False, "report": None}
    status, result, error = row
    return {
        "running": status in ("queued", "running"),
        "report": result if not error else {"error": error},
    }


# --- organizations ------------------------------------------------------------
#
# A system admin creates organizations and names each one's first admin; from
# there the org administers itself (app/routers/org.py). Nothing in the product
# created an org before this -- every row was inserted by hand, so a second
# company could not exist.


class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # Optional: derived from the name when absent. Exposed because it becomes
    # the on-disk directory every one of this org's repositories is cloned
    # under, and is not changeable afterwards.
    slug: str = Field(default="", max_length=48)
    # Who administers it. An existing account is added straight away; an
    # address with no account is invited.
    admin_email: str = Field(default="", max_length=254)


@router.get("/orgs")
async def list_orgs(admin: User = Depends(require_admin)):
    return await asyncio.to_thread(orgs.all_orgs)


@router.post("/orgs")
async def create_org(
    body: OrgIn,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        org = await asyncio.to_thread(
            orgs.create_org, name=body.name, slug=body.slug, created_by=admin.id
        )
    except orgs.OrgError as error:
        raise HTTPException(409, str(error)) from error

    admin_email = (body.admin_email or "").strip().lower()
    outcome: dict = {"admin": None, "invite_link": None}

    if admin_email:
        existing = (await db.execute(select(User).where(User.email == admin_email))).scalars().first()
        if existing is not None:
            try:
                await asyncio.to_thread(
                    orgs.add_member,
                    org_id=org["id"],
                    user_id=existing.id,
                    role=OrgRole.ORG_ADMIN,
                    seniority=Seniority.SDE3,
                )
                outcome["admin"] = existing.email
            except orgs.OrgError as error:
                # The org is already created; report the admin problem rather
                # than failing the whole call and leaving an org nobody knows
                # exists.
                outcome["warning"] = str(error)
        else:
            invite = await org_invites.create_invite(
                db,
                org_id=uuid.UUID(org["id"]),
                email=admin_email,
                role=OrgRole.ORG_ADMIN,
                seniority=Seniority.SDE3,
                invited_by=admin.id,
            )
            link = f"{settings.public_base_url}/join?token={sign_org_invite_token(invite.id)}"
            outcome["invite_link"] = link
            if email_client.smtp_configured():
                try:
                    subject, html, text_body = email_client.build_org_invite_email(
                        admin_email.split("@")[0], org["name"], admin.email, link
                    )
                    await asyncio.to_thread(email_client.send_email, admin_email, subject, html, text_body)
                except Exception:
                    logger.exception("could not send the org admin invite for %s", org["id"])

    # An org with no admin can invite nobody and configure nothing, so say so
    # here rather than letting it be discovered later.
    if not admin_email:
        outcome["warning"] = (
            "This organization has no admin yet. Nobody can invite members or change its "
            "routing until one is named."
        )

    return {"ok": True, "org": org, **outcome}


class OrgAdminIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)


@router.post("/orgs/{org_id}/admins")
async def add_org_admin(
    org_id: uuid.UUID,
    body: OrgAdminIn,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Name an admin on an organization that already exists.

    The recovery path for an org created without one. Such an org can invite
    nobody and configure nothing -- its own admin endpoints all require an
    org admin -- so without this it was unreachable except through a database
    console.
    """
    from app.models import Organization

    organization = await db.get(Organization, org_id)
    if organization is None:
        raise HTTPException(404, "that organization does not exist")

    email = body.email.strip().lower()
    existing = (await db.execute(select(User).where(User.email == email))).scalars().first()

    if existing is not None:
        try:
            await asyncio.to_thread(
                orgs.add_member,
                org_id=org_id,
                user_id=existing.id,
                role=OrgRole.ORG_ADMIN,
                seniority=Seniority.SDE3,
            )
        except orgs.OrgError as error:
            raise HTTPException(409, str(error)) from error
        return {"ok": True, "admin": existing.email, "invite_link": None}

    invite = await org_invites.create_invite(
        db, org_id=org_id, email=email, role=OrgRole.ORG_ADMIN,
        seniority=Seniority.SDE3, invited_by=admin.id,
    )
    link = f"{settings.public_base_url}/join?token={sign_org_invite_token(invite.id)}"
    if email_client.smtp_configured():
        try:
            subject, html, text_body = email_client.build_org_invite_email(
                email.split("@")[0], organization.name, admin.email, link
            )
            await asyncio.to_thread(email_client.send_email, email, subject, html, text_body)
        except Exception:
            logger.exception("could not send the org admin invite for %s", org_id)
    return {"ok": True, "admin": None, "invite_link": link}
