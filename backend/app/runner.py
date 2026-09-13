"""Orchestration entrypoints callable as fire-and-forget asyncio tasks, so
multiple fixes run concurrently by construction (plan.md §15) — nothing here
serializes one fix behind another; each gets its own worktree and container.
"""

from __future__ import annotations

import logging
import uuid

from app.db import async_session
from app.enums import FixStatus, IssueStatus
from app.integrations import github_client
from app.models import Fix, Issue
from app.sandbox.worktree import create_worktree, ensure_mirror

logger = logging.getLogger("whipguard.runner")


async def trigger_fix_council(issue_id: uuid.UUID) -> None:
    from app.graphs.fix_council import build_fix_council_graph
    from app.config import settings

    async with async_session() as db:
        issue = await db.get(Issue, issue_id)
        if issue is None:
            return

        repo_full_name = settings.fixture_repo
        slug = f"issue-{issue.github_issue_number}"

        mirror = ensure_mirror(repo_full_name)
        worktree = create_worktree(mirror, issue.github_issue_number or 0, slug)

        try:
            graph = build_fix_council_graph()
            result = graph.invoke({"worktree_path": str(worktree), "attempt": 1, "prior_rejection": None})

            proposed = result["score"] >= settings.resolution_threshold
            branch_name = f"whipguard/{issue.github_issue_number}-{slug}"

            fix = Fix(
                issue_id=issue.id,
                resolution_score=result.get("score"),
                resolution_rubric={"factors": result.get("rubric", []), "verdict": result.get("verdict")},
                branch_name=branch_name,
                status=FixStatus.AWAITING_APPROVAL if proposed else FixStatus.REJECTED,
            )
            db.add(fix)

            if proposed:
                github_client.push_branch(str(worktree), branch_name)
                pr_number = github_client.create_draft_pr(
                    repo_full_name,
                    branch_name,
                    "main",
                    f"Fix for #{issue.github_issue_number}",
                    f"Closes #{issue.github_issue_number}\n\nAutomated fix. Resolution score: {result.get('score')}/100.\n\n```diff\n{result.get('diff', '')}\n```",
                    ["whipguard:fix-proposed", "whipguard:awaiting-approval"],
                )
                fix.pr_number = pr_number
                issue.status = IssueStatus.FIX_PROPOSED

            await db.commit()
            logger.info("fix council finished for issue %s: score=%s proposed=%s", issue_id, result.get("score"), proposed)
        except Exception:
            logger.exception("fix council run failed for issue %s", issue_id)
