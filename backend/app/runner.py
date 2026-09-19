"""Orchestration entrypoints callable as fire-and-forget asyncio tasks, so
multiple fixes run concurrently by construction (plan.md §15) — nothing here
serializes one fix behind another; each gets its own worktree and container.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from app.db import async_session
from app.enums import FIX_STATUS_RENDER, FixStatus, IssueStatus
from app.integrations import email_client, slack_client
from app.models import Fix, Issue, Repo
from app.routers.ws import emit_event
from app.sandbox.worktree import create_worktree, ensure_mirror

logger = logging.getLogger("whipguard.runner")


async def trigger_fix_council(
    issue_id: uuid.UUID,
    *,
    feedback: str = "",
    attempt: int = 1,
    supersedes: uuid.UUID | None = None,
) -> None:
    """Propose a fix for `issue_id`, or re-propose one a human sent back.

    `feedback` is every instruction the reviewer has given on this issue so
    far (app/fix_review.py compiles it), threaded into the council as
    `prior_rejection` -- the field the patch prompt already reads and which,
    until the review thread existed, nothing ever populated.

    NOTHING here touches GitHub. An attempt is generated, verified in the
    sandbox and scored locally; the branch is pushed and the PR opened only
    once a human approves (app/graphs/approval_graph.py). A proposal nobody
    accepts should leave no trace on the repository.
    """
    from app import app_settings
    from app.categories import resolution_threshold_for
    from app.config import settings
    from app.fix_review import open_review, proposal_summary
    from app.graphs.fix_council import build_fix_council_graph

    async with async_session() as db:
        issue = await db.get(Issue, issue_id)
        if issue is None:
            return

        repo = await db.get(Repo, issue.repo_id)
        if repo and repo.proposals_paused:
            logger.info("fix proposals paused for repo %s (kill switch) -- skipping issue %s", repo.id, issue_id)
            return

        category = issue.category
        threshold = resolution_threshold_for(repo, category) if repo else settings.resolution_threshold
        # repo.github_full_name, NOT settings.fixture_repo. This clones and
        # patches whichever repo the ISSUE belongs to; hardcoding the fixture
        # here meant a fix for any other connected repo was generated against
        # the fixture repo's source and pushed to the fixture repo's branch.
        repo_full_name = repo.github_full_name if repo else settings.fixture_repo
        slug = f"issue-{issue.github_issue_number}"

        evidence_text = (issue.evidence or {}).get("assertion_text", "")
        bug_description = f"{issue.title}. Evidence: {evidence_text[:500]}" if evidence_text else issue.title

        mirror = ensure_mirror(repo_full_name)
        worktree = create_worktree(mirror, issue.github_issue_number or 0, slug)

        try:
            emit_event({"type": "run", "kind": "fix_council", "status": "started", "message": f"Fix Council ({category}) working on: {issue.title}"})
            graph = build_fix_council_graph()
            # Blocking sync I/O throughout (Azure calls, subprocess, sandbox
            # runs) -- off the event loop thread, so several of these firing
            # from the poller/dashboard genuinely run at the same time instead
            # of serializing behind one blocked loop.
            result = await asyncio.to_thread(
                graph.invoke,
                {
                    "worktree_path": str(worktree),
                    "category": category,
                    "repo_full_name": repo_full_name,
                    "repo_id": repo.id if repo else None,
                    "bug_description": bug_description,
                    "resolution_threshold": threshold,
                    "attempt": attempt,
                    "prior_rejection": feedback or None,
                },
            )

            proposed = result["score"] >= threshold
            emit_event({
                "type": "run", "kind": "fix_council", "status": "done",
                "message": f"Fix proposed (score {result['score']})" if proposed else "No fix proposed (below threshold)",
            })
            branch_name = f"whipguard/{issue.github_issue_number}-{slug}"

            fix = Fix(
                issue_id=issue.id,
                resolution_score=result.get("score"),
                resolution_rubric={"factors": result.get("rubric", []), "verdict": result.get("verdict")},
                branch_name=branch_name,
                # Persisted, not left in the worktree: with no PR to read the
                # diff from until approval, this column IS the review surface.
                diff=result.get("diff") or "",
                attempt=attempt,
                status=FixStatus.AWAITING_APPROVAL if proposed else FixStatus.REJECTED,
            )
            db.add(fix)
            await db.flush()

            if supersedes is not None:
                superseded = await db.get(Fix, supersedes)
                if superseded is not None:
                    superseded.status = FixStatus.SUPERSEDED
                    superseded.superseded_by_id = fix.id

            if proposed:
                issue.status = IssueStatus.FIX_PROPOSED
                review = await open_review(
                    db,
                    issue.id,
                    fix,
                    summary=proposal_summary(fix, result.get("verdict") or "", result.get("rubric")),
                )
                review_url = f"{settings.public_base_url}/issues/{issue.id}"
                channel_id = app_settings.slack_for_repo(issue.repo_id)[1]

                if app_settings.slack_bot_token() and channel_id:
                    try:
                        blocks = slack_client.build_fix_proposed_blocks(
                            fix.id, issue.title, result.get("score", 0), review_url,
                            FIX_STATUS_RENDER[FixStatus.AWAITING_APPROVAL]["dashboard_badge"],
                        )
                        ts = slack_client.post_message(channel_id, blocks, text=f"WhipGuard fix proposed: {issue.title}")
                        fix.slack_message_ts = ts
                    except Exception:
                        logger.exception("Slack fix-proposed post failed for fix %s", fix.id)

                if email_client.smtp_configured() and settings.notify_email:
                    try:
                        subject, html, text = email_client.build_fix_proposed_email(
                            issue.title, category, result.get("score", 0), review_url, str(fix.id)
                        )
                        await asyncio.to_thread(email_client.send_email, settings.notify_email, subject, html, text)
                    except Exception:
                        logger.exception("fix-proposed email failed for fix %s", fix.id)

            await db.commit()
            logger.info("fix council finished for issue %s: score=%s proposed=%s", issue_id, result.get("score"), proposed)
        except Exception:
            logger.exception("fix council run failed for issue %s", issue_id)
