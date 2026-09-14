from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.enums import FIX_STATUS_RENDER, ISSUE_STATUS_RENDER, FixStatus, IssueStatus
from app.graphs.approval_graph import resolve_approval
from app.models import Fix, Issue, OutcomeCheck, Repo
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


@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db)):
    raised = (await db.execute(select(func.count()).select_from(Issue).where(Issue.status == IssueStatus.RAISED))).scalar()
    verified = (await db.execute(select(func.count()).select_from(Fix).where(Fix.status == FixStatus.VERIFIED))).scalar()
    awaiting = (await db.execute(select(func.count()).select_from(Fix).where(Fix.status == FixStatus.AWAITING_APPROVAL))).scalar()
    failed = (
        await db.execute(
            select(func.count()).select_from(Fix).where(
                Fix.status.in_([FixStatus.VERIFICATION_FAILED, FixStatus.OUTCOME_CHECK_FAILED])
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
async def list_repos(db: AsyncSession = Depends(get_db)):
    repos = (await db.execute(select(Repo))).scalars().all()
    return [
        {"id": str(r.id), "github_full_name": r.github_full_name, "default_branch": r.default_branch}
        for r in repos
    ]


@router.get("/issues")
async def list_issues(status: str | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(Issue).order_by(Issue.created_at.desc())
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
async def get_issue(issue_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    issue = await db.get(Issue, issue_id)
    if not issue:
        raise HTTPException(404, "issue not found")
    fixes = (await db.execute(select(Fix).where(Fix.issue_id == issue_id).order_by(Fix.created_at.desc()))).scalars().all()
    fix_dicts = []
    for f in fixes:
        d = _fix_dict(f)
        d["outcome_check"] = await _outcome_check_dict(db, f.id)
        fix_dicts.append(d)
    return {**_issue_dict(issue), "fixes": fix_dicts}


@router.get("/fixes")
async def list_fixes(status: str | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(Fix).order_by(Fix.created_at.desc())
    if status:
        stmt = stmt.where(Fix.status == status)
    fixes = (await db.execute(stmt)).scalars().all()
    return [_fix_dict(f) for f in fixes]


@router.get("/fixes/{fix_id}")
async def get_fix(fix_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    fix = await db.get(Fix, fix_id)
    if not fix:
        raise HTTPException(404, "fix not found")
    return _fix_dict(fix)


@router.post("/fixes/{fix_id}/approve")
async def approve_fix(fix_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await resolve_approval(db, fix_id, approved=True, actor="dashboard-user", surface="dashboard")
    return result


@router.post("/fixes/{fix_id}/reject")
async def reject_fix(fix_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await resolve_approval(db, fix_id, approved=False, actor="dashboard-user", surface="dashboard")
    return result


@router.post("/repos/{repo_id}/scan")
async def scan_repo(repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Manual trigger for the Bug Council — fire-and-forget so several scans (or
    a scan plus in-flight fixes) can run concurrently, never queued one at a
    time (plan.md §15)."""
    from app.graphs.bug_council import run_and_persist

    repo = await db.get(Repo, repo_id)
    if not repo:
        raise HTTPException(404, "repo not found")

    async def _run():
        from app.db import async_session

        async with async_session() as scoped_db:
            fresh_repo = await scoped_db.get(Repo, repo_id)
            await run_and_persist(scoped_db, fresh_repo)

    asyncio.create_task(_run())
    return {"ok": True, "message": "scan started"}


@router.post("/issues/{issue_id}/trigger-fix")
async def trigger_fix(issue_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Fires the Fix Council for ANY issue, regardless of whether WhipGuard's own
    Bug Council raised it (plan.md §15's origin=filed-externally path, exposed
    here as a manual button too)."""
    issue = await db.get(Issue, issue_id)
    if not issue:
        raise HTTPException(404, "issue not found")

    asyncio.create_task(trigger_fix_council(issue_id))
    return {"ok": True, "message": "fix council started"}
