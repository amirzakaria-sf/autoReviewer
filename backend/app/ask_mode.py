"""Ask Mode learning from clarification history (plan.md §10.5).

Never writes the rate into a prompt. It only nudges Repo.ask_mode between
`balanced` and `verbose` after enough real HumanInputRequest rows exist.
Autonomous is a human choice — this loop will not select it.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.db import async_session
from app.models import AuditLog, HumanInputRequest, Issue, Repo

logger = logging.getLogger("whipguard.ask_mode")

MIN_ISSUES = 5
HIGH_CLARIFICATION_RATE = 0.3
LOW_CLARIFICATION_RATE = 0.05


async def learn_ask_mode_pass() -> None:
    async with async_session() as db:
        repos = (await db.execute(select(Repo))).scalars().all()
        for repo in repos:
            await _learn_one(db, repo)
        await db.commit()


async def _learn_one(db, repo: Repo) -> None:
    issue_count = (
        await db.execute(select(func.count()).select_from(Issue).where(Issue.repo_id == repo.id))
    ).scalar_one()
    if issue_count < MIN_ISSUES:
        return

    clarifications = (
        await db.execute(
            select(func.count())
            .select_from(HumanInputRequest)
            .join(Issue, Issue.id == HumanInputRequest.issue_id)
            .where(Issue.repo_id == repo.id)
        )
    ).scalar_one()
    rate = clarifications / issue_count
    current = repo.ask_mode or "balanced"
    new_mode = current

    if current == "balanced" and rate >= HIGH_CLARIFICATION_RATE:
        new_mode = "verbose"
    elif current == "verbose" and rate <= LOW_CLARIFICATION_RATE:
        new_mode = "balanced"

    if new_mode == current:
        return

    repo.ask_mode = new_mode
    db.add(
        AuditLog(
            actor_surface="ask_mode_learning",
            action="ask_mode_adjusted",
            target_type="repo",
            target_id=str(repo.id),
            metadata_json={
                "previous": current,
                "new": new_mode,
                "clarification_rate": round(rate, 2),
                "issue_count": issue_count,
                "clarifications": clarifications,
            },
        )
    )
    logger.info(
        "ask_mode: repo=%s %s -> %s (clarification_rate=%.2f, n=%d)",
        repo.id, current, new_mode, rate, issue_count,
    )
