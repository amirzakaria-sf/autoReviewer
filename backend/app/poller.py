"""Background poller (plan.md §15): a human or any other tool labeling an
existing GitHub issue `whipguard:fix-me` fires the exact same FixCouncilGraph
entry point a Bug-Council-raised issue does. The graph does not care who or what
decided this is worth fixing, only that a matching issue exists.

Runs as an asyncio background task, not a separate service — hackathon scope.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.config import settings
from app.db import async_session
from app.enums import IssueStatus
from app.integrations import github_client
from app.models import Issue, Repo
from app.work_queue import enqueue

logger = logging.getLogger("whipguard.poller")

POLL_INTERVAL_SECONDS = 30
FIX_ME_LABEL = "whipguard:fix-me"


async def poll_for_externally_filed_bugs() -> None:
    while True:
        try:
            await _poll_once()
        except Exception:
            logger.exception("poll_for_externally_filed_bugs: one cycle failed, will retry next interval")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _poll_once() -> None:
    async with async_session() as db:
        # Every connected repo, not just the fixture one. Scoped to a single
        # repo this loop silently ignored externally-filed bugs on every
        # other repository a user had connected.
        repos = (await db.execute(select(Repo))).scalars().all()
        if not repos or not settings.github_token:
            return

        for repo in repos:
            try:
                open_issues = github_client.list_issues_with_label(repo.github_full_name, FIX_ME_LABEL)
            except Exception:
                logger.exception("could not list %s issues with label %s", repo.github_full_name, FIX_ME_LABEL)
                continue

            for gh_issue in open_issues:
                existing = (
                    await db.execute(
                        select(Issue).where(
                            # Scoped by repo: GitHub issue numbers restart at 1
                            # per repository, so matching on the number alone
                            # made repo B's issue #5 look like an already-seen
                            # copy of repo A's issue #5 and silently dropped it.
                            Issue.repo_id == repo.id,
                            Issue.github_issue_number == gh_issue["number"],
                        )
                    )
                ).scalars().first()

                if existing is not None:
                    continue

                issue = Issue(
                    repo_id=repo.id,
                    category="ui",
                    origin="filed-externally",
                    github_issue_number=gh_issue["number"],
                    title=gh_issue["title"],
                    severity=2,
                    status=IssueStatus.RAISED,
                )
                db.add(issue)
                await db.flush()
                await db.commit()

                logger.info(
                    "picked up externally-filed issue %s#%s -> queueing Fix Council",
                    repo.github_full_name, gh_issue["number"],
                )
                # Queued, not run here: this loop lives in the privileged
                # worker now, but going through the queue keeps one path for
                # every trigger and makes the work visible/retryable.
                await enqueue("fix_council", {"issue_id": str(issue.id)})
