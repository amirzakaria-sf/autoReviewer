from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.enums import FIX_STATUS_RENDER, ISSUE_STATUS_RENDER, FixStatus, IssueStatus
from app.graphs.approval_graph import resolve_approval
from app.deps import current_user, visible_repo_ids
from app.models import Fix, Issue, OutcomeCheck, Repo, User
from app.runner import trigger_fix_council

router = APIRouter(prefix="/api")


def _issue_dict(issue: Issue) -> dict:
    render = ISSUE_STATUS_RENDER[issue.status]
    return {
        "id": str(issue.id),
        "repo_id": str(issue.repo_id),
        "category": issue.category,
        "origin": issue.origin,
        "github_issue_number": issue.github_issue_number,
        "title": issue.title,
        "severity": issue.severity,
        "assurance_score": issue.assurance_score,
        "assurance_rubric": issue.assurance_rubric,
        "assignee_user_id": str(issue.assignee_user_id) if issue.assignee_user_id else None,
        # Stored rather than recomputed: routing rules change, and "why did
        # this reach me" must stay answerable from the record.
        "assignment_reasoning": issue.assignment_reasoning or [],
        "evidence": issue.evidence,
        "status": issue.status.value,
        "badge": render["dashboard_badge"],
        "color": render["dashboard_color"],
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
    }


def _fix_dict(fix: Fix) -> dict:
    render = FIX_STATUS_RENDER[fix.status]
    return {
        "id": str(fix.id),
        "issue_id": str(fix.issue_id),
        "resolution_score": fix.resolution_score,
        "resolution_rubric": fix.resolution_rubric,
        "branch_name": fix.branch_name,
        "pr_number": fix.pr_number,
        "preview_url": fix.preview_url,
        "status": fix.status.value,
        "badge": render["dashboard_badge"],
        "color": render["dashboard_color"],
        "approved_by": fix.approved_by,
        "approved_via": fix.approved_via,
        "created_at": fix.created_at.isoformat() if fix.created_at else None,
    }


def _guard(repo_id, repo_ids: list[uuid.UUID]) -> None:
    """404, never 403, for a repository outside the caller's organization.

    403 confirms the id names something real, which tells the caller a
    repository they may not see exists. A tenancy boundary should not be a
    lookup service for the other side of it.
    """
    if repo_id not in repo_ids:
        raise HTTPException(404, "repo not found")


@router.get("/overview")
async def overview(
    db: AsyncSession = Depends(get_db),
    repo_ids: list[uuid.UUID] = Depends(visible_repo_ids),
):
    """Counts for the caller's own organization.

    Scoped like every other product read (app/deps.py's visible_repo_ids).
    Unscoped, a member of a brand-new org with no repositories saw twenty
    findings belonging to somebody else.
    """
    if not repo_ids:
        return {"raised_by_ai": 0, "resolved_and_verified": 0, "awaiting_approval": 0, "failed": 0}

    # Fixes reach their org through their issue, which reaches it through its
    # repo -- the single source of truth for tenancy (see Organization).
    mine = select(Issue.id).where(Issue.repo_id.in_(repo_ids))

    # "Raised by AI" is cumulative -- every issue that ever cleared the
    # assurance threshold, not just ones currently stuck in the bare RAISED
    # state before a fix got proposed (which undercounted the moment a fix
    # moved it on to FIX_PROPOSED or CLOSED -- the actual raised->approved->
    # resolved flow this counter is supposed to represent end to end).
    raised = (
        await db.execute(
            select(func.count()).select_from(Issue).where(
                Issue.repo_id.in_(repo_ids),
                Issue.status.in_([IssueStatus.RAISED, IssueStatus.FIX_PROPOSED, IssueStatus.CLOSED]),
            )
        )
    ).scalar()
    # MERGED counts as resolved too -- a human merging the PR directly on
    # GitHub is a real resolution (plan.md's forbidden-action rule is that
    # WHIPGUARD never merges, not that a human can't), and this is the exact
    # count that read "0" while a merged fix sat unrecognized before the
    # pull_request webhook synced it back.
    verified = (
        await db.execute(
            select(func.count()).select_from(Fix).where(
                Fix.issue_id.in_(mine), Fix.status.in_([FixStatus.VERIFIED, FixStatus.MERGED])
            )
        )
    ).scalar()
    awaiting = (
        await db.execute(
            select(func.count()).select_from(Fix).where(
                Fix.issue_id.in_(mine), Fix.status == FixStatus.AWAITING_APPROVAL
            )
        )
    ).scalar()
    failed = (
        await db.execute(
            select(func.count()).select_from(Fix).where(
                Fix.issue_id.in_(mine),
                Fix.status.in_([FixStatus.VERIFICATION_FAILED, FixStatus.OUTCOME_CHECK_FAILED]),
            )
        )
    ).scalar()
    return {
        "raised_by_ai": raised or 0,
        "resolved_and_verified": verified or 0,
        "awaiting_approval": awaiting or 0,
        "failed": failed or 0,
    }


@router.get("/repos")
async def list_repos(
    db: AsyncSession = Depends(get_db),
    repo_ids: list[uuid.UUID] = Depends(visible_repo_ids),
):
    if not repo_ids:
        return []
    repos = (await db.execute(select(Repo).where(Repo.id.in_(repo_ids)))).scalars().all()
    return [
        {
            "id": str(r.id),
            "github_full_name": r.github_full_name,
            "default_branch": r.default_branch,
            "detection_paused": r.detection_paused,
            "proposals_paused": r.proposals_paused,
        }
        for r in repos
    ]


@router.get("/repos/{repo_id}/settings")
async def get_repo_settings(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    repo_ids: list[uuid.UUID] = Depends(visible_repo_ids),
):
    from app.categories import CATEGORY_REGISTRY

    _guard(repo_id, repo_ids)
    repo = await db.get(Repo, repo_id)
    if not repo:
        raise HTTPException(404, "repo not found")

    categories = []
    for key, config in CATEGORY_REGISTRY.items():
        toggle = (repo.enabled_categories or {}).get(key, {})
        thresholds = (repo.thresholds or {}).get(key, {})
        categories.append({
            "key": key,
            "label": config.label,
            "issues_enabled": toggle.get("issues", True),
            "fixes_enabled": toggle.get("fixes", True),
            "assurance_threshold": thresholds.get("assurance", config.assurance_threshold),
            "resolution_threshold": thresholds.get("resolution", config.resolution_threshold),
            "default_assurance_threshold": config.assurance_threshold,
            "default_resolution_threshold": config.resolution_threshold,
        })

    return {
        "id": str(repo.id),
        "github_full_name": repo.github_full_name,
        "ask_mode": repo.ask_mode,
        "detection_paused": repo.detection_paused,
        "proposals_paused": repo.proposals_paused,
        "cloudflare_pages_project": repo.cloudflare_pages_project or "",
        "categories": categories,
    }


@router.patch("/repos/{repo_id}/settings")
async def update_repo_settings(
    repo_id: uuid.UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
    repo_ids: list[uuid.UUID] = Depends(visible_repo_ids),
):
    from app.categories import CATEGORY_REGISTRY

    _guard(repo_id, repo_ids)
    repo = await db.get(Repo, repo_id)
    if not repo:
        raise HTTPException(404, "repo not found")

    if "ask_mode" in body:
        if body["ask_mode"] not in ("autonomous", "balanced", "verbose"):
            raise HTTPException(400, "ask_mode must be autonomous, balanced, or verbose")
        repo.ask_mode = body["ask_mode"]

    if "detection_paused" in body:
        repo.detection_paused = bool(body["detection_paused"])
    if "proposals_paused" in body:
        repo.proposals_paused = bool(body["proposals_paused"])
    if "cloudflare_pages_project" in body:
        value = (body["cloudflare_pages_project"] or "").strip()
        repo.cloudflare_pages_project = value or None

    # Per-category patches: {"ui": {"issues_enabled": false, "resolution_threshold": 85}, ...}
    # Merged key-by-key into the existing JSONB rather than replacing it
    # wholesale, so patching one category never silently resets the others.
    categories_patch = body.get("categories") or {}
    enabled = dict(repo.enabled_categories or {})
    thresholds = dict(repo.thresholds or {})
    for key, patch in categories_patch.items():
        if key not in CATEGORY_REGISTRY:
            raise HTTPException(400, f"unknown category: {key}")
        toggle = dict(enabled.get(key, {}))
        if "issues_enabled" in patch:
            toggle["issues"] = bool(patch["issues_enabled"])
        if "fixes_enabled" in patch:
            toggle["fixes"] = bool(patch["fixes_enabled"])
        if toggle:
            enabled[key] = toggle

        thresh = dict(thresholds.get(key, {}))
        if "assurance_threshold" in patch:
            value = int(patch["assurance_threshold"])
            if not (0 <= value <= 100):
                raise HTTPException(400, "assurance_threshold must be 0-100")
            thresh["assurance"] = value
        if "resolution_threshold" in patch:
            value = int(patch["resolution_threshold"])
            if not (0 <= value <= 100):
                raise HTTPException(400, "resolution_threshold must be 0-100")
            thresh["resolution"] = value
        if thresh:
            thresholds[key] = thresh

    if categories_patch:
        repo.enabled_categories = enabled
        repo.thresholds = thresholds

    await db.commit()
    return {"ok": True}


@router.get("/issues")
async def list_issues(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    repo_ids: list[uuid.UUID] = Depends(visible_repo_ids),
):
    if not repo_ids:
        return []
    stmt = select(Issue).where(Issue.repo_id.in_(repo_ids)).order_by(Issue.created_at.desc())
    if status:
        stmt = stmt.where(Issue.status == status)
    issues = (await db.execute(stmt)).scalars().all()
    return [_issue_dict(i) for i in issues]


async def _outcome_check_dict(db: AsyncSession, fix_id) -> dict | None:
    check = (
        await db.execute(
            select(OutcomeCheck).where(OutcomeCheck.fix_id == fix_id).order_by(OutcomeCheck.checked_at.desc())
        )
    ).scalars().first()
    if not check:
        return None
    return {
        "agreed": check.agreed,
        "github_state": check.github_state,
        "cloudflare_state": check.cloudflare_state,
        "slack_state": check.slack_state,
        "dashboard_state": check.dashboard_state,
        "mismatch_detail": check.mismatch_detail,
        "checked_at": check.checked_at.isoformat() if check.checked_at else None,
    }


@router.get("/issues/{issue_id}")
async def get_issue(
    issue_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    repo_ids: list[uuid.UUID] = Depends(visible_repo_ids),
):
    issue = await db.get(Issue, issue_id)
    if not issue:
        raise HTTPException(404, "issue not found")
    # An issue reaches its org through its repo. Reported as a missing issue
    # rather than a forbidden one, for the reason _guard explains.
    if issue.repo_id not in repo_ids:
        raise HTTPException(404, "issue not found")
    fixes = (await db.execute(select(Fix).where(Fix.issue_id == issue_id).order_by(Fix.created_at.desc()))).scalars().all()
    fix_dicts = []
    for f in fixes:
        d = _fix_dict(f)
        d["outcome_check"] = await _outcome_check_dict(db, f.id)
        fix_dicts.append(d)
    # The repo this issue belongs to, so the UI can build GitHub links from
    # real data. The detail page previously hardcoded the fixture repo's slug
    # into every issue/PR URL, which silently pointed at the wrong repository
    # for every repo but that one -- the same class of bug already fixed
    # three times on the backend.
    repo = await db.get(Repo, issue.repo_id)

    # The thresholds this issue was actually judged against, so the UI can
    # show where the bar sits rather than printing a bare number the reader
    # has to compare by hand. Per-repo overrides win over the registry's
    # defaults, exactly as the councils resolve them.
    from app.categories import CATEGORY_REGISTRY

    config = CATEGORY_REGISTRY.get(issue.category)
    overrides = ((repo.thresholds if repo else None) or {}).get(issue.category, {})
    return {
        **_issue_dict(issue),
        "repo_full_name": repo.github_full_name if repo else None,
        "assurance_threshold": overrides.get("assurance", config.assurance_threshold if config else None),
        "resolution_threshold": overrides.get("resolution", config.resolution_threshold if config else None),
        "fixes": fix_dicts,
    }


async def _fix_in_scope(db, fix_id, repo_ids: list[uuid.UUID]) -> Fix:
    """A fix the caller's organization owns, or a 404.

    Guards the DECISION endpoints as well as the reads: approving or
    rejecting another org's fix would push a branch and deploy code that has
    nothing to do with the person clicking.
    """
    fix = await db.get(Fix, fix_id)
    if fix is None:
        raise HTTPException(404, "fix not found")
    issue = await db.get(Issue, fix.issue_id)
    if issue is None or issue.repo_id not in repo_ids:
        raise HTTPException(404, "fix not found")
    return fix


@router.get("/fixes")
async def list_fixes(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    repo_ids: list[uuid.UUID] = Depends(visible_repo_ids),
):
    if not repo_ids:
        return []
    mine = select(Issue.id).where(Issue.repo_id.in_(repo_ids))
    stmt = select(Fix).where(Fix.issue_id.in_(mine)).order_by(Fix.created_at.desc())
    if status:
        stmt = stmt.where(Fix.status == status)
    fixes = (await db.execute(stmt)).scalars().all()
    return [_fix_dict(f) for f in fixes]


@router.get("/fixes/{fix_id}")
async def get_fix(
    fix_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    repo_ids: list[uuid.UUID] = Depends(visible_repo_ids),
):
    return _fix_dict(await _fix_in_scope(db, fix_id, repo_ids))


class DecisionIn(BaseModel):
    """Why. Optional on an approval, and the whole point of a rejection --
    it is what a later attempt on this issue reads back before proposing
    anything (app/context_broker.py)."""

    note: str = Field(default="", max_length=4000)


@router.post("/fixes/{fix_id}/approve")
async def approve_fix(
    fix_id: uuid.UUID,
    body: DecisionIn | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
    repo_ids: list[uuid.UUID] = Depends(visible_repo_ids),
):
    await _fix_in_scope(db, fix_id, repo_ids)
    return await resolve_approval(
        db, fix_id, approved=True, actor=user.email, surface="dashboard",
        note=(body.note if body else ""),
    )


@router.post("/fixes/{fix_id}/reject")
async def reject_fix(
    fix_id: uuid.UUID,
    body: DecisionIn | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
    repo_ids: list[uuid.UUID] = Depends(visible_repo_ids),
):
    await _fix_in_scope(db, fix_id, repo_ids)
    return await resolve_approval(
        db, fix_id, approved=False, actor=user.email, surface="dashboard",
        note=(body.note if body else ""),
    )


@router.post("/repos/{repo_id}/scan")
async def scan_repo(
    repo_id: uuid.UUID,
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
    repo_ids: list[uuid.UUID] = Depends(visible_repo_ids),
):
    """Manual trigger for the Bug Council — fans out one independent task per
    enabled category (plan.md §10.1's RepoWatchGraph fan-out), fire-and-forget
    so several categories (and several repos) run concurrently, never queued
    one at a time (plan.md §15). Pass `category` to scan just one."""
    from app.categories import enabled_categories_for
    from app.graphs.bug_council import run_and_persist

    # Scanning clones and executes repository code. Doing that for another
    # organization's repo, on their bill, is not a read the boundary can be
    # lax about.
    _guard(repo_id, repo_ids)

    repo = await db.get(Repo, repo_id)
    if not repo:
        raise HTTPException(404, "repo not found")
    if repo.detection_paused:
        raise HTTPException(409, "detection is paused for this repo (kill switch)")
    from app import kill_switch
    if kill_switch.detection_paused():
        raise HTTPException(409, "detection is paused globally (kill switch)")

    categories = [category] if category else enabled_categories_for(repo)

    # Enqueued, not executed here. A detector runs arbitrary repository code
    # in a sandbox, so it belongs to the privileged worker (app/worker.py);
    # this process only records that somebody asked.
    from app.work_queue import enqueue

    await enqueue("scan_repo", {"repo_id": str(repo_id), "categories": categories})
    return {"ok": True, "message": f"scan queued for categories: {', '.join(categories)}"}


@router.post("/issues/{issue_id}/trigger-fix")
async def trigger_fix(
    issue_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    repo_ids: list[uuid.UUID] = Depends(visible_repo_ids),
):
    """Fires the Fix Council for ANY issue, regardless of whether WhipGuard's own
    Bug Council raised it (plan.md §15's origin=filed-externally path, exposed
    here as a manual button too)."""
    issue = await db.get(Issue, issue_id)
    if not issue or issue.repo_id not in repo_ids:
        raise HTTPException(404, "issue not found")

    from app.work_queue import enqueue

    await enqueue("fix_council", {"issue_id": str(issue_id)})
    return {"ok": True, "message": "fix council queued"}
